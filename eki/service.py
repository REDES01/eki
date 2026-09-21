# SPDX-License-Identifier: Apache-2.0
"""The engine, over HTTP, for the Mac app and the CLI.

Localhost only, by default — this thing can start subprocesses that edit
repositories, so it has no business listening on the network.

One verb does work: POST /api/ask. It returns a run id immediately, and the
run is then watched on /api/runs/{id}/stream — by the window that asked, by
another window, by `eki watch` in a terminal, or by nobody at all. The
request that started a run has nothing to do with whether it finishes.

Streaming is server-sent events: one direction is all a run ever needs, it
reconnects on its own, and `curl` can read it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import uvicorn
from fastapi import Request, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import catalog
from . import codex_host
from . import config as config_mod
from . import providers as providers_mod
from . import deploy as deploy_mod
from . import migrate
from . import secrets
from . import bench
from . import gateway
from . import settings as settings_mod
from .adapters import base as adapters
from . import policy as policy_mod
from .engine import Engine
from .quota import claude_bridge, claude_probe
from .quota import pace as quota_pace
from .runs import TERMINAL, unseen

log = logging.getLogger("eki")
STATE: Dict[str, Any] = {}


def engine() -> Engine:
    return STATE["engine"]


def sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    eng: Engine = STATE["engine"]
    eng.quota.start()

    async def reap() -> None:
        # unload local models eki started once they've sat unused a while
        while True:
            await asyncio.sleep(60)
            try:
                stopped = await eng.models.reap_idle()
                if stopped:
                    log.info("unloaded idle: %s", ", ".join(stopped))
                closed = await eng.reap_live()
                if closed:
                    log.info("closed %d idle Claude Code session(s)", closed)
            except Exception:                       # noqa: BLE001
                log.exception("idle reaper")

    async def keep_claude_fresh() -> None:
        """While the app is open, keep the Claude reading from going stale.

        Only with the setting on, only when someone has actually looked at
        usage in the last few minutes, and only when the reading is older
        than the interval — an idle Mac spends nothing.
        """
        while True:
            await asyncio.sleep(60)
            try:
                conf = settings_mod.load()
                if not conf["claude_probe"] or not claude_probe.trusted():
                    continue
                if time.time() - STATE.get("usage_seen", 0) > 300:
                    continue
                reading = eng.quota.latest.get("claude")
                age = reading.age_seconds if reading else None
                if age is not None and age < float(conf["claude_probe_minutes"]) * 60:
                    continue
                await claude_probe.refresh(_claude_binary())
                await eng.quota.refresh(force=True)
                log.info("refreshed Claude usage")
            except claude_probe.NotTrusted:
                continue
            except Exception as e:                  # noqa: BLE001
                log.info("claude probe: %s", e)

    try:
        noted = eng.note_interruptions()
        if noted:
            log.info("noted %d interrupted run(s)", noted)
    except Exception:                               # noqa: BLE001
        log.exception("interruptions")

    async def name_old_threads() -> None:
        await asyncio.sleep(15)                     # let the local servers settle
        try:
            await eng.discover_models()
        except Exception:                           # noqa: BLE001
            log.exception("model discovery")
        await asyncio.sleep(5)
        try:
            named = await eng.backfill_titles()
            if named:
                log.info("named %d older threads", named)
        except Exception:                           # noqa: BLE001
            log.exception("titles")

    async def keep_time() -> None:
        """Fire schedules whose time has come; a time missed while the Mac
        slept fires once on waking (within six hours of it)."""
        await asyncio.sleep(20)
        while True:
            try:
                fired = await eng.fire_due()
                if fired:
                    log.info("scheduled: started %s", ", ".join(fired))
            except Exception:                       # noqa: BLE001
                log.exception("schedules")
            await asyncio.sleep(30)

    async def measure_on_its_own() -> None:
        """Every so often, when nothing else is running, measure one
        provider that has no solid numbers yet (see Engine.auto_measure_due)."""
        await asyncio.sleep(120)
        while True:
            try:
                rid = await eng.auto_measure_once()
                if rid:
                    log.info("measuring on my own: run %s", rid)
            except Exception:                       # noqa: BLE001
                log.exception("auto measure")
            await asyncio.sleep(600)

    reaper = asyncio.create_task(reap())
    fresh = asyncio.create_task(keep_claude_fresh())
    naming = asyncio.create_task(name_old_threads())
    auto = asyncio.create_task(measure_on_its_own())
    clock = asyncio.create_task(keep_time())
    yield
    clock.cancel()
    auto.cancel()
    naming.cancel()
    fresh.cancel()
    reaper.cancel()
    await eng.quota.stop()
    await eng.runner.stop()
    await eng.close()


app = FastAPI(title="eki", lifespan=lifespan)


class AskBody(BaseModel):
    prompt: str
    conversation: str = ""
    backend: str = ""
    repo: str = ""
    images: bool = False


class PolicyBody(BaseModel):
    disabled: List[str] = []
    tiers: Dict[str, int] = {}
    order: List[str] = []
    quota_ceiling: Optional[float] = None


# ---- work -----------------------------------------------------------------

@app.post("/api/ask")
async def ask(body: AskBody) -> Any:
    if not body.prompt.strip():
        raise HTTPException(400, "empty prompt")
    return await engine().ask(body.prompt, conversation=body.conversation,
                              backend_key=body.backend, repo=body.repo,
                              images=body.images)


@app.get("/api/runs")
def runs(limit: int = 40) -> Any:
    return engine().runs.recent(limit)


@app.get("/api/runs/{rid}")
def run(rid: str) -> Any:
    found = engine().runs.get(rid)
    if not found:
        raise HTTPException(404, "no such run")
    return found


@app.post("/api/runs/{rid}/cancel")
def cancel_run(rid: str) -> Any:
    ok = engine().runner.cancel(rid)
    return JSONResponse({"cancelled": ok}, status_code=200 if ok else 409)


@app.post("/api/runs/{rid}/retry")
async def retry_run(rid: str) -> Any:
    started = await engine().retry(rid)
    if not started:
        raise HTTPException(409, "only a failed, cancelled or interrupted run can be retried")
    return started


@app.get("/api/runs/{rid}/diff")
def run_diff(rid: str) -> Any:
    if not engine().runs.get(rid):
        raise HTTPException(404, "no such run")
    return {"id": rid, "diff": engine().diff(rid)}


@app.post("/api/runs/{rid}/resume")
async def run_resume(rid: str) -> Any:
    """Carry on after an interruption (see Engine.resume)."""
    got = await engine().resume(rid)
    if not got:
        raise HTTPException(409, "that run isn't one that can be resumed")
    return got


class AnswerBody(BaseModel):
    request_id: str
    response: Dict[str, Any]          # {"behavior": "allow", "updatedInput": …} or a deny


@app.post("/api/runs/{rid}/answer")
async def run_answer(rid: str, body: AnswerBody) -> Any:
    """Your answer to a question Claude Code asked, or to a permission prompt."""
    eng = engine()
    if not eng.runs.get(rid):
        raise HTTPException(404, "no such run")
    if not await eng.answer(rid, body.request_id, body.response):
        raise HTTPException(409, "nothing is waiting for that answer")
    event = {"event": "answered", "request_id": body.request_id}
    eng.runner.activity.setdefault(rid, []).append(event)
    eng.runner._publish(rid, event)
    return {"ok": True}


@app.get("/api/conversations/{cid}/commands")
async def conversation_commands(cid: str, cwd: str = "", backend: str = "") -> Any:
    """The slash commands the program in this thread offers."""
    return {"commands": await engine().commands_for(cid, cwd, backend)}


@app.get("/api/commands")
async def folder_commands(cwd: str = "", backend: str = "") -> Any:
    """The same, for a thread that hasn't started: a session is opened ahead."""
    return {"commands": await engine().commands_for("", cwd, backend)}


@app.get("/api/runs/{rid}/stream")
async def run_stream(rid: str) -> StreamingResponse:
    eng = engine()
    if not eng.runs.get(rid):
        raise HTTPException(404, "no such run")

    async def events() -> AsyncIterator[str]:
        # Subscribe BEFORE reading the log. The other order drops every chunk
        # that lands between the read and the subscribe — invisible for a
        # long job, but exactly what happens when you reopen a window
        # mid-answer.
        queue = eng.runner.subscribe(rid)
        try:
            snap = eng.runs.get(rid) or {}
            seen = len(snap.get("output") or "")
            if snap.get("backend"):
                yield sse({"event": "route", "backend": snap["backend"],
                           "reason": snap.get("reason") or ""})
            if seen:
                yield sse({"event": "output", "text": snap["output"], "end": seen})
            for event in list(eng.runner.activity.get(rid) or []):
                yield sse(event)              # tool lines, and a prompt still open
            if snap.get("state") in TERMINAL:
                if snap.get("error"):
                    yield sse({"event": "error", "message": snap["error"]})
                yield sse({"event": "state", "state": snap["state"]})
                return
            yield sse({"event": "state", "state": snap.get("state", "queued")})

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.get("event") == "output":
                    # trim whatever the stored log already delivered
                    end = int(event.get("end", 0))
                    text = unseen(seen, event.get("text", ""), end)
                    if text:
                        seen = end
                        yield sse({"event": "output", "text": text, "end": end})
                    continue
                yield sse(event)
                if event.get("event") == "state" and event.get("state") in TERMINAL:
                    return
        finally:
            eng.runner.unsubscribe(rid, queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


# ---- reading --------------------------------------------------------------

class ConversationPatch(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None


@app.patch("/api/conversations/{cid}")
def patch_conversation(cid: str, body: ConversationPatch) -> Any:
    if not engine().store.set_conversation(cid, title=body.title, pinned=body.pinned,
                                           archived=body.archived):
        raise HTTPException(404, "no such conversation")
    return {"ok": True}


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str) -> Any:
    eng = engine()
    if eng.runs.active(cid):
        raise HTTPException(409, "a run is still working in it — stop that first")
    if not eng.store.delete_conversation(cid):
        raise HTTPException(404, "no such conversation")
    return {"deleted": cid}


@app.get("/api/conversations")
def conversations(limit: int = 30, q: str = "", archived: bool = False) -> Any:
    eng = engine()
    rows = eng.store.search(q, limit) if q.strip() else eng.store.conversations(limit, archived)
    live = {r["conversation_id"] for r in eng.runs.live()}
    out = []
    for r in rows:
        row = dict(r)
        row["live"] = row["id"] in live       # the sidebar shows what's working
        out.append(row)
    return out


@app.get("/api/artifacts")
def artifacts(limit: int = 400) -> Any:
    """Every answer that may hold a page, a drawing, a diagram or a picture,
    across all threads — the app's gallery reads them the way the chat does."""
    return engine().store.made(limit)


@app.get("/api/conversations/{cid}")
def conversation(cid: str) -> Any:
    view = engine().conversation(cid)
    if not view["turns"]:
        raise HTTPException(404, "no such conversation")
    return view


@app.get("/api/conversations/{cid}/cost")
def conversation_cost(cid: str) -> Any:
    eng = engine()
    report = eng.store.cost(cid)
    for key, entry in report["by_backend"].items():
        backend = eng.get(key)
        entry["tier"] = backend.info.cost.tier if backend else None
        entry["note"] = backend.info.cost.note if backend else ""
    return report


@app.get("/api/backends")
async def backends() -> Any:
    return await engine().describe()


# ---- providers ------------------------------------------------------------

class ProviderBody(BaseModel):
    template: str = ""
    kind: str = ""
    key: str = ""
    label: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    options: Dict[str, Any] = {}


class ProviderPatch(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None
    tier: Optional[int] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    runtime: Optional[Dict[str, Any]] = None


def _provider_view(p: providers_mod.Provider, status: Dict[str, Any]) -> Dict[str, Any]:
    out = p.to_json()
    out["has_key"] = bool(p.options.get("secret")) and bool(secrets.get(p.key))
    out.update(status)
    return out


def _draft(body: ProviderBody) -> providers_mod.Provider:
    """A provider record from what the Add sheet sent, not yet saved."""
    t = catalog.template(body.template) if body.template else None
    if t is None and not body.kind:
        raise HTTPException(400, "pick a template or a kind")
    kind = t["kind"] if t else body.kind
    if kind not in adapters.kinds():
        raise HTTPException(400, f"unknown kind {kind!r}")
    options = dict((t or {}).get("options", {}))
    options.update(body.options)
    if body.base_url:
        options["base_url"] = body.base_url
    if body.model:
        options["model"] = body.model
    if t and t["needs"] == "key":
        options["secret"] = True
    key = body.key or (t["id"] if t else kind)
    return providers_mod.Provider(
        key=key, kind=kind, label=body.label or (t["title"] if t else key),
        tier=(t or {}).get("tier", 50), note=(t or {}).get("note", ""),
        quota_source=(t or {}).get("quota_source"),
        capabilities=dict((t or {}).get("capabilities", {})), options=options)


async def _probe(p: providers_mod.Provider, api_key: str = "") -> Dict[str, Any]:
    options = dict(p.options)
    if options.get("secret"):
        options["api_key"] = api_key or secrets.get(p.key) or ""
    try:
        backend = adapters.build(p.info(), options)
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "detail": str(e), "models": []}
    try:
        health = await backend.health()
        models: List[str] = []
        if health.ok and hasattr(backend, "list_models"):
            try:
                models = await backend.list_models()
            except Exception:                       # noqa: BLE001
                pass
        return {"ok": health.ok, "detail": health.detail, "models": models[:200]}
    finally:
        await backend.close()


@app.get("/api/providers")
async def providers() -> Any:
    eng = engine()
    live = {b["key"]: b for b in await eng.describe()}
    out = []
    for p in eng.providers.all():
        b = live.get(p.key, {})
        out.append(_provider_view(p, {"ok": bool(b.get("ok")),
                                      "detail": b.get("detail") or ("off" if not p.enabled else "")}))
    return {"providers": out}


@app.get("/api/providers/templates")
def provider_templates() -> Any:
    return {"templates": catalog.TEMPLATES}


@app.get("/api/providers/discover")
async def provider_discover() -> Any:
    have = [p.to_json() for p in engine().providers.all()]
    return {"found": await catalog.discover(have)}


@app.post("/api/providers/test")
async def provider_test(body: ProviderBody) -> Any:
    return await _probe(_draft(body), body.api_key)


@app.post("/api/providers")
async def provider_create(body: ProviderBody) -> Any:
    eng = engine()
    draft = _draft(body)
    draft.key = eng.free_key(draft.key)
    if draft.options.get("secret"):
        if not body.api_key:
            raise HTTPException(400, "this provider needs an API key")
        secrets.put(draft.key, body.api_key)
    eng.providers.upsert(draft)
    await eng.reload()
    return _provider_view(draft, await _probe(draft))


@app.patch("/api/providers/{key}")
async def provider_patch(key: str, body: ProviderPatch) -> Any:
    eng = engine()
    p = eng.providers.get(key)
    if p is None:
        raise HTTPException(404, "no such provider")
    if body.label is not None:
        p.label = body.label
    if body.enabled is not None:
        p.enabled = body.enabled
    if body.tier is not None:
        p.tier = body.tier
    if body.model is not None:
        if body.model:
            p.options["model"] = body.model
        else:
            p.options.pop("model", None)
    if body.base_url:
        p.options["base_url"] = body.base_url
    if body.capabilities is not None:
        p.capabilities.update(body.capabilities)
    if body.runtime is not None:
        p.runtime.update(body.runtime)
    if body.api_key:
        secrets.put(key, body.api_key)
        p.options["secret"] = True
    eng.providers.upsert(p)
    await eng.reload()
    return _provider_view(p, await _probe(p))


@app.delete("/api/providers/{key}")
async def provider_delete(key: str) -> Any:
    eng = engine()
    if not eng.providers.delete(key):
        raise HTTPException(404, "no such provider")
    secrets.delete(key)
    eng.registry.remove_provider(key)
    await eng.reload()
    return {"deleted": key}


@app.post("/api/providers/{key}/codex-host")
async def provider_codex_host(key: str) -> Any:
    """Fetch the helper Codex needs to edit files, from Codex's own release."""
    backend = engine().get(key)
    if backend is None or backend.info.kind != "codex":
        raise HTTPException(404, "not a Codex provider")
    try:
        message = await codex_host.install(getattr(backend, "bin", "") or "codex")
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, str(e))
    engine()._health.pop(key, None)                 # re-check on the next look
    return {"message": message}


@app.get("/api/providers/{key}/models")
async def provider_models(key: str) -> Any:
    p = engine().providers.get(key)
    if p is None:
        raise HTTPException(404, "no such provider")
    return await _probe(p)


# ---- catalog and deploy ---------------------------------------------------

class DeployBody(BaseModel):
    repo: str
    label: str = ""


@app.get("/api/catalog")
async def catalog_search(q: str = "", limit: int = 30) -> Any:
    try:
        return {"models": await deploy_mod.search(q, limit)}
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, f"Hugging Face: {e}")


@app.get("/api/catalog/fit")
async def catalog_fit(repo: str) -> Any:
    try:
        d = await deploy_mod.details(repo)
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, f"Hugging Face: {e}")
    budget = int(deploy_mod.settings()["context_budget"])
    room = deploy_mod.fit(d, engine().models.memory().free_gb, budget)
    d.pop("config", None)
    return {**d, **room}


@app.post("/api/deploy")
async def deploy_model(body: DeployBody) -> Any:
    if "/" not in body.repo:
        raise HTTPException(400, "a Hugging Face repo looks like org/name")
    return await engine().deploy(body.repo, body.label)


# ---- models behind providers ---------------------------------------------

class MeasureBody(BaseModel):
    model: str = ""


@app.get("/api/registry")
def registry_list() -> Any:
    return {"models": [m.to_json() for m in engine().registry.all()]}


@app.post("/api/registry/discover")
async def registry_discover() -> Any:
    return {"found": await engine().discover_models()}


@app.post("/api/registry/{provider}/measure")
async def registry_measure(provider: str, body: MeasureBody) -> Any:
    try:
        return await engine().measure(provider, body.model)
    except KeyError as e:
        raise HTTPException(404, f"unknown model {e}")


# ---- schedules -------------------------------------------------------------

class ScheduleBody(BaseModel):
    name: str = ""
    prompt: str = ""
    cwd: str = ""
    backend: str = ""
    spec: Dict[str, Any] = {}
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    cwd: Optional[str] = None
    backend: Optional[str] = None
    spec: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


@app.get("/api/schedules")
def schedules_list() -> Any:
    return {"schedules": [s.to_json() for s in engine().schedules.all()]}


@app.post("/api/schedules")
def schedules_create(body: ScheduleBody) -> Any:
    if not body.prompt.strip():
        raise HTTPException(400, "a schedule needs something to ask")
    if body.spec.get("kind") not in ("interval", "daily"):
        raise HTTPException(400, "schedule kind must be interval or daily")
    return engine().schedules.create(body.name, body.prompt.strip(), body.spec, body.cwd,
                                     body.backend, body.enabled).to_json()


@app.patch("/api/schedules/{sid}")
def schedules_update(sid: str, body: SchedulePatch) -> Any:
    got = engine().schedules.update(sid, **body.model_dump(exclude_none=True))
    if got is None:
        raise HTTPException(404, "no such schedule")
    return got.to_json()


@app.delete("/api/schedules/{sid}")
def schedules_delete(sid: str) -> Any:
    if not engine().schedules.delete(sid):
        raise HTTPException(404, "no such schedule")
    return {"ok": True}


@app.post("/api/schedules/{sid}/run")
async def schedules_run(sid: str) -> Any:
    """Fire it now, on top of its timetable."""
    eng = engine()
    s = eng.schedules.get(sid)
    if s is None:
        raise HTTPException(404, "no such schedule")
    return await eng.fire(s)


@app.get("/api/bench")
def bench_status() -> Any:
    """The public items eki measures with, and what it would measure next."""
    return {"sets": bench.status(), "attribution": bench.attribution(),
            "due": engine().auto_measure_due(),
            "auto_measure": settings_mod.load().get("auto_measure", "local")}


@app.post("/api/bench/fetch")
async def bench_fetch(force: bool = False) -> Any:
    """Fetch (or refetch) the public items; a new sample when forced."""
    loop = asyncio.get_running_loop()
    got = {}
    for name in bench.SETS:
        try:
            items = await loop.run_in_executor(None, lambda n=name: bench.fetch(n, force=force))
            got[name] = len(items)
        except Exception as e:                      # noqa: BLE001
            got[name] = f"failed: {e}"
    return got


class RegistryPatch(BaseModel):
    model: str = ""
    enabled: Optional[bool] = None
    forget: bool = False
    cost_weight: Optional[float] = None


@app.patch("/api/registry/{provider}")
def registry_patch(provider: str, body: RegistryPatch) -> Any:
    reg = engine().registry
    if body.enabled is not None and not reg.set_enabled(provider, body.model, body.enabled):
        raise HTTPException(404, "unknown model")
    if body.forget and not reg.forget_measurements(provider, body.model):
        raise HTTPException(404, "unknown model")
    if body.cost_weight is not None:
        rec = reg.get(provider, body.model)
        if rec is None:
            raise HTTPException(404, "unknown model")
        rec.cost_weight = body.cost_weight
        reg.upsert(rec)
    rec = reg.get(provider, body.model)
    return rec.to_json() if rec else {}


@app.get("/api/settings")
def get_settings() -> Any:
    return settings_mod.load()


@app.put("/api/settings")
async def put_settings(body: Dict[str, Any]) -> Any:
    saved = settings_mod.save(body)
    await engine().reload()             # the router model may have changed
    return saved


# ---- eki as a model provider (Codex → local models) ------------------------

@app.post("/v1/responses")
async def responses_api(request: Request) -> Any:
    """OpenAI's Responses API, for Codex, over eki's local models: the model
    is a provider key; the server behind it is started if asleep."""
    body = await request.json()
    eng = engine()
    key = str(body.get("model") or "")
    provider = eng.providers.get(key)
    if provider is None or not provider.options.get("base_url"):
        raise HTTPException(404, f"no local model {key!r}")
    local = eng.models.for_backend(key)
    if local is not None and not local.running:
        message = await eng.models.start(key)
        if not local.running:
            raise HTTPException(503, message)
    if local is not None:
        eng.models.hold(key)

    async def frames() -> AsyncIterator[str]:
        try:
            async for frame in gateway.respond(str(provider.options["base_url"]),
                                               str(provider.options.get("model") or key), body):
                yield frame
        finally:
            if local is not None:
                eng.models.release(key)

    return StreamingResponse(frames(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


# ---- usage ----------------------------------------------------------------

LABELS = {"claude": "Claude", "codex": "Codex"}


def usage_view() -> Any:
    STATE["usage_seen"] = time.time()   # someone is looking; see refresh_claude()
    eng = engine()
    readings = []
    for key in eng.quota.providers:
        reading = eng.quota.latest.get(key)
        view = reading.to_json() if reading else {"provider": key, "windows": [],
                                                  "observed_at": None, "age_seconds": None,
                                                  "error": "", "note": "not read yet"}
        view["label"] = LABELS.get(key, key.title())
        if reading:
            paced = quota_pace.provider_pace(reading, ceiling=eng.quota.ceiling)
            for w, wv in zip(reading.windows, view["windows"]):
                p = quota_pace.window_pace(w)
                if w.kind == "window":
                    token = w.key.rsplit("_", 1)[-1].lower()
                    own = paced.pace if w.primary and paced.pace.window == w.label \
                        else paced.models.get(token)
                    if own is not None and own.on_credits:
                        p = own
                wv["pace"] = {"factor": p.factor, "elapsed": p.elapsed, "ahead": p.ahead,
                              "why": p.why}
        readings.append(view)
    return {"providers": readings,
            "ceiling": eng.quota.ceiling,
            "claude_bridge": claude_bridge.installed(),
            "claude_probe": bool(settings_mod.load()["claude_probe"]),
            "claude_probe_ready": claude_probe.trusted(),
            "claude_probe_hint": claude_probe.TRUST_HINT}


@app.get("/api/usage")
def usage() -> Any:
    return usage_view()


@app.post("/api/usage/refresh")
async def usage_refresh() -> Any:
    await engine().quota.refresh(force=True)
    return usage_view()


class BridgeBody(BaseModel):
    enabled: bool


@app.post("/api/usage/claude-bridge")
async def claude_bridge_toggle(body: BridgeBody) -> Any:
    """Opt in or out of reading Claude's limits via Claude Code's status line."""
    try:
        message = claude_bridge.install() if body.enabled else claude_bridge.uninstall()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    await engine().quota.refresh(force=True)
    return {"message": message, **usage_view()}


def _claude_binary() -> str:
    backend = engine().get("claude")
    return getattr(backend, "bin", "") or "claude"


@app.post("/api/usage/claude-probe")
async def refresh_claude() -> Any:
    """Go and get a reading rather than waiting for one."""
    try:
        message = await claude_probe.refresh(_claude_binary())
    except claude_probe.NotTrusted as e:
        raise HTTPException(409, str(e))
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, str(e))
    await engine().quota.refresh(force=True)
    return {"message": message, **usage_view()}


# ---- settings -------------------------------------------------------------

@app.get("/api/models")
def models() -> Any:
    eng = engine()
    from dataclasses import asdict
    return {"models": eng.models.describe(), "memory": asdict(eng.models.memory())}


@app.post("/api/models/{key}/start")
async def start_model(key: str, force: bool = False) -> Any:
    try:
        # someone pressed Start: that outranks a server resting in memory
        return {"message": await engine().models.start(key, force=force, eager=True)}
    except KeyError:
        raise HTTPException(404, "no such model")


@app.post("/api/models/{key}/stop")
async def stop_model(key: str) -> Any:
    try:
        return {"message": await engine().models.stop(key)}
    except KeyError:
        raise HTTPException(404, "no such model")


class IdleBody(BaseModel):
    minutes: float                  # 0 keeps it loaded


@app.put("/api/models/{key}/idle")
def set_model_idle(key: str, body: IdleBody) -> Any:
    """How long this server stays loaded unused. Changed in place — no engine
    reload, so nothing in flight notices — and saved with its provider."""
    eng = engine()
    model = eng.models.get(key)
    if model is None:
        raise HTTPException(404, "no such model")
    if body.minutes < 0 or body.minutes > 24 * 60:
        raise HTTPException(422, "between 0 (keep loaded) and 1440 minutes")
    model.idle_minutes = float(body.minutes)
    p = eng.providers.get(model.backend or key)
    if p is not None:
        p.runtime["idle_minutes"] = model.idle_minutes
        eng.providers.upsert(p)
    # the window starts now, not from whenever it last answered: shortening
    # it shouldn't unload a model out from under the person changing it
    eng.models.touch(key)
    if model.pinned:
        return {"message": f"{model.label} stays loaded until you stop it"}
    return {"message": f"{model.label} unloads after {body.minutes:g} min unused"}


@app.get("/api/policy")
def get_policy() -> Any:
    return engine().policy.to_json()


@app.put("/api/policy")
def put_policy(body: PolicyBody) -> Any:
    eng = engine()
    known = {b.key for b in eng.backends}
    unknown = [k for k in list(body.disabled) + list(body.tiers) + list(body.order)
               if k not in known]
    if unknown:
        raise HTTPException(400, f"unknown backend(s): {', '.join(sorted(set(unknown)))}")
    eng.set_policy(policy_mod.Policy(
        disabled=list(body.disabled), tiers=dict(body.tiers),
        order=list(body.order), quota_ceiling=body.quota_ceiling))
    return eng.policy.to_json()


@app.get("/api/health")
def health() -> Any:
    eng = engine()
    return {"ok": True, "backends": len(eng.backends), "running": eng.runner.running}


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="eki-service")
    ap.add_argument("-c", "--config", default=str(config_mod.default_path()))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for note in migrate.run(Path(__file__).resolve().parent.parent):
        log.info("migrated: %s", note)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    STATE["engine"] = Engine(config_mod.load(args.config), owner=True, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
