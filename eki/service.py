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
import os
import re
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import uvicorn
from fastapi import Request, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
from pydantic import BaseModel

from . import catalog
from . import mcpregistry
from . import skills as skills_mod
from . import standing
from . import nesting
from .adapters.base import BackendError
from . import codex_host
from . import config as config_mod
from . import providers as providers_mod
from . import deploy as deploy_mod
from . import engines as engines_mod
from . import identify as identify_mod
from . import workflow as workflow_mod
from . import profile as profile_mod
from . import suggest as suggest_mod
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
    # everything the engine starts — Claude Code, Codex, the shells they open —
    # inherits this: an `eki ask` from in there is an agent asking, not you
    os.environ[INSIDE] = "1"
    eng.quota.start()
    # eki watching itself: errors logged by its own loops and request
    # handlers are faults too (eki/observe.py)
    from . import observe as observe_mod
    loop = asyncio.get_running_loop()
    faults = observe_mod.install_log_handler()
    faults.on_fault = lambda entry: loop.call_soon_threadsafe(eng.fault_seen, entry)
    # one skill store, linked into both CLIs' folders (eki/skills.py)
    report = await asyncio.to_thread(skills_mod.boot)
    if report.get("linked") or report.get("conflicts") or report.get("error"):
        log.info("skills: %s", report)
    # one standing context: AGENTS.md, linked into both CLIs (eki/standing.py)
    report = await asyncio.to_thread(standing.boot)
    if report.get("linked") or report.get("conflicts") or report.get("error"):
        log.info("standing context: %s", report)
    # one tool registry, rendered into Codex's config (its web search
    # switch included) and Gemini CLI's settings at start; Claude Code
    # gets it per session
    try:
        await asyncio.to_thread(mcpregistry.render_codex)
    except OSError as e:
        log.warning("codex config: %s", e)
    try:
        report = await asyncio.to_thread(mcpregistry.render_gemini)
        if report.get("conflicts") or report.get("error"):
            log.info("gemini mcp: %s", report)
    except OSError as e:
        log.warning("gemini settings: %s", e)

    async def reap() -> None:
        # unload local models eki started once they've sat unused a while
        ticks = 0
        while True:
            await asyncio.sleep(60)
            ticks += 1
            if ticks % 60 == 1:
                # once an hour: threads' folder copies nobody has used in a
                # week, and builds that are neither current nor previous
                try:
                    from . import workspace as workspace_mod
                    from . import builds as builds_mod
                    gone = await asyncio.to_thread(workspace_mod.sweep)
                    gone += await asyncio.to_thread(builds_mod.prune)
                    if gone:
                        log.info("removed %d unused worktree(s)/build(s)", len(gone))
                except Exception:                   # noqa: BLE001
                    log.exception("sweep")
            if ticks % 60 == 30:
                # the daily model watch runs itself once it's due
                asyncio.create_task(eng.watch_refresh())
            try:
                # what a request costs on each subscription (eki/capacity.py)
                await asyncio.to_thread(eng.capacity_tick)
            except Exception:                       # noqa: BLE001
                log.exception("capacity")
            try:
                # a swap that has finished: say how it went, once
                await eng.settle_swap()
            except Exception:                       # noqa: BLE001
                log.exception("swap outcome")
            try:
                # self-work cut off while this engine stayed up (a program
                # lost): taken up again; and the release train, if it's time
                await eng.self_carry_on()
                await eng.self_release()
            except Exception:                       # noqa: BLE001
                log.exception("self-work steps")
            try:
                # the weekly restart drill, while eki works on itself (eki/drill.py)
                if await eng.self_drill_tick() == "started":
                    log.info("started the weekly restart drill")
            except Exception:                       # noqa: BLE001
                log.exception("restart drill")
            try:
                # the daily digest, once it's due (eki/digest.py)
                if await eng.self_digest():
                    log.info("wrote the daily digest")
            except Exception:                       # noqa: BLE001
                log.exception("daily digest")
            try:
                # finished work dirs after a week; a worker nobody took up, killed
                await asyncio.to_thread(eng.sweep_workers)
            except Exception:                       # noqa: BLE001
                log.exception("worker housekeeping")
            if ticks % 5 == 2:
                try:
                    # what the engine runs but your checkout missed: in, once it can go
                    await eng.catch_up_checkout()
                except Exception:                   # noqa: BLE001
                    log.exception("catch up")
                try:
                    # an app rebuild that waited while you were using it
                    await eng.app_tick()
                except Exception:                   # noqa: BLE001
                    log.exception("app rebuild")
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
        # the programs the last engine left working (eki/workers.py): taken
        # up again before anything decides what to carry on
        kept = eng.reattach_workers()
        if kept:
            log.info("took up %d program(s) still working", kept)
    except Exception:                               # noqa: BLE001
        log.exception("reattaching workers")
    try:
        # the model servers eki started outlive it (a session of their own):
        # taken back as eki's at once, even with the record of them lost —
        # not only at the first idle check, a minute on (eki/drill.py)
        claimed = await asyncio.to_thread(eng.models.claim_own)
        if claimed:
            log.info("took back model server(s) eki started: %s", ", ".join(claimed))
    except Exception:                               # noqa: BLE001
        log.exception("model servers")
    try:
        noted = eng.note_interruptions()
        if noted:
            log.info("noted %d interrupted run(s)", noted)
        resumed = await eng.resume_interrupted()
        if resumed:
            log.info("carried on with %d interrupted run(s)", resumed)
        try:
            # what landed while no engine watched: ticked, and its items closed
            await asyncio.to_thread(eng.self_settle)
        except Exception:                           # noqa: BLE001
            log.exception("self-work settle")
        # every step of self-work the last engine was in the middle of —
        # a check, a conflict resolution — taken up again (eki/steps.py)
        carried = await eng.self_carry_on()
        if carried:
            log.info("carried on with %d step(s) of self-work", carried)
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
            # which models are out there, if it's been a day (eki/watch.py)
            await eng.watch_refresh()
        except Exception:                           # noqa: BLE001
            log.exception("model watch")
        try:
            named = await eng.backfill_titles()
            if named:
                log.info("named %d older threads", named)
        except Exception:                           # noqa: BLE001
            log.exception("titles")

    async def follow() -> None:
        # a program that carried on by itself gets its turn written down
        while True:
            await asyncio.sleep(2)
            try:
                await eng.follow_live()
            except Exception:                       # noqa: BLE001
                log.exception("following live sessions")

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

    async def work_while_idle() -> None:
        """The idle shift: the next missing piece of a declared goal, when
        the machine has room for it (eki/goals.py, eki/shift.py)."""
        await asyncio.sleep(30)
        while True:
            try:
                await eng.shift_tick()
            except Exception:                       # noqa: BLE001
                log.exception("idle shift")
            # every 8 s, or the moment a piece ends — then the next starts at once
            eng.shift_wake.clear()
            try:
                await asyncio.wait_for(eng.shift_wake.wait(), 8)
                await asyncio.sleep(0.5)            # let the run settle
            except asyncio.TimeoutError:
                pass

    shift_task = asyncio.create_task(work_while_idle())
    reaper = asyncio.create_task(reap())
    fresh = asyncio.create_task(keep_claude_fresh())
    naming = asyncio.create_task(name_old_threads())
    auto = asyncio.create_task(measure_on_its_own())
    follower = asyncio.create_task(follow())
    yield
    shift_task.cancel()
    eng._awake.let_go()
    follower.cancel()
    auto.cancel()
    naming.cancel()
    fresh.cancel()
    reaper.cancel()
    await eng.quota.stop()
    await eng.runner.stop()
    await eng.close()


app = FastAPI(title="eki", lifespan=lifespan)
#: set in the engine's environment; the CLI reads it (see cli.cmd_ask)
INSIDE = "EKI_INSIDE"
#: the thread a request comes from, when a program eki started makes it (cli.parent_headers)
PARENT_HEADER = "X-Eki-Parent"
#: what only the person may do: how far eki goes alone, what it applies to
#: itself, the settings and the routing policy
PERSON_ONLY = (("PUT", re.compile(r"^/api/(self/)?settings$")),
               ("PUT", re.compile(r"^/api/policy$")),
               ("POST", re.compile(r"^/api/self/on$")),
               ("POST", re.compile(r"^/api/self/changes/[^/]+/(apply|undo)$")),
               ("POST", re.compile(r"^/api/self/release$")))


def person_only(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in PERSON_ONLY)


@app.middleware("http")
async def not_for_eki_alone(request: Request, call_next: Any) -> Any:
    """Work eki started on its own — and anything below it — is refused the
    switches that are the person's (selfloop.owner): it can't apply a change
    to eki, raise its own autonomy, or change the settings it runs under."""
    parent = request.headers.get(PARENT_HEADER, "")
    if parent and person_only(request.method, request.url.path) and engine().owner_of(parent) == "eki":
        return JSONResponse({"detail": "refused: this was asked from work eki started on its own, "
                                       "and only you can do that"}, status_code=403)
    return await call_next(request)


class AskBody(BaseModel):
    prompt: str
    conversation: str = ""
    backend: str = ""
    repo: str = ""
    images: bool = False
    # a picture's size and how many, given outright; the words of the prompt
    # ("4 images… at 1536x1024") say the same thing without these
    width: int = 0
    height: int = 0
    batch: int = 0
    #: pictures on this Mac to show with the question and give to the program
    attachments: List[str] = []
    #: "agent": a program asking through eki's tools, not a person
    via: str = ""
    #: an agent's request: the grant of the run asking, and what it hands
    #: on — read-only, the commands the child may run, paths it may write
    #: (eki/grant.py). The child gets no more than the parent has.
    parent: Dict[str, Any] = {}
    read_only: bool = False
    commands: List[str] = []
    paths: List[str] = []
    #: how deep the asker is, and the run it asks from (see nesting)
    depth: int = 0
    parent_run: str = ""
    #: the thread of the program asking, when a program asks (engine.owner_of)
    parent_thread: str = ""
    #: the folder the call was made from: the run belongs to its project
    where: str = ""


class AttachmentBody(BaseModel):
    data: str                 # base64
    name: str = "image.png"


@app.post("/api/attachments")
def attachment_create(body: AttachmentBody) -> Any:
    """A pasted or dropped picture, kept under ~/.eki/attachments so the
    thread can show it later; the path goes into the ask."""
    import base64
    import uuid as uuid_mod
    ext = (body.name.rsplit(".", 1)[-1].lower() if "." in body.name else "png")
    if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
        ext = "png"
    folder = Path("~/.eki/attachments").expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid_mod.uuid4().hex[:12]}.{ext}"
    try:
        path.write_bytes(base64.b64decode(body.data))
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"not an image: {e}")
    return {"path": str(path)}


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
    try:
        return await engine().ask(body.prompt, conversation=body.conversation,
                                  backend_key=body.backend, repo=body.repo,
                                  images=body.images,
                                  image={"width": body.width, "height": body.height, "batch": body.batch},
                                  attachments=body.attachments, via=body.via, parent=body.parent,
                                  wants={"read_only": body.read_only, "commands": body.commands,
                                         "paths": body.paths},
                                  depth=body.depth, parent_run=body.parent_run,
                                  parent_thread=body.parent_thread, where=body.where)
    except nesting.TooDeep as e:
        raise HTTPException(429, str(e))
    except ValueError as e:
        raise HTTPException(403, str(e))


@app.get("/api/runs")
def runs(limit: int = 40, project: str = "") -> Any:
    """The latest runs; with `project` (a project's root), only its own."""
    return engine().runs.recent(limit, project=project)


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


class AllowBody(BaseModel):
    #: the grant of the run asking, when an agent asks (eki/grant.py)
    parent: Dict[str, Any] = {}


@app.post("/api/runs/{rid}/allow")
async def allow_run(rid: str, body: Optional[AllowBody] = None) -> Any:
    """Allow what a narrowed run was refused, and run it again (Engine.allow)."""
    started = await engine().allow(rid, (body.parent if body else None) or None)
    if not started:
        raise HTTPException(409, "that run wasn't refused anything, or it was allowed already")
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


# ---- Claude Code under eki: what its panels show ----------------------------

class ClaudeControlBody(BaseModel):
    op: str
    conversation: str = ""
    cwd: str = ""
    backend: str = ""                 # the picked backend; decides Claude Code or Codex
    args: Dict[str, Any] = {}


@app.post("/api/agent/control")
async def agent_control(body: ClaudeControlBody) -> Any:
    """The panels, for whichever program the picked backend (or the thread)
    is — Claude Code's control channel or Codex's app-server, same ops.
    See Engine.claude_control and Engine.codex_control."""
    try:
        return await engine().agent_control(body.op, body.conversation, body.cwd, body.backend, **body.args)
    except BackendError as e:
        raise HTTPException(409, str(e))


@app.post("/api/claude/control")
async def claude_control(body: ClaudeControlBody) -> Any:
    """One control request to the thread's Claude Code session (or a
    folder's warm one): mcp, models, set_model, account, permission_mode,
    thinking, usage, context, rules, rewind, rename, background, stop_task,
    reload_skills, settings, update_settings, interrupt, status, and the
    mcp_* actions (toggle, reconnect, authenticate, oauth_callback,
    clear_auth, import, apply). See Engine.claude_control."""
    try:
        return await engine().claude_control(body.op, body.conversation, body.cwd, **body.args)
    except BackendError as e:
        raise HTTPException(409, str(e))


class McpServerBody(BaseModel):
    name: str
    type: str = ""
    command: str = ""
    args: List[str] = []
    env: Dict[str, str] = {}
    url: str = ""
    headers: Dict[str, str] = {}
    backends: List[str] = ["claude", "codex", "gemini"]
    enabled: bool = True
    provides: List[str] = []
    #: from the catalog: its id, and the key it asks for
    catalog: str = ""
    key: str = ""


@app.get("/api/mcp/catalog")
def mcp_catalog() -> Any:
    """Servers eki knows how to add in one step, and what each gives a backend."""
    return {"catalog": mcpregistry.CATALOG}


class McpToggleBody(BaseModel):
    enabled: bool
    backend: str = ""


@app.get("/api/mcp")
def mcp_registry() -> Any:
    """eki's own MCP registry (~/.eki/mcp.json), rendered into both CLIs."""
    return {"servers": mcpregistry.load(), "path": str(mcpregistry.PATH),
            "codex_config": str(mcpregistry.CODEX_CONFIG),
            "gemini_settings": str(mcpregistry.GEMINI_SETTINGS)}


@app.put("/api/mcp/{name}")
async def mcp_put(name: str, body: McpServerBody) -> Any:
    spec = body.model_dump()
    if body.catalog:
        entry = mcpregistry.catalog_entry(body.catalog)
        if entry is None:
            raise HTTPException(404, "no such catalog entry")
        spec = {**spec, "command": entry["command"], "provides": entry["provides"]}
        if entry["key_env"]:
            if not body.key:
                raise HTTPException(400, f"{entry['title']} needs a key ({entry['key_env']})")
            spec["env"] = {**spec.get("env", {}), entry["key_env"]: body.key}
    try:
        servers = mcpregistry.put(name, spec)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await engine().reload()            # a backend may have gained a capability
    return {"servers": servers}


@app.post("/api/mcp/{name}/enabled")
async def mcp_enabled(name: str, body: McpToggleBody) -> Any:
    try:
        servers = mcpregistry.set_enabled(name, body.enabled, body.backend)
    except KeyError:
        raise HTTPException(404, "no such server in eki's registry")
    await engine().reload()
    return {"servers": servers}


@app.delete("/api/mcp/{name}")
async def mcp_delete(name: str) -> Any:
    servers = mcpregistry.remove(name)
    await engine().reload()
    return {"servers": servers}


# ---- skills: one store, a view per backend (eki/skills.py) --------------------

class SkillBody(BaseModel):
    text: str = ""                  # the whole SKILL.md, or…
    description: str = ""           # …a description and a body
    body: str = ""
    backends: Optional[List[str]] = None


class SkillToggleBody(BaseModel):
    enabled: bool
    backend: str = ""


class SkillImportBody(BaseModel):
    names: List[str] = []


def _skills_state() -> Dict[str, Any]:
    return {"skills": skills_mod.list_skills(), "unmanaged": skills_mod.unmanaged(),
            "store": str(skills_mod.STORE),
            "views": {k: str(v) for k, v in skills_mod.VIEWS.items()}}


@app.get("/api/skills")
def skills_list() -> Any:
    """eki's skill store, what each backend sees, and skills eki doesn't hold yet."""
    return _skills_state()


@app.get("/api/skills/{name}")
def skill_get(name: str) -> Any:
    try:
        return {"skill": skills_mod.get(name), "text": skills_mod.source(name),
                "history": skills_mod.history(20, name)}
    except KeyError:
        raise HTTPException(404, "no such skill")


@app.put("/api/skills/{name}")
def skill_put(name: str, body: SkillBody) -> Any:
    try:
        skills_mod.put(name, text=body.text, description=body.description,
                       body=body.body, backends=body.backends)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _skills_state()


@app.post("/api/skills/{name}/enabled")
def skill_enabled(name: str, body: SkillToggleBody) -> Any:
    try:
        skills_mod.set_enabled(name, body.enabled, body.backend)
    except KeyError:
        raise HTTPException(404, "no such skill")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _skills_state()


@app.delete("/api/skills/{name}")
def skill_delete(name: str) -> Any:
    try:
        skills_mod.remove(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _skills_state()


@app.post("/api/skills/import")
def skills_import(body: SkillImportBody) -> Any:
    return {**_skills_state(), "report": skills_mod.import_existing(body.names or None)}


class ExplainBody(BaseModel):
    prompt: str
    conversation: str = ""
    folder: str = ""


@app.get("/api/routing")
def routing(thread: str = "", folder: str = "") -> Any:
    """The routing table, the rules, and the room on each subscription —
    with a project's roster and policy when `folder` is in one."""
    eng = engine()
    return {**eng.routing_view(thread, folder), "text": eng.routing_text(thread, folder)}


@app.post("/api/routing/explain")
async def routing_explain(body: ExplainBody) -> Any:
    """Where a request would go and why — nothing runs."""
    return await engine().routing_explain(body.prompt, body.conversation, body.folder)


@app.get("/api/routing/replay")
async def routing_replay(limit: int = 40) -> Any:
    return await engine().routing_replay(limit)


@app.post("/api/routing/forget/{what}")
def routing_forget(what: str) -> Any:
    from . import table as table_mod
    rules = table_mod.load()
    gone = table_mod.forget(rules, what)
    table_mod.save(rules)
    return {"removed": gone}


@app.post("/api/access/request")
def access_request() -> Any:
    """Ask macOS, as eki, for Screen Recording and Accessibility (eki/launcher.py)."""
    from . import launcher
    if not launcher.request_access():
        raise HTTPException(409, "the engine isn't running under the eki app — `eki agent install` builds it")
    return {"asked": True, "app": str(launcher.APP)}


@app.post("/api/pick-folder")
async def pick_folder(body: Dict[str, Any]) -> Any:
    """A folder chosen in macOS's own dialog — the board's Choose… button. The
    engine is on this Mac, so the dialog is too, in a browser or in the app.
    {"path": ""} when cancelled."""
    return {"path": await choose_folder(str(body.get("from") or ""), str(body.get("prompt") or ""))}


async def choose_folder(start: str = "", prompt: str = "") -> str:
    start = os.path.expanduser(start.strip() or "~")
    if not os.path.isdir(start):
        start = os.path.dirname(start) if os.path.isdir(os.path.dirname(start)) else os.path.expanduser("~")
    words = prompt.strip() or "Choose a folder for eki to work in"
    # AppleScript strings take the same escapes JSON's do for quotes and backslashes
    script = ["activate",
              f"POSIX path of (choose folder with prompt {json.dumps(words)} "
              f"default location (POSIX file {json.dumps(start)}))"]
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", *[x for line in script for x in ("-e", line)],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError:
        raise HTTPException(501, "this needs macOS")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    if proc.returncode != 0:                        # cancelled
        return ""
    path = out.decode("utf-8", "replace").strip()
    return path.rstrip("/") if path != "/" else path


@app.get("/api/goals")
def goals_view() -> Any:
    out = engine().goals_view()
    out["page"] = _board_version()          # the board reloads itself when it changes
    return out


def _board_version() -> str:
    try:
        st = (Path(__file__).with_name("web") / "goals.html").stat()
        return f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        return ""


def _goal_call(fn, *args, **kw) -> Any:
    from . import goals as goals_mod
    try:
        return fn(*args, **kw)
    except goals_mod.GoalError as e:
        raise HTTPException(400, str(e))


@app.post("/api/goals")
def goals_create(body: Dict[str, Any]) -> Any:
    """A goal: what to keep doing, in words; when; a folder; may it use
    subscriptions; may it use the screen (then only while you're away); a
    repeating one: on time rather than when there's room, fresh each time."""
    return _goal_call(engine().goals_create, str(body.get("text") or ""), body.get("when"),
                      str(body.get("folder") or ""), bool(body.get("spare")), bool(body.get("screen")),
                      bool(body.get("on_time")), bool(body.get("fresh")))


@app.patch("/api/goals/{gid}")
def goals_update(gid: str, body: Dict[str, Any]) -> Any:
    fields = {k: body[k] for k in ("text", "when", "folder", "spare", "screen", "on_time", "fresh", "state") if k in body}
    if "state" in fields and fields["state"] not in ("active", "paused"):
        raise HTTPException(400, "a goal can be paused or made active")
    return _goal_call(engine().goals_update, gid, **fields)


@app.post("/api/goals/{gid}/run")
def goals_run_now(gid: str) -> Any:
    return _goal_call(engine().goals_run_now, gid)


@app.delete("/api/goals/{gid}")
def goals_remove(gid: str) -> Any:
    return _goal_call(engine().goals_remove, gid)


@app.post("/api/goals/mode")
def goals_mode(body: Dict[str, Any]) -> Any:
    """Background work on or off; whenever there's room, or only when you're away."""
    eng = engine()
    changes = {}
    if "on" in body:
        changes["background"] = "local" if body["on"] else "off"
    if body.get("when") in ("resources", "away"):
        changes["background_when"] = body["when"]
    if not changes:
        raise HTTPException(400, "on is true or false; when is resources or away")
    eng.settings = settings_mod.save({**settings_mod.load(), **changes})
    eng.shift_wake.set()
    return eng.goals_view()


@app.get("/goals")
def goals_board() -> Any:
    """The board: goals, their threads, a reply box."""
    from fastapi.responses import HTMLResponse
    page = Path(__file__).with_name("web") / "goals.html"
    return HTMLResponse(page.read_text(), headers={"Cache-Control": "no-store"})


@app.get("/api/goals/file")
def goals_file(path: str) -> Any:
    """A picture a goal's thread shows: eki's own images, or a file in a goal's folder."""
    from fastapi.responses import FileResponse
    from . import goals as goals_mod
    real = Path(path).expanduser().resolve()
    roots = [Path("~/.eki/images").expanduser().resolve()] + \
        [Path(g.folder).resolve() for g in goals_mod.all_goals() if g.folder]
    if not real.is_file() or not any(r == real or r in real.parents for r in roots):
        raise HTTPException(404, "not a file a goal made")
    return FileResponse(str(real), headers={"Cache-Control": "no-cache"})


@app.get("/api/goals/report")
def goals_report(hours: float = 24.0) -> Any:
    return engine().goals_report(hours)


@app.get("/api/watch")
def watched() -> Any:
    """The vendors' ladders and the local models eki suggests (eki/watch.py)."""
    from . import watch as watch_mod
    return watch_mod.load()


@app.post("/api/watch/refresh")
async def watch_refresh() -> Any:
    return await engine().watch_refresh(force=True)


@app.post("/api/watch/take/{name}")
async def watch_take(name: str) -> Any:
    try:
        return await engine().watch_take(name)
    except KeyError:
        raise HTTPException(404, f"{name} isn't a current suggestion")
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- eki working on itself (eki/selfengine.py, docs/self-build.md) ---------------------

def _self_call(fn, *args, **kw) -> Any:
    from . import selfwork
    try:
        return fn(*args, **kw)
    except selfwork.SelfWorkError as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


async def _self_await(fn, *args, **kw) -> Any:
    from . import selfwork
    try:
        return await fn(*args, **kw)
    except selfwork.SelfWorkError as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/self")
def self_view() -> Any:
    """What eki is doing to itself, what waits for you, what's next — and
    the last release train that finished merging, so an empty board still
    shows the last one worked."""
    from . import appbuild, treemerge
    out = engine().self_view()
    out["page"] = _board_version()
    out["app"] = appbuild.versions_line()          # "app: running …, installed …"
    if out.get("can"):
        out["tree_last"] = treemerge.last()
    return out


@app.post("/api/self")
async def self_ask(body: Dict[str, Any], request: Request) -> Any:
    """A change to eki: `when` now (a run, in its own thread) or later (the loop takes it)."""
    when = str(body.get("when") or "now")
    if when not in ("now", "later"):
        raise HTTPException(400, "when is now or later")
    return await _self_await(engine().self_ask, str(body.get("request") or ""), when=when,
                             conversation=str(body.get("conversation") or ""),
                             apply=bool(body.get("apply")), base=str(body.get("base") or ""),
                             check_base=bool(body.get("check_base", True)),
                             backend=str(body.get("backend") or ""), title=str(body.get("title") or ""),
                             parent=request.headers.get(PARENT_HEADER, ""))


@app.post("/api/self/on")
def self_on(body: Dict[str, Any]) -> Any:
    """Start or pause the goal that has eki work on itself when there's room."""
    return _self_call(engine().self_on, bool(body.get("on", True)),
                      spare=body["spare"] if "spare" in body else None)


@app.put("/api/self/settings")
def self_settings(body: Dict[str, Any]) -> Any:
    """How far it goes alone (autonomy, per area), how much waits for you, local models,
    how many at once."""
    return _self_call(engine().self_settings, **{k: body[k] for k in ("autonomy", "areas", "review_max", "local",
                                                                   "parallel", "release_minutes") if k in body})


@app.post("/api/self/release")
async def self_release() -> Any:
    """Go live now: what's applied and waiting for the next release train
    leaves at once, instead of at its time."""
    return await _self_await(engine().self_release, now=True)


@app.get("/api/self/changes/{cid}")
def self_change(cid: str) -> Any:
    from . import selfwork
    c = _self_call(selfwork.change, cid)
    fields = {k: v for k, v in c.items() if k in selfwork.Proposal.__dataclass_fields__}
    return {**c, "lines": selfwork.Proposal(**fields).lines(), **engine().self_stage(c["id"])}


@app.get("/api/self/changes/{cid}/diff")
def self_diff(cid: str) -> Any:
    return {"diff": _self_call(engine().self_diff, cid)}


@app.post("/api/self/changes/{cid}/{action}")
async def self_decide(cid: str, action: str, body: Optional[Dict[str, Any]] = None) -> Any:
    """A person's decision. Applying a change that touches protected paths
    takes {"confirm": true} — sent once they've been shown which."""
    eng = engine()
    fn = {"apply": eng.self_apply, "discard": eng.self_discard, "undo": eng.self_undo}.get(action)
    if fn is None:
        raise HTTPException(404, "apply, discard or undo")
    if action == "apply":
        return await _self_await(fn, cid, confirmed=bool((body or {}).get("confirm")),
                                 now=bool((body or {}).get("now")))
    return await _self_await(fn, cid)


@app.get("/api/self/items/{iid}")
def self_item(iid: str) -> Any:
    from . import selfloop, selfwork
    it = _self_call(selfloop.get, iid).to_json()
    try:
        it["change_detail"] = selfwork.change(it["change"]) if it.get("change") else None
    except selfwork.SelfWorkError:
        it["change_detail"] = None
    return it


@app.post("/api/self/items/{iid}/{action}")
def self_item_action(iid: str, action: str) -> Any:
    return _self_call(engine().self_item_action, iid, action)


@app.post("/api/self/roadmap/{key}/{action}")
def self_roadmap_action(key: str, action: str) -> Any:
    """A ROADMAP item the loop hasn't taken yet: person (leave it for me) or drop."""
    return _self_call(engine().self_roadmap_action, key, action)


@app.post("/api/self/note")
async def self_note() -> Any:
    """Write this week's note now, rather than when it's due."""
    return await _self_await(engine().self_note_now)


@app.post("/api/self/digest")
async def self_digest() -> Any:
    """Write today's digest now, rather than at `digest_at` — and say it."""
    return await _self_await(engine().self_digest, True)


@app.post("/api/self/notes/{nid}/{index}/{action}")
async def self_suggestion(nid: str, index: int, action: str) -> Any:
    """A weekly note's suggestion: ask (a request eki takes later), roadmap, dismiss."""
    return await _self_await(engine().self_suggestion, nid, index, action)


@app.get("/api/observe")
def observed(days: float = 7) -> Any:
    """What eki noticed about itself, and the fixes it proposed."""
    from . import observe as observe_mod
    return observe_mod.summary(days)


@app.get("/api/skills-learned")
def skills_learned(limit: int = 30) -> Any:
    """Skills eki learned from runs, and its latest reviews (eki/learn.py)."""
    from . import learn as learn_mod
    return {"skills": [s for s in skills_mod.list_skills() if s.get("learned")],
            "reviews": learn_mod.reviews(limit)}


@app.post("/api/conversations/{cid}/learn")
async def learn_from(cid: str) -> Any:
    """Review this thread's latest finished run for a skill, because you asked."""
    try:
        return await engine().learn_now(cid)
    except KeyError:
        raise HTTPException(404, "no finished run in that conversation")


@app.post("/api/skills/sync")
def skills_sync() -> Any:
    return {**_skills_state(), "report": skills_mod.sync()}


@app.get("/api/files")
def file_suggestions(cwd: str = "", q: str = "") -> Any:
    """`@file` completion: paths in the folder matching what's typed."""
    from . import files as files_mod
    return {"suggestions": files_mod.suggest(cwd, q)}


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
    options: Optional[Dict[str, Any]] = None        # only the keys in TUNABLE


#: What may be changed about a provider after it's added, by kind: for an image
#: model, the size and count it draws when a request names neither, and its limits.
TUNABLE = {"comfyui": ("width", "height", "batch", "max_batch", "max_pixels", "steps", "timeout_seconds")}


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


class WorkflowBody(BaseModel):
    base_url: str = "http://127.0.0.1:8188"
    text: str = ""                                  # an API-format export, pasted or read from a file
    template: str = ""                              # or one of workflow_mod.TEMPLATES…
    file: str = ""                                  # …made for this model file


async def _object_info(base_url: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{base_url.rstrip('/')}/object_info")
    r.raise_for_status()
    return r.json()


@app.get("/api/comfy/files")
async def comfy_files(base_url: str = "http://127.0.0.1:8188") -> Any:
    """The model files a ComfyUI can see, by loader, and the templates eki
    can build a workflow from for them."""
    try:
        info = await _object_info(base_url)
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, f"ComfyUI: {e}")
    files = workflow_mod.files_available(info)
    return {"files": files, "nodes": len(info),
            "templates": [{"id": k, "title": t["title"], "needs": t["needs"], "note": t["note"]}
                          for k, t in workflow_mod.TEMPLATES.items()]}


@app.post("/api/comfy/workflow")
async def comfy_workflow(body: WorkflowBody) -> Any:
    """Read a workflow (pasted, or built from a template for a model file),
    find where the prompt and the rest go, and check it against that
    ComfyUI. Nothing is saved: the Add sheet sends the graph back with the
    provider."""
    if body.template:
        t = workflow_mod.TEMPLATES.get(body.template)
        if t is None:
            raise HTTPException(400, "no such template")
        graph = t["make"](body.file) if body.file else t["make"]()
    else:
        try:
            graph = workflow_mod.parse(body.text)
        except ValueError as e:
            raise HTTPException(400, str(e))
    bindings = workflow_mod.infer(graph)
    try:
        info = await _object_info(body.base_url)
        problems = workflow_mod.problems(graph, info)
    except Exception as e:                          # noqa: BLE001
        problems = [f"couldn't check it against ComfyUI: {e}"]
    return {"graph": graph, "bindings": bindings.as_dict(), "summary": bindings.describe(),
            "problems": problems, "can_edit": bindings.image is not None,
            "ok": bindings.prompt is not None and not problems}


@app.post("/api/providers")
async def provider_create(body: ProviderBody) -> Any:
    eng = engine()
    draft = _draft(body)
    draft.key = eng.free_key(draft.key)
    if draft.options.get("secret"):
        if not body.api_key:
            raise HTTPException(400, "this provider needs an API key")
        secrets.put(draft.key, body.api_key)
    graph = draft.options.pop("workflow_graph", None)
    if isinstance(graph, dict) and graph:
        # the workflow is the model: kept in eki's folder, bound once here
        draft.options["workflow"] = workflow_mod.save(draft.key, graph)
        draft.options["bindings"] = workflow_mod.infer(graph).as_dict()
        draft.options.setdefault("title", draft.label)
        draft.runtime.setdefault("kind", "image")
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
    if body.options is not None:
        refused = sorted(set(body.options) - set(TUNABLE.get(p.kind, ())))
        if refused:
            raise HTTPException(400, f"can't change {', '.join(refused)} on a {p.kind} provider")
        for k, v in body.options.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                raise HTTPException(400, f"{k} must be a number")
            p.options[k] = v
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
    repo: str = ""
    label: str = ""
    quant: str = ""                                 # a GGUF quantisation, e.g. Q4_K_M
    path: str = ""                                  # a .gguf or MLX folder already on disk


class IdentifyBody(BaseModel):
    text: str


@app.get("/api/catalog")
async def catalog_search(q: str = "", limit: int = 30, min_b: float = 0.0, max_b: float = 0.0,
                         format: str = "mlx") -> Any:
    try:
        return {"models": await deploy_mod.search(q, limit, min_b=min_b, max_b=max_b, fmt=format)}
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, f"Hugging Face: {e}")


@app.get("/api/catalog/suggest")
async def catalog_suggest(fresh: bool = False) -> Any:
    """Which models are worth downloading on this Mac (see eki/suggest.py)."""
    eng = engine()
    mem = eng.models.memory()
    installed = [str(p.options.get("model")) for p in eng.providers.all()
                 if p.kind == "mlx" and p.options.get("model")]
    try:
        return await suggest_mod.get(mem.ceiling_gb, mem.free_gb, installed, fresh=fresh)
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, f"Hugging Face: {e}")


@app.get("/api/engines")
def engines_list() -> Any:
    """The programs that serve models, and whether eki has fetched them."""
    return {"engines": engines_mod.statuses()}


@app.post("/api/engines/{name}/install")
async def engine_install(name: str) -> Any:
    if name not in engines_mod.ENGINES:
        raise HTTPException(404, "no such engine")
    return await engine().install_engine(name)


@app.delete("/api/engines/{name}")
def engine_remove(name: str) -> Any:
    if name not in engines_mod.ENGINES:
        raise HTTPException(404, "no such engine")
    eng = engine()
    using = [p.key for p in eng.providers.all() if p.runtime.get("engine") == name]
    if using:
        raise HTTPException(409, f"still used by {', '.join(using)}")
    engines_mod.get(name).remove()
    return {"message": f"{engines_mod.get(name).info.title} removed"}


@app.get("/api/catalog/fit")
async def catalog_fit(repo: str, quant: str = "") -> Any:
    try:
        d = await deploy_mod.details(repo, quant=quant)
    except Exception as e:                          # noqa: BLE001
        raise HTTPException(502, f"Hugging Face: {e}")
    mem = engine().models.memory()
    room = deploy_mod.fit(d, mem.free_gb, mem.ceiling_gb)
    prof = profile_mod.from_hub(repo, d).as_dict()
    d.pop("config", None)
    return {**d, **room, "profile": prof}


@app.post("/api/deploy")
async def deploy_model(body: DeployBody) -> Any:
    if body.path:
        return await engine().deploy("", body.label, path=body.path)
    if "/" not in body.repo:
        raise HTTPException(400, "a Hugging Face repo looks like org/name")
    return await engine().deploy(body.repo, body.label, body.quant)


@app.post("/api/identify")
async def identify_anything(body: IdentifyBody) -> Any:
    """A link, a repo id, a file, a folder or a server address → what it
    is and what eki would do with it (see eki/identify.py)."""
    mem = engine().models.memory()
    return await identify_mod.identify(body.text, mem.free_gb, mem.ceiling_gb)


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
    before = settings_mod.load()
    saved = settings_mod.save(body)
    eng = engine()
    await eng.reload()                  # the router model may have changed
    if before.get("codex_web_search") != saved.get("codex_web_search"):
        await asyncio.to_thread(mcpregistry.render_codex)
    if any(before.get(k) != saved.get(k) for k in ("claude_tools", "claude_screen", "claude_system_prompt")):
        # what a session is given is decided at its start: idle ones are
        # closed so the next turn opens with the new tools (resumed by id)
        await eng.close_idle_claude()
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


class ContextBody(BaseModel):
    tokens: int = 0                                 # 0 = worked out by eki


@app.put("/api/models/{key}/context")
async def set_model_context(key: str, body: ContextBody) -> Any:
    """Pin a local model's context window, or hand it back to eki (0).
    Saved with its provider; the harnesses are told on their next session."""
    eng = engine()
    model = eng.models.get(key)
    if model is None:
        raise HTTPException(404, "no such model")
    if body.tokens < 0 or body.tokens > 2_000_000:
        raise HTTPException(422, "a token count, or 0 for automatic")
    p = eng.providers.get(model.backend or key)
    if p is None:
        raise HTTPException(404, "no such provider")
    if body.tokens:
        p.runtime["context_pin"] = int(body.tokens)
    else:
        p.runtime.pop("context_pin", None)
    eng.providers.upsert(p)
    await eng.reload()
    fresh = eng.models.get(key)
    window = (fresh.context if fresh else None) or {}
    return {"message": window.get("summary") or f"{model.label}: context set"}


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
    from . import builds
    return {"ok": True, "backends": len(eng.backends), "running": eng.runner.running,
            "build": builds.running().get("id", "dev"), "pid": os.getpid()}


def _go_with_launcher() -> None:
    """Started by the eki app (eki/launcher.swift): if it goes, so does the
    engine — a stray engine would hold the port the next one needs."""
    import threading
    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(2)
            if os.getppid() != parent:
                os._exit(0)
    threading.Thread(target=watch, daemon=True, name="launcher-watch").start()


def _managed() -> bool:
    """Started by launchd as eki's engine (or the drill's), not by hand."""
    return bool(os.environ.get("EKI_LAUNCHER")) or \
        os.environ.get("XPC_SERVICE_NAME", "").startswith("local.eki.")


def _eki_engine(pid: int) -> bool:
    """`pid` is an eki engine: `eki serve`, or eki.service run itself."""
    import subprocess
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return ("eki.cli" in cmd and " serve" in cmd) or "eki.service" in cmd


def _holding(host: str, port: int) -> List[int]:
    """Who holds the port: the pid its /api/health gives — and, for an
    engine too stuck to answer, whoever listens there."""
    import subprocess
    pids: List[int] = []
    try:
        pid = int(httpx.get(f"http://{host}:{port}/api/health", timeout=3).json().get("pid") or 0)
        if pid:
            pids.append(pid)
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        pids += [int(x) for x in out if x.isdigit() and int(x) not in pids]
    except (OSError, subprocess.SubprocessError):
        pass
    return [p for p in pids if p != os.getpid()]


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.1)
    return False


def _port_taken(host: str, port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def take_port(host: str, port: int, grace: float = 10.0) -> List[int]:
    """An older eki engine still on the port — its launcher killed, left
    behind (2026-09-24, 22:46–22:56: every new engine died with "address
    already in use" until a person killed it) — is stopped before this one
    binds: TERM, then KILL. Anything else on the port is left alone, and
    this engine fails to bind as before. The pids stopped."""
    import signal
    if not _port_taken(host, port):
        return []
    stopped = []
    for pid in _holding(host, port):
        if not _eki_engine(pid):
            continue
        log.warning("an older engine (pid %d) holds %s:%d; stopping it", pid, host, port)
        for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
            try:
                os.kill(pid, sig)
            except OSError:
                break                       # gone already, or not ours to stop
            if _gone_within(pid, wait):
                break
        stopped.append(pid)
    deadline = time.time() + 5
    while stopped and _port_taken(host, port) and time.time() < deadline:
        time.sleep(0.1)
    return stopped


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="eki-service")
    ap.add_argument("-c", "--config", default=str(config_mod.default_path()))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if os.environ.get("EKI_LAUNCHER"):
        _go_with_launcher()
    if _managed():
        take_port(args.host, args.port)     # before taking up anyone's work
    for note in migrate.run(Path(__file__).resolve().parent.parent):
        log.info("migrated: %s", note)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    STATE["engine"] = Engine(config_mod.load(args.config), owner=True, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
