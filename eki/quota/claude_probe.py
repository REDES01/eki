"""Ask Claude Code for a reading, instead of waiting to be told.

The status-line bridge is passive: it records whatever Claude Code reports
while you happen to have a session open. That is free and exact, but it says
nothing on a machine where you mostly use Claude somewhere else.

So this starts one: a real interactive session, in a scratch folder of eki's
own, in plan mode with no tools, one word in and one word out. Claude Code
renders its status line, the bridge records the limits, and the session is
closed. It costs a few hundred tokens of the subscription you already have.

Two things it deliberately does not do. It never touches Claude Code's
credentials — the whole point of this path is that eki doesn't hold your
login. And it never answers the folder-trust prompt: the first run asks you
to approve `~/.eki/probe` yourself, in your own terminal, once.
"""
from __future__ import annotations

import asyncio
import json
import os
import pty
import select
import signal
import time
from pathlib import Path
from typing import Optional, Tuple

from .statusline_bridge import READING

PROBE_DIR = Path("~/.eki/probe").expanduser()
CLAUDE_CONFIG = Path("~/.claude.json").expanduser()
#: plan mode and no tools: it cannot edit, run or read anything in there
ARGS = ["--permission-mode", "plan", "--allowedTools", ""]
PROMPT = "hi"
TRUST_HINT = ("Claude Code needs you to trust eki's probe folder once. In a "
              "terminal: cd ~/.eki/probe && claude — answer “Yes, I trust this "
              "folder”, then /exit. After that eki can refresh on its own.")


def trusted(folder: Path = PROBE_DIR) -> bool:
    """Has the user already approved this folder in Claude Code?

    Read-only, and only this one flag: eki asks the question, it does not
    answer it.
    """
    try:
        projects = json.loads(CLAUDE_CONFIG.read_text()).get("projects") or {}
    except (OSError, ValueError):
        return False
    entry = projects.get(str(folder)) or projects.get(os.path.realpath(folder))
    return bool(isinstance(entry, dict) and entry.get("hasTrustDialogAccepted"))


def _has_limits() -> bool:
    """A reading with numbers in it.

    Claude Code renders its status line as soon as the session opens, before
    it has asked the API anything — so the first render carries no limits at
    all. Waiting for the file to change is not enough; it has to change into
    something with limits in it.
    """
    try:
        return bool(json.loads(READING.read_text()).get("rate_limits"))
    except (OSError, ValueError):
        return False


def _drive(binary: str, deadline: float) -> Tuple[bool, str]:
    """Run one tiny session in a pty. Returns (a reading arrived, detail)."""
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    pid, fd = pty.fork()
    if pid == 0:                                    # child: becomes Claude Code
        try:
            os.chdir(PROBE_DIR)
            os.environ["TERM"] = "xterm-256color"
            os.execvp(binary, [binary, *ARGS])
        finally:
            os._exit(127)

    seen = b""
    started = time.time()
    sent_at: Optional[float] = None
    detail = "no reading arrived"
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(fd, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                seen += chunk[-8192:]
            text = seen.decode("utf-8", "replace")
            if "trust this folder" in text:
                # never answered here; the user does that once, themselves
                detail = TRUST_HINT
                break
            ready = "? for shortcuts" in text or "Try \"" in text
            # don't depend on Claude Code's welcome text staying the same: if
            # the session has been quiet for a while, just type
            if sent_at is None and (ready or time.time() - started > 15):
                os.write(fd, PROMPT.encode() + b"\r")
                sent_at = time.time()
            if _has_limits():
                # the status line rendered with limits in it: that's the whole job
                return True, "read from a fresh Claude Code session"
            if sent_at and time.time() - sent_at > 90:
                detail = ("Claude Code answered but its status line reported no "
                          "limits — that's what an API-key login looks like")
                break
    finally:
        _end(pid, fd)
    return False, detail


def _end(pid: int, fd: int) -> None:
    for signature in (b"\x03", b"\x03"):            # ctrl-c twice quits cleanly
        try:
            os.write(fd, signature)
            time.sleep(0.3)
        except OSError:
            break
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(20):
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            break
        if done:
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
    try:
        os.close(fd)
    except OSError:
        pass


class NotTrusted(RuntimeError):
    """The probe folder hasn't been approved in Claude Code yet."""


async def refresh(binary: str = "claude", timeout: float = 120.0) -> str:
    """Start a session, wait for a reading, stop. Raises NotTrusted first run."""
    if not trusted():
        raise NotTrusted(TRUST_HINT)
    deadline = time.time() + timeout
    ok, detail = await asyncio.to_thread(_drive, binary, deadline)
    if not ok:
        raise RuntimeError(detail)
    return detail
