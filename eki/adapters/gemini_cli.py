# SPDX-License-Identifier: Apache-2.0
"""Gemini CLI, driven as a subprocess.

The same rule as Claude Code and Codex: run Google's own `gemini` binary
under your own login, never read or re-serve its credentials.

`gemini -p … --output-format stream-json` prints one JSON object per line:
an `init` carrying the session id and model, `message` events (the
assistant's arrive as deltas), tool calls and their results, and a final
`result` with the token counts. As with Codex, the parser reads what it
recognises and skips the rest, so a new event kind is noise, not a failure.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .. import grant as grant_mod
from .. import nesting
from .. import settings as settings_mod
from .base import Backend, BackendError, Health, Message, register
from .claude_code import _find_binary


def read_event(event: Dict[str, Any]) -> Tuple[str, str]:
    """(assistant text, error) from one stream-json event; either may be empty."""
    kind = event.get("type")
    if kind == "message" and event.get("role") == "assistant":
        content = event.get("content")
        if isinstance(content, str):
            return content, ""
        if isinstance(content, list):
            return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict)), ""
    if kind == "error" and event.get("severity") != "warning":
        return "", str(event.get("message") or "gemini reported an error")
    if kind == "result" and event.get("status") not in (None, "success"):
        err = event.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        return "", str(err or event.get("status"))
    return "", ""


def usage(stats: Dict[str, Any]) -> Dict[str, Any]:
    """The result's stats, in the names the other adapters use."""
    out = {"input_tokens": stats.get("input_tokens", stats.get("input")),
           "output_tokens": stats.get("output_tokens"),
           "total_tokens": stats.get("total_tokens")}
    return {k: v for k, v in out.items() if isinstance(v, int)}


@register("gemini_cli")
class GeminiCliBackend(Backend):
    PRODUCES = ("code", "prose")

    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.bin = _find_binary(self.options.get("binary", "gemini"))
        self.model = self.options.get("model")
        self.timeout = float(self.options.get("timeout_seconds", 900))
        #: a folder run under "ask" permissions may edit, not run anything
        self.approval_mode = self.options.get("approval_mode", "auto_edit")
        self.last_session: Optional[str] = None
        self.last_usage: Dict[str, Any] = {}

    async def health(self) -> Health:
        if not self.bin:
            return Health(False, "gemini not found on PATH")
        proc = await asyncio.create_subprocess_exec(
            self.bin, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return Health(False, "gemini --version failed")
        return Health(True, "Gemini CLI " + out.decode().strip().split("\n")[0])

    def _argv(self, prompt: str, resume: Optional[str], cwd: Optional[str],
              grant: grant_mod.Grant = grant_mod.FULL) -> List[str]:
        argv = [self.bin, "-p", prompt, "--output-format", "stream-json"]
        if self.model:
            argv += ["-m", self.model]
        if resume:
            argv += ["--resume", resume]
        # Headless, nobody answers an approval prompt: Settings → Permissions
        # "auto" is the program's own yolo mode; otherwise a run given a
        # folder may edit it, and a chat turn keeps the default (read-only)
        if grant.narrowed:
            # a run an agent started: what its parent handed it (eki/grant.py)
            argv += ["--approval-mode", grant_mod.gemini_mode(grant)]
        elif settings_mod.load().get("permissions", "auto") == "auto":
            argv += ["--approval-mode", "yolo"]
        elif cwd:
            argv += ["--approval-mode", self.approval_mode]
        return argv

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        if not self.bin:
            raise BackendError("gemini not found — install Gemini CLI and sign in once")
        # the CLI keeps the conversation; earlier turns come back via --resume
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if not prompt:
            raise BackendError("no user message to send")

        cwd = kw.get("cwd") or self.options.get("cwd")
        grant = kw.get("grant") or grant_mod.FULL
        argv = self._argv(prompt, kw.get("resume") or self.options.get("resume"), cwd, grant)
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=nesting.child_env(grant_mod.env(grant)),
            # DEVNULL: with a pipe on stdin the CLI reads it as more prompt
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024)

        seen_any = False
        last_error = ""
        finished = False
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout)
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue                        # banner or log noise
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "init" and event.get("session_id"):
                    self.last_session = str(event["session_id"])
                if event.get("type") == "result" and isinstance(event.get("stats"), dict):
                    self.last_usage = usage(event["stats"])
                text, err = read_event(event)
                if text:
                    seen_any = True
                    yield text
                elif err:
                    last_error = err
            finished = True
        except asyncio.TimeoutError as e:
            raise BackendError("gemini timed out") from e
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
            try:
                err = (await proc.stderr.read())[:300].decode("utf-8", "replace").strip()
            except Exception:                       # noqa: BLE001
                err = ""
            # as with Codex: only a stream that really ended can be blamed
            # for its silence; a cancelled job closes this generator
            if finished and (last_error or not seen_any):
                raise BackendError(last_error or err or "gemini produced no output")
