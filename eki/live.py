# SPDX-License-Identifier: Apache-2.0
"""Claude Code kept open under eki's own interface.

The CLI has a two-way streaming mode — the one its SDK uses — in which the
program stays up between turns and talks JSON lines on stdin and stdout:
user messages go in; text, tool activity and results come out; and when
Claude wants something from you — an answer to a question it asked, or
permission for a command — a `control_request` arrives and waits for a
`control_response`. Slash commands are ordinary messages. Everything the
terminal shows can therefore be shown by eki instead, drawn its own way,
while the genuine program does the work on its own login.

One session per conversation, kept open while the engine runs; a thread
reopened later resumes the same conversation by its session id. The program
runs as a worker (eki/workers.py): the engine restarting leaves it working,
and the next engine takes the session up again where its reading stood
(`attach`).

Nothing here reads or forwards the program's credentials; eki only runs it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from . import steps
from . import workers

log = logging.getLogger("eki.live")

#: tools that ask *you* something, rather than ask permission to do something
QUESTION_TOOLS = {"AskUserQuestion"}
#: slash commands that only make sense in a terminal: not offered
TERMINAL_ONLY_DEFAULT = {"doctor", "color", "reload-plugins", "vim", "terminal-setup", "exit",
                         "quit", "login", "logout", "resume", "theme"}
INIT_TIMEOUT = 60.0
IDLE_SECONDS = 30 * 60


class LiveSession:
    def __init__(self, argv: List[str], cwd: Optional[str], env: Optional[Dict[str, str]],
                 append_system_prompt: str = "", bridge: Any = None,
                 extra_servers: Optional[Dict[str, Dict[str, Any]]] = None):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.append_system_prompt = append_system_prompt
        #: servers added right after the handshake (mcp_set_servers) rather
        #: than by config — the program's own computer-use server keeps its
        #: reserved name that way
        self.extra_servers = dict(extra_servers or {})
        #: eki's own tools, served to the program in-process (see eki/mcpbridge.py):
        #: the program sends each MCP message as a control request and waits
        #: for the reply, so no second process and no port
        self.bridge = bridge
        #: what the handshake said about the account and the program
        self.models: List[Dict[str, Any]] = []
        self.account: Dict[str, Any] = {}
        self.agents: List[Dict[str, Any]] = []
        self.output_style: str = ""
        self.output_styles: List[str] = []
        #: from the init event: the servers, the tools, what the build can do
        self.mcp_servers: List[Dict[str, Any]] = []
        self.tools: List[str] = []
        self.skills: List[str] = []
        self.capabilities: List[str] = []
        self.version: str = ""
        self.cwd_reported: str = ""
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.session_id: str = ""
        self.model: str = ""
        #: background tasks the program has going, by id — a download it
        #: kicked off, a subagent — the reason a thread may stir on its own
        self.background: Dict[str, str] = {}
        #: what Claude Code reports for the model once a turn has finished;
        #: until then, what its family is known to have
        self.context_window: int = 200_000
        self.permission_mode: str = ""
        self.commands: List[Dict[str, Any]] = []
        self.rate_limits: Dict[str, Any] = {}
        self.last_used = time.time()
        self.busy = False
        #: what the worker serves, written in its folder: thread, backend, run
        self.worker_meta: Dict[str, Any] = {}
        self._reading = workers.Reading(None)
        self._events: asyncio.Queue = workers.Tagged()
        self._waiting: Dict[str, asyncio.Future] = {}        # our control requests
        self._pending: Dict[str, Dict[str, Any]] = {}        # its requests to us
        self._reader: Optional[asyncio.Task] = None
        self._got_delta = False
        self._terminal_only = set(TERMINAL_ONLY_DEFAULT)
        self._init_seen: Optional[asyncio.Event] = None
        self.exit_error: str = ""

    # ---- lifecycle ---------------------------------------------------

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    @property
    def worker(self) -> Optional[workers.Worker]:
        return getattr(self.proc, "worker", None)

    def serve(self, **fields: Any) -> None:
        """Say in the worker's folder what it works for now (the run of a
        turn), so a new engine knows whose it is."""
        self.worker_meta.update(fields)
        if self.worker is not None:
            try:
                self.worker.update(**fields)
            except OSError:
                pass

    async def start(self) -> None:
        self.proc = await workers.start_proc(self.argv, self.cwd, self.env, kind="claude",
                                             **self.worker_meta)
        self._reading = workers.Reading(self.proc)
        self._init_seen = asyncio.Event()
        self._reader = asyncio.create_task(self._pump())
        init: Dict[str, Any] = {"subtype": "initialize"}
        if self.append_system_prompt.strip():
            init["appendSystemPrompt"] = self.append_system_prompt.strip()
        if self.bridge is not None:
            init["sdkMcpServers"] = [self.bridge.name]
        try:
            reply = await asyncio.wait_for(self._request(init), timeout=INIT_TIMEOUT)
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError("Claude Code didn't answer the handshake"
                               + (f": {self.exit_error}" if self.exit_error else ""))
        except RuntimeError as e:
            # gone before it read the handshake (a session that can't be
            # resumed): what it said on the way out is the reason, not the pipe
            await self._last_words()
            await self.close()
            raise RuntimeError(self.exit_error or str(e)) from None
        reply = reply if isinstance(reply, dict) else {}
        self.commands = [c for c in reply.get("commands") or []
                         if isinstance(c, dict) and c.get("name")]
        self.models = [m for m in reply.get("models") or [] if isinstance(m, dict) and m.get("value")]
        self.account = reply.get("account") if isinstance(reply.get("account"), dict) else {}
        self.agents = [a for a in reply.get("agents") or [] if isinstance(a, dict)]
        self.output_style = str(reply.get("output_style") or "")
        self.output_styles = [str(s) for s in reply.get("available_output_styles") or []]
        try:
            # the init event follows with the session id and which commands
            # are terminal-only; it's quick, and worth having before the first turn
            await asyncio.wait_for(self._init_seen.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        self.commands = [c for c in self.commands if c["name"] not in self._terminal_only]
        if self.extra_servers:
            try:
                await self.mcp_set_servers({})
            except (RuntimeError, asyncio.TimeoutError) as e:
                log.warning("extra MCP servers: %s", e)

    async def _last_words(self, wait: float = 5.0) -> None:
        """Let the reader finish with a program that has exited, so
        `exit_error` holds what it wrote to stderr."""
        if self._reader and not self._reader.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._reader), timeout=wait)
            except (asyncio.TimeoutError, Exception):       # noqa: BLE001
                pass

    @classmethod
    def attach(cls, worker: workers.Worker, bridge: Any = None,
               extra_servers: Optional[Dict[str, Dict[str, Any]]] = None) -> "LiveSession":
        """A session a previous engine started, still working: read on from
        where that engine's reading stood — no line twice, and what the
        program still waits on (a permission, a tool call) asked again."""
        spec = worker.spec
        session = cls(list(spec.get("argv") or []), spec.get("cwd") or None, None,
                      bridge=bridge, extra_servers=extra_servers)
        session.worker_meta = {k: spec[k] for k in ("thread", "backend", "run") if spec.get(k)}
        session.session_id = str(spec.get("session") or "")
        session.proc = workers.attach(worker)
        session._reading = workers.Reading(session.proc)
        # perhaps mid-message: its deltas were read, so the whole message
        # that closes it isn't text again
        session._got_delta = True
        session._reader = asyncio.get_running_loop().create_task(session._pump())
        return session

    async def close(self) -> None:
        """Ended on purpose: the program goes (it would never see an end of
        input — its worker keeps that open across restarts)."""
        if self._reader:
            self._reader.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.stdin.close()
            except Exception:                       # noqa: BLE001
                pass
            if getattr(self.proc, "detached", False):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
        if getattr(self.proc, "detached", False):
            self.proc.detach()
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(RuntimeError("session closed"))

    async def detach(self) -> None:
        """The engine is going away: stop reading, leave the program working
        for the next engine to take up (`attach`)."""
        if self._reader:
            self._reader.cancel()
        if getattr(self.proc, "detached", False):
            self.proc.detach()
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(RuntimeError("the engine is restarting"))

    # ---- talking -----------------------------------------------------

    def _write(self, obj: Dict[str, Any]) -> None:
        if not self.alive:
            raise RuntimeError("Claude Code is not running" +
                               (f": {self.exit_error}" if self.exit_error else ""))
        try:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        except workers.InputStalled as e:
            # cut off, not failed: its step is taken up again (eki/steps.py)
            raise steps.Interrupted(f"Claude Code stopped reading: {e}") from None
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"Claude Code is not reading: {e}")

    async def _request(self, request: Dict[str, Any]) -> Any:
        rid = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        try:
            self._write({"type": "control_request", "request_id": rid, "request": request})
            return await fut
        finally:
            self._waiting.pop(rid, None)

    async def send(self, text: str, images: Optional[List[str]] = None) -> None:
        """A user turn — with pictures, if any were attached (paths on this
        Mac, sent as image blocks the way a paste in the terminal is); the
        events that follow come from `turn()`."""
        self.last_used = time.time()
        content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for path in images or []:
            block = image_source(path)
            if block:
                content.append(block)
        # stamped with an id of ours: the one /rewind takes to put the files
        # back as they were before this turn (the program doesn't echo it)
        mid = str(uuid.uuid4())
        self._write({"type": "user", "uuid": mid, "session_id": self.session_id or "",
                     "message": {"role": "user", "content": content},
                     "parent_tool_use_id": None})
        self._events.put_nowait({"kind": "checkpoint", "uuid": mid})

    async def answer(self, request_id: str, response: Dict[str, Any]) -> bool:
        """Reply to a question or permission prompt. `response` is what the
        program's own callback would return: {"behavior": "allow", ...} or
        {"behavior": "deny", "message": ...}."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        self._reading.paid(request_id)
        body = dict(response)
        if pending.get("tool_use_id") and "toolUseID" not in body:
            body["toolUseID"] = pending["tool_use_id"]
        self._write({"type": "control_response",
                     "response": {"subtype": "success", "request_id": request_id,
                                  "response": body}})
        return True

    async def interrupt(self) -> None:
        try:
            await asyncio.wait_for(self._request({"subtype": "interrupt"}), timeout=10)
        except (asyncio.TimeoutError, RuntimeError):
            pass

    async def set_model(self, model: str) -> None:
        await self._request({"subtype": "set_model", "model": model})
        self.model = model

    def pending(self) -> List[Dict[str, Any]]:
        return list(self._pending.values())

    # ---- the rest of what the terminal can ask the program ------------
    #
    # Each of these is one control request, the same ones the program's
    # own SDK sends; the terminal's panels (/mcp, /model, /permissions,
    # /usage, /context, /rewind …) are drawn from their replies. A request
    # the running build doesn't know answers with an error, which comes
    # back as RuntimeError — the caller says so rather than guessing.

    async def control(self, request: Dict[str, Any], timeout: float = 30.0) -> Any:
        """Any control request, answered or raised."""
        return await asyncio.wait_for(self._request(request), timeout=timeout)

    async def mcp_status(self) -> List[Dict[str, Any]]:
        reply = await self.control({"subtype": "mcp_status"})
        servers = (reply or {}).get("mcpServers") if isinstance(reply, dict) else None
        if isinstance(servers, list):
            self.mcp_servers = [s for s in servers if isinstance(s, dict) and s.get("name")]
        return list(self.mcp_servers)

    async def mcp_toggle(self, name: str, enabled: bool) -> Any:
        return await self.control({"subtype": "mcp_toggle", "serverName": name, "enabled": bool(enabled)})

    async def mcp_reconnect(self, name: str) -> Any:
        return await self.control({"subtype": "mcp_reconnect", "serverName": name}, timeout=60)

    async def mcp_authenticate(self, name: str, redirect_uri: str = "") -> Any:
        """Start the server's OAuth flow. The program opens the browser (or
        answers with the URL to open) and finishes on the callback; what it
        answers is returned as is, since builds differ in what they say."""
        req: Dict[str, Any] = {"subtype": "mcp_authenticate", "serverName": name}
        if redirect_uri:
            req["redirectUri"] = redirect_uri
        return await self.control(req, timeout=300)

    async def mcp_oauth_callback(self, name: str, callback_url: str) -> Any:
        return await self.control({"subtype": "mcp_oauth_callback_url", "serverName": name,
                                   "callbackUrl": callback_url}, timeout=60)

    async def mcp_clear_auth(self, name: str) -> Any:
        return await self.control({"subtype": "mcp_clear_auth", "serverName": name})

    async def mcp_set_servers(self, servers: Dict[str, Dict[str, Any]]) -> Any:
        """Add or replace process-transport servers for this session only
        (`dynamic` scope); eki's in-process server is re-listed so it stays."""
        merged = {**self.extra_servers, **servers}
        if self.bridge is not None:
            merged[self.bridge.name] = {"type": "sdk", "name": self.bridge.name}
        return await self.control({"subtype": "mcp_set_servers", "servers": merged}, timeout=60)

    async def set_permission_mode(self, mode: str) -> None:
        await self.control({"subtype": "set_permission_mode", "mode": mode})
        self.permission_mode = mode

    async def set_thinking(self, max_tokens: Optional[int] = None, display: Optional[str] = None) -> Any:
        req: Dict[str, Any] = {"subtype": "set_max_thinking_tokens", "max_thinking_tokens": max_tokens}
        if display:
            req["thinking_display"] = display
        return await self.control(req)

    async def usage(self) -> Dict[str, Any]:
        reply = await self.control({"subtype": "get_usage", "skip_behaviors": True}, timeout=60)
        return reply if isinstance(reply, dict) else {}

    async def context_usage(self, detail: str = "summary") -> Dict[str, Any]:
        reply = await self.control({"subtype": "get_context_usage", "detail": detail}, timeout=60)
        return reply if isinstance(reply, dict) else {}

    async def permission_rules(self) -> Dict[str, Any]:
        reply = await self.control({"subtype": "list_permission_rules"})
        return reply if isinstance(reply, dict) else {}

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            reply = await self.control({"subtype": "list_models"})
        except RuntimeError:
            return list(self.models)
        models = (reply or {}).get("models") if isinstance(reply, dict) else None
        if isinstance(models, list):
            self.models = [m for m in models if isinstance(m, dict) and m.get("value")]
        return list(self.models)

    async def rewind_files(self, user_message_id: str, dry_run: bool = False) -> Any:
        return await self.control({"subtype": "rewind_files", "user_message_id": user_message_id,
                                   "dry_run": bool(dry_run)}, timeout=60)

    async def rename(self, title: str) -> Any:
        return await self.control({"subtype": "rename_session", "title": title, "source": "host"})

    async def background_tasks(self) -> Any:
        return await self.control({"subtype": "background_tasks"})

    async def stop_task(self, task_id: str) -> Any:
        return await self.control({"subtype": "stop_task", "task_id": task_id})

    async def reload_skills(self) -> Any:
        return await self.control({"subtype": "reload_skills"}, timeout=60)

    async def settings(self) -> Dict[str, Any]:
        reply = await self.control({"subtype": "get_settings"})
        return reply if isinstance(reply, dict) else {}

    async def update_settings(self, settings: Dict[str, Any], source: str = "userSettings") -> Any:
        return await self.control({"subtype": "update_settings", "source": source, "settings": settings})

    # ---- reading -----------------------------------------------------

    async def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                self._reading.before_line()
                line = await self.proc.stdout.readline()
                if not line:
                    break
                _, end = self._reading.after_line()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if self._reading.replaying() and not (event.get("type") == "control_request"
                                                      and self._reading.again(event.get("request_id"))):
                    continue                        # handled by the engine before this one
                self._events.pos = end              # type: ignore[attr-defined]
                try:
                    self._handle(event)
                except Exception as e:              # noqa: BLE001
                    # one event in a shape eki doesn't know must not end the
                    # session: the turn would hang until it timed out. It is
                    # a fault (eki/observe.py) and reading carries on.
                    log.exception("live reader: %s in a %r event: %s", type(e).__name__,
                                  event.get("type"), json.dumps(event)[:400])
                if self._events.empty():
                    # nothing of it waits to be handled: read past it for good
                    self._reading.handled(end)
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # noqa: BLE001
            log.warning("live reader: %s", e)
        try:
            err = (await self.proc.stderr.read())[-600:].decode("utf-8", "replace").strip()
        except Exception:                           # noqa: BLE001
            err = ""
        self.exit_error = err or f"exited with code {self.proc.returncode}"
        stalled = str(getattr(self.proc, "stalled", "") or "")
        if stalled:
            self.exit_error = f"stopped: {stalled}"
        self._events.put_nowait({"kind": "exit", "error": self.exit_error, "cut_off": bool(stalled)})
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(RuntimeError(self.exit_error))

    def _handle(self, event: Dict[str, Any]) -> None:
        t = event.get("type")
        if t == "control_response":
            resp = event.get("response") or {}
            fut = self._waiting.get(resp.get("request_id", ""))
            if fut and not fut.done():
                if resp.get("subtype") == "success":
                    fut.set_result(resp.get("response"))
                else:
                    fut.set_exception(RuntimeError(str(resp.get("error"))))
            return
        if t == "control_request":
            req = event.get("request") or {}
            rid = event.get("request_id", "")
            sub = req.get("subtype")
            if sub == "mcp_message" and self.bridge is not None \
                    and req.get("server_name") == self.bridge.name:
                # one JSON-RPC message for eki's own tools; answered in a
                # task so a slow tool (a picture) doesn't stall the reader
                self._reading.owe(rid)
                asyncio.get_running_loop().create_task(self._serve_bridge(rid, req.get("message")))
                return
            if sub in ("elicitation", "request_user_dialog", "can_use_tool"):
                self._reading.owe(rid)              # asked again after a restart, until answered
            if sub == "elicitation":
                # an MCP server asking you something: a form, or a URL to
                # visit (its own sign-in, say) — a card either way
                self._pending[rid] = {**req, "request_id": rid}
                self._events.put_nowait({"kind": "elicitation", "request_id": rid,
                                         "server": req.get("mcp_server_name", ""),
                                         "message": req.get("message", ""),
                                         "mode": req.get("mode") or "form",
                                         "url": req.get("url") or "",
                                         "schema": req.get("requested_schema") or {},
                                         "title": req.get("title") or req.get("display_name") or ""})
                return
            if sub == "request_user_dialog":
                self._pending[rid] = {**req, "request_id": rid}
                self._events.put_nowait({"kind": "dialog", "request_id": rid,
                                         "dialog": req.get("dialog_kind", ""),
                                         "payload": req.get("payload") or {},
                                         "tool_use_id": req.get("tool_use_id")})
                return
            if sub == "can_use_tool":
                self._pending[rid] = {**req, "request_id": rid}
                if req.get("tool_name") in QUESTION_TOOLS:
                    self._events.put_nowait({"kind": "ask", "request_id": rid,
                                             "questions": (req.get("input") or {}).get("questions") or [],
                                             "input": req.get("input") or {},
                                             "tool_use_id": req.get("tool_use_id")})
                else:
                    self._events.put_nowait({"kind": "permission", "request_id": rid,
                                             "tool": req.get("tool_name", ""),
                                             "title": req.get("title") or req.get("display_name") or "",
                                             "description": req.get("description") or "",
                                             "input": req.get("input") or {},
                                             "suggestions": req.get("permission_suggestions") or [],
                                             "tool_use_id": req.get("tool_use_id")})
            else:
                # hooks and servers eki doesn't host: say no, politely
                self._write({"type": "control_response",
                             "response": {"subtype": "error", "request_id": rid,
                                          "error": f"eki doesn't handle {sub}"}})
            return
        if t == "control_cancel_request":
            rid = event.get("request_id", "")
            self._reading.paid(rid)
            if self._pending.pop(rid, None) is not None:
                self._events.put_nowait({"kind": "cancel", "request_id": rid})
            return
        if t == "system":
            sub = event.get("subtype")
            if sub == "init":
                self.session_id = event.get("session_id") or self.session_id
                if self.session_id and self.worker_meta.get("session") != self.session_id:
                    self.serve(session=self.session_id)
                self.model = event.get("model") or self.model
                self.context_window = known_window(self.model) or self.context_window
                self.permission_mode = event.get("permissionMode") or ""
                self._terminal_only |= set(event.get("terminal_slash_commands") or [])
                self.commands = [c for c in self.commands if c["name"] not in self._terminal_only]
                servers = event.get("mcp_servers")
                if isinstance(servers, list):
                    self.mcp_servers = [s for s in servers if isinstance(s, dict) and s.get("name")]
                self.tools = [str(x) for x in event.get("tools") or [] if isinstance(x, str)]
                self.skills = [str(x) for x in event.get("skills") or [] if isinstance(x, str)]
                self.capabilities = [str(x) for x in event.get("capabilities") or [] if isinstance(x, str)]
                self.version = str(event.get("claude_code_version") or self.version)
                self.cwd_reported = str(event.get("cwd") or "")
                if self._init_seen:
                    self._init_seen.set()
            elif sub == "mcp_status" or sub == "mcp_servers_changed":
                servers = event.get("mcp_servers") or event.get("mcpServers")
                if isinstance(servers, list):
                    self.mcp_servers = [s for s in servers if isinstance(s, dict) and s.get("name")]
                    self._events.put_nowait({"kind": "mcp", "servers": list(self.mcp_servers)})
            elif sub == "compact_boundary":
                meta = event.get("compact_metadata") or {}
                self._events.put_nowait({"kind": "note",
                                         "text": f"Context compacted ({meta.get('trigger', 'auto')})"})
            # work the program runs in the background — a long download, a
            # subagent — shown as it goes, the way the terminal shows it
            elif sub == "task_started" and not event.get("ambient"):
                what = str(event.get("description") or "a task")
                self.background[str(event.get("task_id"))] = what
                self._events.put_nowait({"kind": "note", "text": f"In the background: {what}"})
            elif sub == "task_progress" and not event.get("ambient"):
                what = str(event.get("summary") or event.get("last_tool_name") or "").strip()
                if what:
                    self._events.put_nowait({"kind": "note",
                                             "text": f"{str(event.get('description') or 'background task')[:60]} — {what[:120]}"})
            elif sub == "task_notification" and not event.get("ambient"):
                self.background.pop(str(event.get("task_id")), None)
                status = str(event.get("status") or "finished")
                summary = str(event.get("summary") or "").strip()
                self._events.put_nowait({"kind": "note",
                                         "text": f"Background task {status}" + (f": {summary[:160]}" if summary else "")})
            elif sub == "background_tasks_changed":
                self.background = {str(t.get("task_id")): str(t.get("description") or "")
                                   for t in (event.get("tasks") or []) if not t.get("ambient")}
            elif sub == "commands_changed" and isinstance(event.get("commands"), list):
                self.commands = [c for c in event["commands"] if isinstance(c, dict) and c.get("name")
                                 and c["name"] not in self._terminal_only]
            return
        if t == "rate_limit_event":
            self.rate_limits = event.get("rate_limit_info") or {}
            self._events.put_nowait({"kind": "rate_limit", "info": self.rate_limits})
            return
        if t == "stream_event":
            inner = event.get("event") or {}
            if inner.get("type") == "message_start":
                self._got_delta = False
            elif inner.get("type") == "content_block_delta":
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    self._got_delta = True
                    self._events.put_nowait({"kind": "text", "text": delta["text"]})
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    # the model thinking, as the terminal shows it in grey
                    self._events.put_nowait({"kind": "thinking", "text": delta["thinking"]})
            return
        if t == "assistant":
            usage = (event.get("message") or {}).get("usage") or {}
            used = sum(int(usage.get(k, 0) or 0) for k in
                       ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                        "output_tokens"))
            if used:
                self._events.put_nowait({"kind": "context", "used": used,
                                         "window": self.context_window})
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text") and not self._got_delta:
                    self._events.put_nowait({"kind": "text", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    self._events.put_nowait({"kind": "activity", "tool": block.get("name", ""),
                                             "input": block.get("input") or {},
                                             "id": block.get("id")})
            self._got_delta = False
            return
        if t == "user":
            blocks = (event.get("message") or {}).get("content") or []
            if not isinstance(blocks, list):
                # a plain string: a local command's echo ("Set model to opus"
                # after a model switch mid-thread) — nothing to show
                return
            # tool results coming back: only errors are worth a line — and
            # the screen tools saying macOS hasn't let them in, which is a
            # card with the settings a click away, not a line to scroll past
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                text = block.get("content")
                if isinstance(text, list):
                    text = " ".join(str(b.get("text", "")) for b in text if isinstance(b, dict))
                text = str(text or "")
                need = permission_needed(text)
                if need:
                    self._events.put_nowait({"kind": "needs_permission", "what": need,
                                             "text": text[:300]})
                if block.get("is_error"):
                    self._events.put_nowait({"kind": "activity", "tool": "error",
                                             "input": {"text": text[:300]}})
            return
        if t == "result":
            for info in (event.get("modelUsage") or {}).values():
                if isinstance(info, dict) and info.get("contextWindow"):
                    self.context_window = int(info["contextWindow"])
            self._events.put_nowait({"kind": "result", "is_error": bool(event.get("is_error")),
                                     "result": event.get("result"), "usage": event.get("usage") or {},
                                     "subtype": event.get("subtype", "")})
            return

    async def _serve_bridge(self, rid: str, message: Any) -> None:
        """Answer one MCP message for eki's in-process server."""
        try:
            reply = await self.bridge.handle(message)
        except Exception as e:                      # noqa: BLE001
            log.warning("eki tools: %s", e)
            reply = {"jsonrpc": "2.0", "id": (message or {}).get("id") if isinstance(message, dict) else None,
                     "error": {"code": -32603, "message": str(e)[:300]}}
        try:
            self._write({"type": "control_response",
                         "response": {"subtype": "success", "request_id": rid,
                                      "response": {"mcp_response": reply}}})
            self._reading.paid(rid)
        except RuntimeError:
            pass

    def stirred(self) -> bool:
        """The program did something while nobody asked — a background task
        finished and it carried on. Those events are a turn of its own."""
        return not self.busy and not self._events.empty()

    async def turn(self, timeout: float = 600.0, until_quiet: float = 0.0
                   ) -> AsyncIterator[Dict[str, Any]]:
        """Events of the turn in progress, up to and including its result.

        While a question or permission is pending the clock stops: the
        program is waiting on you, not stuck. With `until_quiet`, a lull of
        that many seconds ends the turn instead — for a turn the program
        took by itself, which may be a few progress lines and no result.
        """
        self.busy = True
        try:
            while True:
                waiting = bool(self._pending)
                try:
                    ev = await asyncio.wait_for(self._events.get(),
                                                timeout=None if waiting else (until_quiet or timeout))
                except asyncio.TimeoutError:
                    if until_quiet:
                        return
                    raise RuntimeError("Claude Code went quiet for too long")
                self.last_used = time.time()
                pos = ev.pop("_pos", None)
                yield ev
                # handled — the reading moves past its line for good
                self._reading.handled(pos)
                if ev["kind"] in ("result", "exit"):
                    return
        finally:
            self.busy = False


_PERMISSION = re.compile(r"(accessibility|screen recording|screen capture)[^.]{0,80}"
                         r"(not granted|not been granted|is required|permission)", re.I)


def permission_needed(text: str) -> str:
    """"accessibility" or "screen" when a tool result says macOS hasn't
    let the screen tools in; "" otherwise."""
    m = _PERMISSION.search(text or "")
    if not m:
        return ""
    return "screen" if "screen" in m.group(1).lower() else "accessibility"


def image_source(path: str) -> Optional[Dict[str, Any]]:
    """A picture on disk as the API's image block, or None if unreadable."""
    import base64
    try:
        with open(os.path.expanduser(path), "rb") as f:
            data = f.read()
    except OSError:
        return None
    ext = path.rsplit(".", 1)[-1].lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")
    return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                        "data": base64.b64encode(data).decode("ascii")}}


#: context windows by family, for the meter before the first turn reports
#: one; every current Claude family runs at a million tokens
KNOWN_WINDOWS = {"fable": 1_000_000, "opus": 1_000_000, "sonnet": 1_000_000, "haiku": 200_000}


def known_window(model: str) -> int:
    name = (model or "").lower()
    for family, window in KNOWN_WINDOWS.items():
        if family in name:
            return window
    return 0


def summarize_activity(tool: str, inp: Dict[str, Any]) -> str:
    """One line for what a tool call is doing, the way the terminal says it."""
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or ""
    short = path.split("/")[-1] if path else ""
    if tool == "error":
        return "⚠ " + str(inp.get("text", ""))[:200]
    if tool == "approved":
        return "Approved " + str(inp.get("text", ""))[:160]
    if tool in ("Read", "NotebookRead"):
        return f"Reading {short}"
    if tool in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"Editing {short}"
    if tool == "Write":
        return f"Writing {short}"
    if tool == "Bash":
        cmd = str(inp.get("command", ""))
        return "Running " + (cmd if len(cmd) <= 80 else cmd[:77] + "…")
    if tool in ("Glob", "Grep"):
        return f"Searching for {inp.get('pattern', '')}"
    if tool == "WebSearch":
        return f"Searching the web: {inp.get('query', '')}"
    if tool == "WebFetch":
        return f"Fetching {inp.get('url', '')}"
    if tool in ("Task", "Agent"):
        return f"Delegating: {str(inp.get('description') or inp.get('prompt', ''))[:80]}"
    if tool == "TodoWrite":
        return "Updating the task list"
    if tool == "Skill":
        return f"Using skill {inp.get('skill') or inp.get('name', '')}"
    if tool == "AskUserQuestion":
        return "Asking you"
    return tool


_INTENT = re.compile(r"\b(let me|i'll|i will|i am going to|i'm going to|first,? i|now i|next,? i|"
                     r"going to|will now|let's|installing|starting|running|adding|creating|"
                     r"writing|fixing|updating|checking|setting up|wiring|porting|restarting|"
                     r"re-?running|retrying|testing|rebuilding|downloading|patching)\b", re.I)
_DONE = re.compile(r"\b(done|finished|complete|completed|all set|ready to|you can now|"
                   r"is running on|is up and running|nothing (else|more) to do)\b", re.I)


def sounds_unfinished(text: str) -> bool:
    """A turn that announced work instead of finishing it: short, phrased as
    intent or as work in progress, and not a question or a wrap-up. What a
    small model does when it forgets it has hands — or when its tool call
    came out malformed and was dropped."""
    t = text.strip()
    if not t or len(t) > 700 or t.endswith("?"):
        return False
    last = t.splitlines()[-1].strip()
    if _DONE.search(last):
        return False
    return bool(_INTENT.search(last))
