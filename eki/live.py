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
reopened later resumes the same conversation by its session id.

Nothing here reads or forwards the program's credentials; eki only runs it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

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
                 append_system_prompt: str = ""):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.append_system_prompt = append_system_prompt
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.session_id: str = ""
        self.model: str = ""
        self.permission_mode: str = ""
        self.commands: List[Dict[str, Any]] = []
        self.rate_limits: Dict[str, Any] = {}
        self.last_used = time.time()
        self.busy = False
        self._events: asyncio.Queue = asyncio.Queue()
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

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._init_seen = asyncio.Event()
        self._reader = asyncio.create_task(self._pump())
        init: Dict[str, Any] = {"subtype": "initialize"}
        if self.append_system_prompt.strip():
            init["appendSystemPrompt"] = self.append_system_prompt.strip()
        try:
            reply = await asyncio.wait_for(self._request(init), timeout=INIT_TIMEOUT)
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError("Claude Code didn't answer the handshake"
                               + (f": {self.exit_error}" if self.exit_error else ""))
        self.commands = [c for c in (reply or {}).get("commands") or []
                         if isinstance(c, dict) and c.get("name")]
        try:
            # the init event follows with the session id and which commands
            # are terminal-only; it's quick, and worth having before the first turn
            await asyncio.wait_for(self._init_seen.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        self.commands = [c for c in self.commands if c["name"] not in self._terminal_only]

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.stdin.close()
            except Exception:                       # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(RuntimeError("session closed"))

    # ---- talking -----------------------------------------------------

    def _write(self, obj: Dict[str, Any]) -> None:
        if not self.alive:
            raise RuntimeError("Claude Code is not running" +
                               (f": {self.exit_error}" if self.exit_error else ""))
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))

    async def _request(self, request: Dict[str, Any]) -> Any:
        rid = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        self._write({"type": "control_request", "request_id": rid, "request": request})
        try:
            return await fut
        finally:
            self._waiting.pop(rid, None)

    async def send(self, text: str) -> None:
        """A user turn; the events that follow come from `turn()`."""
        self.last_used = time.time()
        self._write({"type": "user", "session_id": self.session_id or "",
                     "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                     "parent_tool_use_id": None})

    async def answer(self, request_id: str, response: Dict[str, Any]) -> bool:
        """Reply to a question or permission prompt. `response` is what the
        program's own callback would return: {"behavior": "allow", ...} or
        {"behavior": "deny", "message": ...}."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
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

    # ---- reading -----------------------------------------------------

    async def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                self._handle(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # noqa: BLE001
            log.warning("live reader: %s", e)
        try:
            err = (await self.proc.stderr.read())[-600:].decode("utf-8", "replace").strip()
        except Exception:                           # noqa: BLE001
            err = ""
        self.exit_error = err or f"exited with code {self.proc.returncode}"
        self._events.put_nowait({"kind": "exit", "error": self.exit_error})
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
            if req.get("subtype") == "can_use_tool":
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
                # hooks, MCP, dialogs eki doesn't host: say no, politely
                self._write({"type": "control_response",
                             "response": {"subtype": "error", "request_id": rid,
                                          "error": f"eki doesn't handle {req.get('subtype')}"}})
            return
        if t == "control_cancel_request":
            rid = event.get("request_id", "")
            if self._pending.pop(rid, None) is not None:
                self._events.put_nowait({"kind": "cancel", "request_id": rid})
            return
        if t == "system":
            sub = event.get("subtype")
            if sub == "init":
                self.session_id = event.get("session_id") or self.session_id
                self.model = event.get("model") or self.model
                self.permission_mode = event.get("permissionMode") or ""
                self._terminal_only |= set(event.get("terminal_slash_commands") or [])
                self.commands = [c for c in self.commands if c["name"] not in self._terminal_only]
                if self._init_seen:
                    self._init_seen.set()
            elif sub == "compact_boundary":
                meta = event.get("compact_metadata") or {}
                self._events.put_nowait({"kind": "note",
                                         "text": f"Context compacted ({meta.get('trigger', 'auto')})"})
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
            return
        if t == "assistant":
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
            # tool results coming back: only errors are worth a line
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    text = block.get("content")
                    if isinstance(text, list):
                        text = " ".join(str(b.get("text", "")) for b in text if isinstance(b, dict))
                    self._events.put_nowait({"kind": "activity", "tool": "error",
                                             "input": {"text": str(text)[:300]}})
            return
        if t == "result":
            self._events.put_nowait({"kind": "result", "is_error": bool(event.get("is_error")),
                                     "result": event.get("result"), "usage": event.get("usage") or {},
                                     "subtype": event.get("subtype", "")})
            return

    async def turn(self, timeout: float = 600.0) -> AsyncIterator[Dict[str, Any]]:
        """Events of the turn in progress, up to and including its result.

        While a question or permission is pending the clock stops: the
        program is waiting on you, not stuck.
        """
        self.busy = True
        try:
            while True:
                waiting = bool(self._pending)
                try:
                    ev = await asyncio.wait_for(self._events.get(),
                                                timeout=None if waiting else timeout)
                except asyncio.TimeoutError:
                    raise RuntimeError("Claude Code went quiet for too long")
                self.last_used = time.time()
                yield ev
                if ev["kind"] in ("result", "exit"):
                    return
        finally:
            self.busy = False


def summarize_activity(tool: str, inp: Dict[str, Any]) -> str:
    """One line for what a tool call is doing, the way the terminal says it."""
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or ""
    short = path.split("/")[-1] if path else ""
    if tool == "error":
        return "⚠ " + str(inp.get("text", ""))[:200]
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
