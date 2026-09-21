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
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import catalog
from . import config as config_mod
from . import providers as providers_mod
from . import deploy as deploy_mod
from . import migrate
from . import secrets
from . import settings as settings_mod
from .adapters import base as adapters
from . import policy as policy_mod
from .engine import Engine
from .quota import claude_bridge, claude_probe
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

    reaper = asyncio.create_task(reap())
    fresh = asyncio.create_task(keep_claude_fresh())
    yield
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

@app.get("/api/conversations")
def conversations(limit: int = 30, q: str = "") -> Any:
    eng = engine()
    rows = eng.store.search(q, limit) if q.strip() else eng.store.conversations(limit)
    live = {r["conversation_id"] for r in eng.runs.live()}
    out = []
    for r in rows:
        row = dict(r)
        row["live"] = row["id"] in live       # the sidebar shows what's working
        out.append(row)
    return out


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
    await eng.reload()
    return {"deleted": key}


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


@app.get("/api/settings")
def get_settings() -> Any:
    return settings_mod.load()


@app.put("/api/settings")
async def put_settings(body: Dict[str, Any]) -> Any:
    saved = settings_mod.save(body)
    await engine().reload()             # the router model may have changed
    return saved


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
    memory = eng.models.memory()
    return {"models": eng.models.describe(),
            "memory": {"total_gb": memory.total_gb, "ceiling_gb": memory.ceiling_gb,
                       "committed_gb": memory.committed_gb, "free_gb": memory.free_gb}}


@app.post("/api/models/{key}/start")
async def start_model(key: str, force: bool = False) -> Any:
    try:
        return {"message": await engine().models.start(key, force=force)}
    except KeyError:
        raise HTTPException(404, "no such model")


@app.post("/api/models/{key}/stop")
async def stop_model(key: str) -> Any:
    try:
        return {"message": await engine().models.stop(key)}
    except KeyError:
        raise HTTPException(404, "no such model")


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
    STATE["engine"] = Engine(config_mod.load(args.config), owner=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
