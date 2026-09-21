"""Claude Code, driven as a subprocess.

This is the sanctioned way to spend a subscription from outside the client: run
the genuine binary in its headless mode. The engine never touches the OAuth
token and never re-exposes it — that pattern is prohibited and enforced.

`--output-format stream-json` emits one JSON object per line: an init event
carrying the session id, assistant messages as they complete, and a final
result event with token counts.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import Backend, BackendError, Health, Message, register


def _find_binary(name: str) -> Optional[str]:
    """A GUI-launched process inherits a bare PATH, so look where CLIs live."""
    if os.path.isabs(name):
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for d in ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"):
        cand = os.path.join(os.path.expanduser(d), name)
        if os.access(cand, os.X_OK):
            return cand
    return None


@register("claude_code")
class ClaudeCodeBackend(Backend):
    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.bin = _find_binary(self.options.get("binary", "claude"))
        self.model = self.options.get("model")
        self.timeout = float(self.options.get("timeout_seconds", 900))
        #: how a folder run handles permission prompts it cannot ask about;
        #: acceptEdits lets it write, still refusing the dangerous verbs
        self.permission_mode = self.options.get("permission_mode", "acceptEdits")
        self._help: Optional[str] = None
        #: set from the init event, so a follow-up can --resume this thread
        self.last_session: Optional[str] = None

    async def health(self) -> Health:
        if not self.bin:
            return Health(False, "claude not found on PATH")
        proc = await asyncio.create_subprocess_exec(
            self.bin, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return Health(False, "claude --version failed")
        return Health(True, out.decode().strip().split("\n")[0])

    def _no_bare_flag(self) -> Optional[str]:
        """The flag that keeps `-p` on your subscription login, if one exists.

        Claude Code's docs say `--bare` will become the default for `-p`, and
        bare mode ignores the subscription login. The opt-out isn't named yet,
        so read the CLI's own help once and pass `--no-bare` only if the help
        actually lists it — never a guess. When the change ships, this is the
        one place to update.
        """
        if self._help is None:
            try:
                out = subprocess.run([self.bin, "--help"], capture_output=True,
                                     text=True, timeout=15)
                self._help = out.stdout + out.stderr
            except (OSError, subprocess.SubprocessError):
                self._help = ""
        # whole-flag match only: a substring test would happily "find" a
        # flag inside some unrelated longer one and pass it to every run
        if re.search(r"(?<![\w-])--no-bare(?![\w-])", self._help):
            return "--no-bare"
        return None

    def _argv(self, prompt: str, resume: Optional[str], cwd: Optional[str]) -> List[str]:
        argv = [self.bin, "-p", prompt,
                "--output-format", "stream-json", "--verbose"]
        keep_login = self._no_bare_flag()
        if keep_login:
            argv.append(keep_login)
        if self.model:
            argv += ["--model", self.model]
        if resume:
            argv += ["--resume", resume]
        if cwd:
            # Headless, there is nobody to answer a permission prompt: without
            # a mode the run ends with "I don't have permission to write".
            # Only a run that was given a folder gets edit rights, and only
            # for that folder — a chat turn stays read-only.
            argv += ["--add-dir", cwd,
                     "--permission-mode", self.permission_mode]
        return argv

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        if not self.bin:
            raise BackendError("claude not found — install Claude Code or set binary")

        # The CLI owns conversation state, so only the newest user turn is sent;
        # earlier turns come back via --resume rather than being replayed.
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if not prompt:
            raise BackendError("no user message to send")

        cwd = kw.get("cwd") or self.options.get("cwd")
        argv = self._argv(prompt, kw.get("resume") or self.options.get("resume"), cwd)

        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            # DEVNULL, not inherit: with a pipe on stdin the CLI waits for more
            # input instead of answering ("Reading additional input from stdin")
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        finished = False
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout)
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue            # banner or log noise

                kind = event.get("type")
                if kind == "system" and event.get("subtype") == "init":
                    self.last_session = event.get("session_id")
                elif kind == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text" and block.get("text"):
                            yield block["text"]
                elif kind == "result":
                    if event.get("is_error"):
                        raise BackendError(str(event.get("result"))[:200])
                    self.last_usage = event.get("usage") or {}
                    break
            finished = True
        except asyncio.TimeoutError as e:
            raise BackendError("claude timed out") from e
        finally:
            killed = proc.returncode is None
            if killed:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
            try:
                err = (await proc.stderr.read())[:300].decode("utf-8", "replace")
            except Exception:                       # noqa: BLE001
                err = ""
            # A signal we sent is not a failure: after `result` arrives we kill
            # the process ourselves, and a cancelled job closes this generator.
            # Only an exit the CLI chose counts as an error.
            if finished and not killed and proc.returncode != 0 and err:
                raise BackendError(err.strip())
