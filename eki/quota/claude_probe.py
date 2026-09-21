# SPDX-License-Identifier: Apache-2.0
"""Ask Claude Code for a reading, instead of waiting to be told.

The status-line bridge is passive: it records whatever Claude Code reports
while you happen to have a session open. That is free and exact, but it says
nothing on a machine where you mostly use Claude somewhere else — and the
status line only carries the account's session and weekly windows.

So this opens a session and types /usage, which is where Claude Code shows
everything: the session and weekly windows, any per-model weekly allowance
(Fable, say), and the usage credits spent past the plan. /usage asks about
your account without asking a model anything, so it costs no tokens. The
session is in eki's own scratch folder, in plan mode with no tools, and is
closed as soon as the panel has been read.

Two things it deliberately does not do. It never touches Claude Code's
credentials — the whole point of this path is that eki doesn't hold your
login. And it never answers the folder-trust prompt: the first run asks you
to approve `~/.eki/probe` yourself, in your own terminal, once.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import select
import signal
import struct
import tempfile
import termios
import time
from pathlib import Path
from typing import List, Tuple

from . import claude_usage
from .statusline_bridge import QUOTA_DIR

PROBE_DIR = Path("~/.eki/probe").expanduser()
CLAUDE_CONFIG = Path("~/.claude.json").expanduser()
USAGE = QUOTA_DIR / "claude-usage.json"
#: plan mode and no tools: it cannot edit, run or read anything in there
ARGS = ["--permission-mode", "plan", "--allowedTools", ""]
COLS, ROWS = 120, 60
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


def _drive(binary: str, deadline: float) -> Tuple[List[str], str]:
    """Open a session, show /usage, return the screen's lines (or why not)."""
    import pyte                                     # a terminal to render into

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    pid, fd = pty.fork()
    if pid == 0:                                    # child: becomes Claude Code
        try:
            os.chdir(PROBE_DIR)
            os.environ.update(TERM="xterm-256color", COLUMNS=str(COLS), LINES=str(ROWS))
            os.execvp(binary, [binary, *ARGS])
        finally:
            os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.ByteStream(screen)
    started = time.time()
    typed = entered = 0.0
    # the panel fills in piece by piece — the session block first, per-model
    # limits and credits a moment later — so it's read once it stops growing
    seen_blocks, stable_since = 0, 0.0
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.3)
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                stream.feed(data)
            lines = [l.rstrip() for l in screen.display]
            text = "\n".join(lines)
            if "trust this folder" in text:
                return [], TRUST_HINT             # never answered here
            now = time.time()
            ready = "? for shortcuts" in text or 'Try "' in text
            if not typed and (ready or now - started > 10):
                os.write(fd, b"/usage")
                typed = now
            elif typed and not entered and now - typed > 1.0:
                os.write(fd, b"\r")                 # past the command palette
                entered = now
            elif entered:
                count = len(claude_usage.parse(lines))
                if count != seen_blocks:
                    seen_blocks, stable_since = count, now
                elif count and now - stable_since > 2.5:
                    return lines, ""
        return [], "Claude Code's /usage panel didn't appear"
    finally:
        _end(pid, fd)


def _drain(fd: int, stream, seconds: float = 1.5) -> None:
    """Read what's pending for a moment. Bounded: Claude Code redraws its
    screen continuously, so "until it goes quiet" can mean forever."""
    until = time.time() + seconds
    while time.time() < until and select.select([fd], [], [], 0.2)[0]:
        try:
            data = os.read(fd, 65536)
        except OSError:
            return
        if not data:
            return
        stream.feed(data)


def _end(pid: int, fd: int) -> None:
    """Close the session without ever blocking.

    A process can't finish exiting while its terminal still holds output
    nobody has read — it sits in the kernel waiting for the pty to drain, and
    even SIGKILL waits behind that. So the master side is read (and thrown
    away) the whole time, then closed, and every wait is non-blocking.
    """
    def drain() -> None:
        try:
            while select.select([fd], [], [], 0)[0]:
                if not os.read(fd, 65536):
                    return
        except OSError:
            return

    def reaped() -> bool:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
            return bool(done)
        except ChildProcessError:
            return True

    for keys in (b"\x1b", b"\x03", b"\x03"):      # close the panel, then quit
        try:
            os.write(fd, keys)
        except OSError:
            break
        drain()
        time.sleep(0.3)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        for _ in range(20):
            drain()
            if reaped():
                break
            time.sleep(0.1)
        else:
            if sig == signal.SIGKILL:
                break
            continue
        break
    try:
        os.close(fd)                                # unblocks any write still pending
    except OSError:
        pass
    for _ in range(20):
        if reaped():
            return
        time.sleep(0.1)


def save(blocks: List[claude_usage.Block]) -> None:
    QUOTA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"observed_at": int(time.time()), "source": "usage",
               "rate_limits": claude_usage.to_rate_limits(blocks)}
    fd, tmp = tempfile.mkstemp(dir=QUOTA_DIR, prefix=".usage-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, USAGE)


def reading() -> dict | None:
    try:
        return json.loads(USAGE.read_text())
    except (OSError, ValueError):
        return None


class NotTrusted(RuntimeError):
    """The probe folder hasn't been approved in Claude Code yet."""


async def refresh(binary: str = "claude", timeout: float = 45.0) -> str:
    """Show /usage in a throwaway session and keep what it says."""
    if not trusted():
        raise NotTrusted(TRUST_HINT)
    lines, problem = await asyncio.to_thread(_drive, binary, time.time() + timeout)
    if problem:
        raise (NotTrusted if problem == TRUST_HINT else RuntimeError)(problem)
    blocks = claude_usage.parse(lines)
    if not blocks:
        raise RuntimeError("Claude Code showed /usage but no plan limits — "
                           "that's what an API-key login looks like")
    save(blocks)
    return f"read {len(blocks)} limits from Claude Code's /usage"
