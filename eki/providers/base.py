"""What every provider is: a way to take one turn of a thread.

A provider gets a `Turn` (what to send, which session to resume, the
folder) and reports what happens through `emit(kind, data)`:

    session  {"id"}                    the program's session, so a resume is possible
    text     {"text"}                  what it said
    tool     {"name", "detail"}        a tool it used
    thinking {"text"}
    usage    {...}
    note     {"text"}                  something eki wants you to know

and returns an `Outcome`. It never decides where work goes; that is
routing's job.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

Emit = Callable[[str, Dict[str, Any]], None]

#: a program that prints nothing for this long is taken as hung
IDLE_LIMIT = float(os.environ.get("EKI_IDLE_LIMIT") or 30 * 60)


@dataclass
class Turn:
    prompt: str                           # what to send now
    history: List[Dict[str, str]]         # the thread so far (for providers without a session)
    resume: Optional[str] = None          # this provider's session for the thread
    cwd: Optional[str] = None
    run_id: str = ""
    thread_id: str = ""
    stop: threading.Event = field(default_factory=threading.Event)
    extra: Dict[str, Any] = field(default_factory=dict)   # skills dir, mcp config …


@dataclass
class Outcome:
    state: str = "done"                   # done | failed | handed_off | limited
    error: str = ""
    reset_at: Optional[float] = None      # when a limit lifts
    reason: str = ""                      # why it handed off


class Provider:
    kind = "base"
    #: the provider can use tools (files, commands, web): a harness
    harness = False

    def __init__(self, name: str, cfg: Dict[str, Any]):
        self.name = name
        self.cfg = cfg
        self.label = cfg.get("label") or name

    def available(self) -> tuple:
        """(can it take work at all, why not)."""
        return True, ""

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        raise NotImplementedError


def find_binary(name: str) -> Optional[str]:
    """A launchd-started process has a bare PATH, so look where CLIs live."""
    if os.path.isabs(name):
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for d in ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.codex/bin"):
        cand = os.path.join(os.path.expanduser(d), name)
        if os.access(cand, os.X_OK):
            return cand
    return None


class ProgramProvider(Provider):
    """A provider that is a program printing one JSON object per line."""

    harness = True

    def argv(self, turn: Turn) -> List[str]:
        raise NotImplementedError

    def read(self, event: Dict[str, Any], emit: Emit, out: Outcome) -> None:
        """Turn one line of the program's output into events; note the outcome."""
        raise NotImplementedError

    def env(self, turn: Turn) -> Dict[str, str]:
        env = dict(os.environ)
        env["EKI_RUN"] = turn.run_id
        env["EKI_THREAD"] = turn.thread_id
        return env

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        out = Outcome()
        argv = self.argv(turn)
        proc = subprocess.Popen(
            argv, cwd=turn.cwd, env=self.env(turn),
            # DEVNULL: with a pipe on stdin the CLIs wait for more input
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            process_group=0)
        if turn.extra.get("on_child"):
            turn.extra["on_child"](proc.pid)
        lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        err_tail: List[bytes] = []

        def pump() -> None:
            for line in proc.stdout:          # type: ignore[union-attr]
                lines.put(line)
            lines.put(None)

        def pump_err() -> None:
            for line in proc.stderr:          # type: ignore[union-attr]
                err_tail.append(line)
                del err_tail[:-20]

        threading.Thread(target=pump, daemon=True).start()
        threading.Thread(target=pump_err, daemon=True).start()
        last = time.time()
        try:
            while True:
                if turn.stop.is_set():
                    out.state, out.error = "cancelled", "stopped"
                    break
                try:
                    line = lines.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - last > IDLE_LIMIT:
                        out.state, out.error = "failed", f"no output for {int(IDLE_LIMIT)}s"
                        break
                    continue
                if line is None:
                    break
                last = time.time()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue                  # banners, log noise
                if isinstance(event, dict):
                    self.read(event, emit, out)
        finally:
            _stop(proc)
        code = proc.returncode
        if out.state == "done" and code not in (0, None) and not out.error:
            tail = b"".join(err_tail).decode("utf-8", "replace").strip()
            out.state, out.error = "failed", (tail[-400:] or f"{self.name} exited {code}")
        if out.state == "done" and out.error:
            out.state = "failed"
        if out.state == "failed" and _looks_like_limit(out.error):
            out.state = "limited"
        return out


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, PermissionError):
        pass
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def _looks_like_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ("usage limit", "rate limit", "rate_limit", "limit reached",
                                "hit your limit", "quota", "too many requests", "429"))


#: how much of a thread a program joining it is given
HISTORY_CHARS = 12000


def with_history(turn: Turn) -> str:
    """The prompt for a program that has no session in this thread yet:
    what was said before (by whoever said it), then the request."""
    if not turn.history:
        return turn.prompt
    lines: List[str] = []
    for m in turn.history:
        who = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"{who}: {m['content']}")
    past = "\n\n".join(lines)
    if len(past) > HISTORY_CHARS:
        past = "…" + past[-HISTORY_CHARS:]
    return ("Part of this conversation happened with another assistant. What was said "
            "that you haven't seen:\n\n"
            f"<earlier>\n{past}\n</earlier>\n\nThe request now:\n\n{turn.prompt}")


def short(value: Any, n: int = 120) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
