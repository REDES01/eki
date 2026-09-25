"""Codex, the genuine program on your own login, through its app-server.

`codex app-server` speaks JSON-RPC on stdin/stdout — the protocol Codex's
own apps use. eki opens it (`initialize`), starts or resumes the thread,
starts one turn and reads notifications until `turn/completed`. When Codex
asks you something (`item/tool/requestUserInput`) or asks for approval, a
request arrives with an id; it becomes an ask, and the answer goes back as
the reply. With permissions on "auto" (the default: the top of the run
tree may do anything) approvals are granted by eki and noted in the thread.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .base import Emit, Outcome, Turn, find_binary, short, with_history
from .program import Channel, ProgramProvider

APPROVALS = ("item/commandExecution/requestApproval", "execCommandApproval",
             "item/fileChange/requestApproval", "applyPatchApproval", "item/permissions/requestApproval")


class Codex(ProgramProvider):
    kind = "codex"
    interactive = True

    def __init__(self, name: str, cfg: Dict[str, Any]):
        super().__init__(name, cfg)
        self.bin = find_binary(cfg.get("binary", "codex"))

    def available(self) -> tuple:
        return (True, "") if self.bin else (False, "codex is not installed")

    @property
    def auto(self) -> bool:
        return self.cfg.get("permissions", "auto") == "auto"

    def argv(self, turn: Turn) -> List[str]:
        argv = [self.bin or "codex", "-c", "suppress_unstable_features_warning=true",
                "--enable", "default_mode_request_user_input"]
        for flag in turn.extra.get("codex_config") or []:
            argv += ["-c", flag]
        return argv + ["app-server"]

    # ---- json-rpc ------------------------------------------------------------------------

    def _call(self, ch: Channel, method: str, params: Dict[str, Any], purpose: str) -> None:
        ch.state["next"] = ch.state.get("next", 0) + 1
        rid = ch.state["next"]
        ch.state.setdefault("calls", {})[rid] = purpose
        ch.write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})

    def _thread_params(self, turn: Turn) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            # on-request keeps approvals coming as questions; in "auto" eki says yes
            "approvalPolicy": "on-request",
            "sandbox": "danger-full-access" if self.auto else "workspace-write"}
        if turn.cwd:
            params["cwd"] = turn.cwd
        if self.cfg.get("model"):
            params["model"] = self.cfg["model"]
        if turn.extra.get("note"):
            params["developerInstructions"] = turn.extra["note"]
        return params

    def opening(self, ch: Channel, turn: Turn) -> None:
        self._call(ch, "initialize", {"protocolVersion": "2024-11-05",
                                      "capabilities": {"experimentalApi": True},
                                      "clientInfo": {"name": "eki", "title": "eki", "version": "0.3.0"}},
                   "init")

    def read(self, event: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        method = event.get("method")
        if method is None and "id" in event:
            self._replied(event, emit, out, ch)
        elif method and "id" in event:
            self._asked(event["id"], method, event.get("params") or {}, emit, ch)
        elif method:
            self._notified(method, event.get("params") or {}, emit, out, ch)

    def _replied(self, event: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        purpose = ch.state.get("calls", {}).pop(event["id"], "")
        turn = ch.turn
        if "error" in event:
            msg = str((event["error"] or {}).get("message") or event["error"])
            if purpose == "thread" and turn.resume:
                # the session can't be found: start a new one with the thread as context
                emit("note", {"text": "earlier Codex session not found; starting a new one"})
                turn.resume, turn.history = None, turn.extra.get("full_history", turn.history)
                self._call(ch, "thread/start", self._thread_params(turn), "thread")
                return
            out.error, out.finished = short(msg, 400), True
            return
        result = event.get("result") or {}
        if purpose == "init":
            ch.write({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            if turn.resume:
                self._call(ch, "thread/resume", {"threadId": turn.resume, **self._thread_params(turn)}, "thread")
            else:
                self._call(ch, "thread/start", self._thread_params(turn), "thread")
        elif purpose == "thread":
            tid = (result.get("thread") or {}).get("id") or result.get("threadId") or turn.resume
            ch.state["thread"] = tid
            emit("session", {"id": tid})
            params: Dict[str, Any] = {"threadId": tid, "input": [{"type": "text", "text": with_history(turn)}]}
            if turn.cwd:
                params["cwd"] = turn.cwd
            self._call(ch, "turn/start", params, "turn")

    def _notified(self, method: str, params: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        if method == "item/agentMessage/delta":
            buf = ch.state.setdefault("buf", [])
            buf.append(params.get("delta") or "")
            ch.state["streamed"] = True
            if sum(map(len, buf)) > 160:
                self._flush(ch, emit)
        elif method == "item/started":
            item = params.get("item") or {}
            kind = item.get("type")
            if kind == "agentMessage":
                if ch.state.get("said"):
                    ch.state.setdefault("buf", []).append("\n\n")
                ch.state["streamed"] = False
            elif kind == "commandExecution":
                emit("tool", {"name": "shell", "detail": short(item.get("command"))})
            elif kind == "fileChange":
                for change in item.get("changes") or []:
                    emit("tool", {"name": "edit", "detail": change.get("path", ""), "path": change.get("path", "")})
            elif kind == "webSearch":
                emit("tool", {"name": "web search", "detail": short(item.get("query"))})
            elif kind == "mcpToolCall":
                emit("tool", {"name": item.get("tool") or "tool", "detail": short(item.get("server") or "")})
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                if not ch.state.get("streamed") and item.get("text"):
                    ch.state.setdefault("buf", []).append(item["text"])
                self._flush(ch, emit)
                ch.state["said"] = True
        elif method == "turn/completed":
            self._flush(ch, emit)
            error = (params.get("turn") or {}).get("error")
            if error:
                out.error = short(error.get("message") if isinstance(error, dict) else error, 400)
            out.finished = True
        elif method == "account/rateLimits/updated":
            windows = quota_windows(params.get("rateLimits") or {})
            if windows:
                emit("quota", {"provider": self.name, "windows": windows})
        elif method == "thread/tokenUsage/updated":
            ch.state["usage"] = params.get("tokenUsage") or {}
        elif method == "error":
            emit("note", {"text": short(params.get("error") or params.get("message") or params, 300)})

    def _flush(self, ch: Channel, emit: Emit) -> None:
        buf = ch.state.get("buf") or []
        text = "".join(buf)
        buf.clear()
        if text:
            emit("text", {"text": text})

    def _asked(self, rid: Any, method: str, params: Dict[str, Any], emit: Emit, ch: Channel) -> None:
        key = {"rid": rid, "method": method, "params": params}
        if method == "item/tool/requestUserInput":
            questions = [{"question": q.get("question", ""), "header": q.get("header", ""), "multiSelect": False,
                          "options": [{"label": o.get("label", ""), "description": o.get("description", "")}
                                      for o in q.get("options") or []]}
                         for q in params.get("questions") or []]
            ch.ask("question", {"questions": questions}, key)
        elif method in APPROVALS and self.auto:
            self.answer(ch, key, {"allow": True})
            emit("tool", {"name": "approved", "detail": _what(params)})
        elif method in APPROVALS:
            ch.ask("permission", {"tool": "Codex", "title": _title(method), "description": params.get("reason") or "",
                                  "detail": _what(params), "can_always": method != "item/permissions/requestApproval"},
                   key)
        else:
            ch.write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"eki doesn't handle {method}"}})

    def answer(self, ch: Channel, key: Any, response: Dict[str, Any]) -> None:
        method, params = key["method"], key["params"]
        allow = bool(response.get("allow", True))
        if method == "item/tool/requestUserInput":
            given = response.get("answers") or {}
            answers = {}
            for q in params.get("questions") or []:
                got = given.get(q.get("question", "")) or given.get(q.get("id", ""))
                if got is not None:
                    answers[q["id"]] = {"answers": got if isinstance(got, list) else [str(got)]}
            result: Dict[str, Any] = {"answers": answers}
        elif method == "item/permissions/requestApproval":
            result = {"permissions": params.get("permissions") if allow else {}, "scope": "turn"}
        else:
            result = {"decision": ("acceptForSession" if response.get("always") else "accept") if allow else "decline"}
        ch.write({"jsonrpc": "2.0", "id": key["rid"], "result": result})


def _title(method: str) -> str:
    if "fileChange" in method or "Patch" in method:
        return "Change files"
    if "permissions" in method:
        return "More permissions"
    return "Run a command"


def _what(params: Dict[str, Any]) -> str:
    if params.get("command"):
        cmd = params["command"]
        return short(" ".join(cmd) if isinstance(cmd, list) else cmd, 400)
    changes = params.get("changes") or []
    if changes:
        return ", ".join(c.get("path", "") for c in changes)[:400]
    return short(params.get("permissions") or params.get("reason") or "", 400)


def quota_windows(limits: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Codex's windows by their real length: {"5h": …, "week": …, "30 days": …}."""
    out: Dict[str, Dict[str, Any]] = {}
    for slot in ("primary", "secondary"):
        w = limits.get(slot)
        if not isinstance(w, dict) or w.get("usedPercent") is None:
            continue
        mins = int(w.get("windowDurationMins") or 0)
        name = {300: "five_hour", 10080: "seven_day", 43200: "thirty_day"}.get(mins, f"{mins}m")
        out[name] = {"used": float(w["usedPercent"]) / 100, "resets_at": w.get("resetsAt")}
    return out


def mcp_flags(servers: Dict[str, Dict[str, Any]]) -> List[str]:
    """The registry as `-c` overrides, so nothing is written to ~/.codex."""
    flags: List[str] = []
    for name, spec in servers.items():
        key = f"mcp_servers.{name}"
        if spec.get("url"):
            flags.append(f"{key}.url={json.dumps(spec['url'])}")
            continue
        flags.append(f"{key}.command={json.dumps(spec.get('command', ''))}")
        if spec.get("args"):
            flags.append(f"{key}.args={json.dumps(list(spec['args']))}")
        env = spec.get("env") or {}
        if env:
            inner = ", ".join(f"{k} = {json.dumps(str(v))}" for k, v in env.items())
            flags.append(f"{key}.env={{{inner}}}")
    return flags
