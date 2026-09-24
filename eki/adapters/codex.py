# SPDX-License-Identifier: Apache-2.0
"""Codex CLI, driven as a subprocess.

Same rule as Claude Code: run the genuine binary under your own login, never
extract or re-serve its token.

`codex exec --json` prints one event per line. The event vocabulary has
changed between releases — `-a untrusted` vanished under us once already — so
the parser reads defensively: it looks for text in any of the shapes seen in
the wild and ignores what it doesn't recognise, rather than assuming a schema.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, AsyncIterator, Dict, List, Optional

from . import _codex_events as events
from .. import grant as grant_mod
from .. import learn
from .. import settings as settings_mod
from .base import Backend, BackendError, Health, Message, register


def _find_binary(name: str) -> Optional[str]:
    if os.path.isabs(name):
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for d in ("~/.local/bin", "~/.codex/bin", "/opt/homebrew/bin", "/usr/local/bin"):
        cand = os.path.join(os.path.expanduser(d), name)
        if os.access(cand, os.X_OK):
            return cand
    return None


_FEATURES: Dict[str, set] = {}


def features(binary: str) -> set:
    """The feature names this Codex knows (`codex features list`), once per
    binary. Empty when it can't say — then eki disables nothing optional."""
    if not binary:
        return set()
    if binary not in _FEATURES:
        import subprocess
        try:
            out = subprocess.run([binary, "features", "list"], capture_output=True, text=True,
                                 timeout=15, stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _FEATURES[binary] = {line.split()[0] for line in out.splitlines()
                             if line.strip() and not line.startswith(" ")}
    return _FEATURES[binary]


@register("codex")
class CodexBackend(Backend):
    PRODUCES = ("code", "prose")

    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.bin = _find_binary(self.options.get("binary", "codex"))
        self.model = self.options.get("model")
        self.sandbox = self.options.get("sandbox", "read-only")
        self.disabled_features = list(self.options.get("disable_features", []))
        self.timeout = float(self.options.get("timeout_seconds", 900))
        self.last_session: Optional[str] = None
        self.last_usage: Dict[str, Any] = {}

    async def health(self) -> Health:
        if not self.bin:
            return Health(False, "codex not found on PATH")
        proc = await asyncio.create_subprocess_exec(
            self.bin, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return Health(False, "codex --version failed")
        detail = out.decode().strip()
        from .. import codex_host
        if not codex_host.present(self.bin):
            # answers questions, refuses every edit — worth saying up front
            detail += " · can't edit files: codex-code-mode-host is missing"
        return Health(True, detail)

    def gateway_flags(self) -> List[str]:
        gateway = self.options.get("gateway")
        if not gateway:
            return []
        return ["-c", "model_provider=eki",
                "-c", 'model_providers.eki.name="eki"',
                "-c", f'model_providers.eki.base_url="{gateway}"',
                "-c", 'model_providers.eki.wire_api="responses"',
                "-c", f"model_context_window={int(self.options.get('context_tokens', 32000))}",
                "-c", "model_reasoning_effort=\"medium\""]

    def _disabled(self) -> List[str]:
        """Features turned off for runs under eki: what the provider's options
        say, and Codex's own memories while eki is learning — one lesson is
        kept in one place, the skill store every backend reads (eki/learn.py).
        Only a feature this Codex has: `--disable` with a name it doesn't
        know is a hard error, and an older Codex has no `memories`."""
        off = list(self.disabled_features)
        if (learn.learning(settings_mod.load()) and "memories" not in off
                and "memories" in features(self.bin or "")):
            off.append("memories")
        return off

    def live_argv(self) -> List[str]:
        """The app-server (see eki/codex_live.py): the program without its
        screen, questions and approvals over stdio."""
        if not self.bin:
            raise BackendError("codex not found — install Codex or set binary")
        argv = [self.bin, "--enable", "default_mode_request_user_input",
                "-c", "suppress_unstable_features_warning=true"]
        for feature in self._disabled():
            argv += ["--disable", feature]
        argv += self.gateway_flags()
        argv.append("app-server")
        return argv

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        if not self.bin:
            raise BackendError("codex not found — install Codex CLI and `codex login`")
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if not prompt:
            raise BackendError("no user message to send")

        cwd = kw.get("cwd") or self.options.get("cwd")
        sandbox = "workspace-write" if cwd else self.sandbox
        # outside a git repo Codex refuses unless told to skip the check;
        # for a chat turn there is no repo to trust
        resume = kw.get("resume") or self.options.get("resume")
        auto = settings_mod.load().get("permissions", "auto") == "auto"
        # Settings → Permissions "auto": Codex's own skip-everything mode;
        # otherwise the sandbox keeps it to the folder
        grant = kw.get("grant") or grant_mod.FULL
        guard = ["--dangerously-bypass-approvals-and-sandbox"] if auto else ["-s", sandbox]
        if grant.narrowed:
            # a run an agent started: what its parent handed it (eki/grant.py)
            guard = grant_mod.codex_argv(grant)
        argv = [self.bin, "exec", "--json", *guard]
        if resume:
            argv = [self.bin, "exec", "resume", resume, "--json", *guard]
        if not cwd:
            argv.append("--skip-git-repo-check")
        if self.model:
            argv += ["-m", self.model]
        # a local model, served through eki's own Responses endpoint
        # (see eki/gateway.py): Codex's harness, the Mac's model
        argv += self.gateway_flags()
        for feature in self._disabled():
            # a feature whose helper binary isn't installed fails closed and
            # the model then reports it cannot edit anything
            argv += ["--disable", feature]
        note = learn.agent_note(settings_mod.load())
        if note:
            argv += ["-c", f"developer_instructions={json.dumps(note)}"]
        argv.append(prompt)

        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=grant_mod.env(grant),
            # DEVNULL, not inherit: with a pipe on stdin the CLI waits for more
            # input instead of answering ("Reading additional input from stdin")
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            # a line can carry a whole image (the judge reading a picture)
            limit=16 * 1024 * 1024)

        seen_any = False
        last_error = ""
        finished = False
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(),
                                              timeout=self.timeout)
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = events.session_id(event)
                if sid:
                    self.last_session = sid
                counts = events.usage(event)
                if counts:
                    self.last_usage = counts
                text, err = events.read(event)
                if text:
                    seen_any = True
                    yield text
                elif err:
                    # non-fatal item errors (a missing optional host, say) are
                    # noise unless nothing else arrives
                    last_error = err
            finished = True
        except asyncio.TimeoutError as e:
            raise BackendError("codex timed out") from e
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
            # only complain about silence when the stream really ended; a
            # cancelled job closes this generator, and raising there would
            # replace the cancellation with a bogus failure
            if finished and not seen_any:
                raise BackendError(last_error or err or "codex produced no output")
