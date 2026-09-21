# SPDX-License-Identifier: Apache-2.0
"""Codex kept open under eki's interface, through its app-server.

`codex app-server` is the program without its screen: JSON-RPC over stdio.
A thread is started (or resumed by id), each message is a turn, and the
server notifies as it goes — text as deltas, each command and file change
as an item — and asks over the same pipe when it wants something: approval
for a command, or an answer to a question it put to you. That is the same
shape as Claude Code's streaming mode (eki/live.py), so eki shows both the
same way: text streaming in, a line per tool call, a card per question or
permission.

Codex's slash commands live in its terminal UI, not in the server; the few
that matter are done here — /compact, /model, /new, /status, /diff — and
anything else typed with a slash is sent to the model as words.

The same session drives eki's own local models (eki/gateway.py): Codex is
pointed at eki as its model provider, and nothing here changes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

log = logging.getLogger("eki.codex_live")

INIT_TIMEOUT = 60.0
#: warnings that say nothing the user can act on
NOISE = ("Model metadata for", "Skill descriptions were shortened", "Under-development features")
COMMANDS = [
    {"name": "compact", "description": "summarise the conversation to free context", "argumentHint": ""},
    {"name": "model", "description": "change the model for the next turns", "argumentHint": "<model>"},
    {"name": "new", "description": "start a fresh thread (context cleared)", "argumentHint": ""},
    {"name": "status", "description": "model, thread and token usage", "argumentHint": ""},
    {"name": "diff", "description": "what this thread has changed", "argumentHint": ""},
    {"name": "review", "description": "review the current changes", "argumentHint": ""},
]


class CodexSession:
    def __init__(self, argv: List[str], cwd: Optional[str], env: Optional[Dict[str, str]],
                 model: str = "", permissions: str = "auto", resume: str = ""):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.model = model
        self.permissions = permissions
        self.resume = resume
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.session_id: str = ""                  # Codex's thread id
        self.turn_id: str = ""
        self.commands: List[Dict[str, Any]] = list(COMMANDS)
        self.rate_limits: Dict[str, Any] = {}
        self.usage: Dict[str, Any] = {}
        self.diff: str = ""
        self.last_used = time.time()
        self.busy = False
        self.exit_error = ""
        self._events: asyncio.Queue = asyncio.Queue()
        self._waiting: Dict[int, asyncio.Future] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._reader: Optional[asyncio.Task] = None
        self._next_id = 0
        self._text_open = False
        self._compacted: Optional[asyncio.Event] = None

    # ---- lifecycle ---------------------------------------------------

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._pump())
        try:
            await asyncio.wait_for(self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"experimentalApi": True},
                "clientInfo": {"name": "eki", "title": "eki", "version": "0.2.0"}}), INIT_TIMEOUT)
            self._notify("initialized", {})
            if self.resume:
                result = await asyncio.wait_for(self._request("thread/resume", {
                    "threadId": self.resume, **self._thread_params()}), INIT_TIMEOUT)
            else:
                result = await asyncio.wait_for(self._request("thread/start", self._thread_params()),
                                                INIT_TIMEOUT)
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError("Codex didn't answer the handshake"
                               + (f": {self.exit_error}" if self.exit_error else ""))
        except RuntimeError as e:
            await self.close()
            raise RuntimeError(str(e))
        thread = (result or {}).get("thread") or {}
        self.session_id = thread.get("id") or (result or {}).get("threadId") or self.resume

    def _thread_params(self) -> Dict[str, Any]:
        auto = self.permissions == "auto"
        params: Dict[str, Any] = {
            "approvalPolicy": "never" if auto else "on-request",
            "sandbox": "danger-full-access" if auto else ("workspace-write" if self.cwd else "read-only"),
        }
        if self.cwd:
            params["cwd"] = self.cwd
        if self.model:
            params["model"] = self.model
        return params

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

    # ---- json-rpc ----------------------------------------------------

    def _write(self, obj: Dict[str, Any]) -> None:
        if not self.alive:
            raise RuntimeError("Codex is not running" + (f": {self.exit_error}" if self.exit_error else ""))
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: Dict[str, Any]) -> Any:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await fut
        finally:
            self._waiting.pop(rid, None)

    def _reply(self, rid: Any, result: Dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    # ---- talking -----------------------------------------------------

    async def set_model(self, model: str) -> None:
        self.model = model

    async def send(self, text: str) -> None:
        """A user turn — or one of eki's few slash commands for Codex."""
        self.last_used = time.time()
        stripped = text.strip()
        if stripped.startswith("/"):
            name, _, arg = stripped[1:].partition(" ")
            handled = await self._slash(name.lower(), arg.strip())
            if handled:
                return
        params: Dict[str, Any] = {"threadId": self.session_id,
                                  "input": [{"type": "text", "text": text}]}
        if self.model:
            params["model"] = self.model
        if self.cwd:
            params["cwd"] = self.cwd
        result = await self._request("turn/start", params)
        self.turn_id = ((result or {}).get("turn") or {}).get("id", "")

    async def _slash(self, name: str, arg: str) -> bool:
        if name == "compact":
            self._compacted = asyncio.Event()
            await self._request("thread/compact/start", {"threadId": self.session_id})
            try:
                await asyncio.wait_for(self._compacted.wait(), timeout=120)
                self._events.put_nowait({"kind": "note", "text": "Context compacted"})
            except asyncio.TimeoutError:
                self._events.put_nowait({"kind": "note", "text": "Compaction requested"})
        elif name == "model":
            if arg:
                self.model = arg
                self._events.put_nowait({"kind": "text", "text": f"Model for the next turns: {arg}"})
            else:
                self._events.put_nowait({"kind": "text", "text": f"Model: {self.model or 'Codex default'}"})
        elif name == "new":
            result = await self._request("thread/start", self._thread_params())
            self.session_id = ((result or {}).get("thread") or {}).get("id") or self.session_id
            self.usage, self.diff = {}, ""
            self._events.put_nowait({"kind": "note", "text": "New thread — context cleared"})
        elif name == "status":
            total = (self.usage.get("total") or {})
            lines = [f"**Model:** {self.model or 'Codex default'}",
                     f"**Thread:** `{self.session_id}`"]
            if total:
                lines.append(f"**Tokens:** {total.get('totalTokens', 0):,} total — "
                             f"{total.get('inputTokens', 0):,} in, {total.get('outputTokens', 0):,} out")
            rl = (self.rate_limits.get("primary") or {})
            if rl:
                lines.append(f"**Limit:** {rl.get('usedPercent', 0)}% of the "
                             f"{int((rl.get('windowDurationMins') or 0) / 60 / 24)}-day window used")
            self._events.put_nowait({"kind": "text", "text": "\n".join(lines)})
        elif name == "diff":
            self._events.put_nowait({"kind": "text", "text": f"```diff\n{self.diff}\n```" if self.diff
                                     else "Nothing changed in this thread yet."})
        elif name == "review":
            await self._request("review/start", {"threadId": self.session_id,
                                                 "target": {"type": "uncommittedChanges"}})
            return True                             # its events come like a turn's
        else:
            return False
        self._events.put_nowait({"kind": "result", "is_error": False, "result": "", "usage": {}})
        return True

    async def answer(self, request_id: str, response: Dict[str, Any]) -> bool:
        """The user's answer, in the shape eki's cards produce (the same
        shape Claude Code takes), translated to what Codex asked for."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        rid, method, params = pending["id"], pending["method"], pending["params"]
        allow = response.get("behavior", "allow") == "allow"
        if method == "item/tool/requestUserInput":
            given = ((response.get("updatedInput") or {}).get("answers") or {})
            answers = {}
            for q in params.get("questions") or []:
                got = given.get(q.get("question", "")) or given.get(q.get("id", ""))
                if got is None:
                    continue
                answers[q["id"]] = {"answers": got if isinstance(got, list) else [str(got)]}
            self._reply(rid, {"answers": answers})
        elif method in ("item/commandExecution/requestApproval", "execCommandApproval"):
            always = bool(response.get("updatedPermissions"))
            self._reply(rid, {"decision": ("acceptForSession" if always else "accept") if allow else "decline"})
        elif method in ("item/fileChange/requestApproval", "applyPatchApproval"):
            always = bool(response.get("updatedPermissions"))
            self._reply(rid, {"decision": ("acceptForSession" if always else "accept") if allow else "decline"})
        elif method == "item/permissions/requestApproval":
            self._reply(rid, {"permissions": params.get("permissions") if allow else {}, "scope": "turn"})
        else:
            self._reply(rid, {})
        return True

    async def interrupt(self) -> None:
        if not self.turn_id:
            return
        try:
            await asyncio.wait_for(self._request("turn/interrupt", {
                "threadId": self.session_id, "turnId": self.turn_id}), timeout=10)
        except (asyncio.TimeoutError, RuntimeError):
            pass

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
                    msg = json.loads(line)
                except ValueError:
                    continue
                self._handle(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # noqa: BLE001
            log.warning("codex reader: %s", e)
        try:
            err = (await self.proc.stderr.read())[-600:].decode("utf-8", "replace").strip()
        except Exception:                           # noqa: BLE001
            err = ""
        self.exit_error = err or f"exited with code {self.proc.returncode}"
        self._events.put_nowait({"kind": "exit", "error": self.exit_error})
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(RuntimeError(self.exit_error))

    def _handle(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method")
        if method is None and "id" in msg:                         # a response to us
            fut = self._waiting.get(msg["id"])
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(str((msg["error"] or {}).get("message") or msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return
        params = msg.get("params") or {}
        if "id" in msg:                                            # a request to us
            self._server_request(msg["id"], method or "", params)
            return
        # notifications
        if method == "item/agentMessage/delta":
            delta = params.get("delta") or ""
            if delta:
                if not self._text_open and self._had_text:
                    delta = "\n\n" + delta
                self._text_open = True
                self._had_text = True
                self._events.put_nowait({"kind": "text", "text": delta})
        elif method == "item/started":
            item = params.get("item") or {}
            kind = item.get("type")
            if kind == "agentMessage":
                self._text_open = False
            elif kind == "commandExecution":
                self._events.put_nowait({"kind": "activity", "tool": "Bash",
                                         "input": {"command": _plain_command(item.get("command", ""))}})
            elif kind == "fileChange":
                for change in item.get("changes") or []:
                    self._events.put_nowait({"kind": "activity", "tool": "Edit",
                                             "input": {"file_path": change.get("path", "")}})
            elif kind == "webSearch":
                self._events.put_nowait({"kind": "activity", "tool": "WebSearch",
                                         "input": {"query": item.get("query", "")}})
            elif kind == "mcpToolCall":
                self._events.put_nowait({"kind": "activity", "tool": item.get("tool") or "tool", "input": {}})
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                self._text_open = False
                if not self._had_text and item.get("text"):
                    # no deltas came (a resumed or replayed turn): the whole thing
                    self._had_text = True
                    self._events.put_nowait({"kind": "text", "text": item["text"]})
            elif item.get("type") == "commandExecution" and item.get("status") == "failed":
                out = str(item.get("aggregatedOutput") or "")[-200:]
                self._events.put_nowait({"kind": "activity", "tool": "error",
                                         "input": {"text": f"command failed: {out or item.get('command', '')}"}})
        elif method == "turn/started":
            self.turn_id = ((params.get("turn") or {}).get("id")) or self.turn_id
            self._had_text = False
            self._text_open = False
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            error = turn.get("error")
            self._events.put_nowait({"kind": "result", "is_error": bool(error),
                                     "result": (error or {}).get("message") if isinstance(error, dict) else error,
                                     "usage": _usage(self.usage), "subtype": turn.get("status", "")})
        elif method == "thread/tokenUsage/updated":
            self.usage = params.get("tokenUsage") or {}
        elif method == "turn/diff/updated":
            self.diff = params.get("diff") or ""
        elif method == "thread/compacted":
            if self._compacted:
                self._compacted.set()
        elif method == "account/rateLimits/updated":
            self.rate_limits = params.get("rateLimits") or {}
            self._events.put_nowait({"kind": "rate_limit", "info": self.rate_limits})
        elif method == "model/rerouted":
            self._events.put_nowait({"kind": "note", "text": f"Model rerouted to {params.get('toModel') or params.get('model', '?')}"})
        elif method in ("error", "warning"):
            text = str((params.get("error") or params.get("message") or params.get("warning") or params))[:300]
            if not any(noise in text for noise in NOISE):
                self._events.put_nowait({"kind": "activity", "tool": "error", "input": {"text": text}})
        elif method == "thread/started":
            thread = params.get("thread") or {}
            self.session_id = thread.get("id") or self.session_id

    _had_text = False

    def _server_request(self, rid: Any, method: str, params: Dict[str, Any]) -> None:
        key = f"{method}#{rid}"
        self._pending[key] = {"id": rid, "method": method, "params": params, "request_id": key}
        if method == "item/tool/requestUserInput":
            questions = [{"question": q.get("question", ""), "header": q.get("header", ""),
                          "multiSelect": False,
                          "options": [{"label": o.get("label", ""), "description": o.get("description", "")}
                                      for o in q.get("options") or []]}
                         for q in params.get("questions") or []]
            self._events.put_nowait({"kind": "ask", "request_id": key, "questions": questions,
                                     "input": {"questions": questions}, "tool_use_id": params.get("itemId")})
        elif method in ("item/commandExecution/requestApproval", "execCommandApproval"):
            self._events.put_nowait({"kind": "permission", "request_id": key, "tool": "Bash",
                                     "title": "Run a command",
                                     "description": params.get("reason") or "",
                                     "input": {"command": _plain_command(params.get("command") or "")},
                                     "suggestions": ["acceptForSession"], "tool_use_id": params.get("itemId")})
        elif method in ("item/fileChange/requestApproval", "applyPatchApproval"):
            paths = [c.get("path", "") for c in params.get("changes") or []]
            self._events.put_nowait({"kind": "permission", "request_id": key, "tool": "Edit",
                                     "title": "Change files",
                                     "description": params.get("reason") or "",
                                     "input": {"file_path": ", ".join(paths)},
                                     "suggestions": ["acceptForSession"], "tool_use_id": params.get("itemId")})
        elif method == "item/permissions/requestApproval":
            self._events.put_nowait({"kind": "permission", "request_id": key, "tool": "Permissions",
                                     "title": "Codex asks for more permissions",
                                     "description": params.get("reason") or "",
                                     "input": {"permissions": json.dumps(params.get("permissions") or {})[:300]},
                                     "suggestions": [], "tool_use_id": params.get("itemId")})
        else:
            # elicitations, dynamic tools, attestation: not hosted here
            self._pending.pop(key, None)
            self._write({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601, "message": f"eki doesn't handle {method}"}})

    async def turn(self, timeout: float = 600.0) -> AsyncIterator[Dict[str, Any]]:
        self.busy = True
        try:
            while True:
                waiting = bool(self._pending)
                try:
                    ev = await asyncio.wait_for(self._events.get(), timeout=None if waiting else timeout)
                except asyncio.TimeoutError:
                    raise RuntimeError("Codex went quiet for too long")
                self.last_used = time.time()
                yield ev
                if ev["kind"] in ("result", "exit"):
                    return
        finally:
            self.busy = False


def _plain_command(command: Any) -> str:
    """"/bin/zsh -lc 'ls -la'" → "ls -la"; a list → joined."""
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    s = str(command)
    for shell in ("/bin/zsh -lc ", "/bin/bash -lc ", "bash -lc ", "zsh -lc ", "sh -c "):
        if s.startswith(shell):
            s = s[len(shell):]
            if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
                s = s[1:-1]
            break
    return s


def _usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    last = usage.get("last") or usage.get("total") or {}
    return {"input_tokens": int(last.get("inputTokens", 0)),
            "output_tokens": int(last.get("outputTokens", 0))}
