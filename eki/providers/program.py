"""Running an agent program for one turn, and talking with it while it works.

A program prints one JSON object per line. An interactive one also reads
JSON on stdin: eki opens the conversation (`opening`), and when the
program asks something — a question for you, permission for a command —
the provider turns it into an *ask* (`Channel.ask`). The ask waits in the
database for your answer, however long that takes; the answer is handed
back to the program (`answer`) in its own terms. A turn ends when the
program says so (`Outcome.finished`); its input is then closed and it
exits by itself.

If the worker dies while an ask is open, nothing is lost: the run resumes
in the program's session and the program asks again.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from .base import Emit, Outcome, Provider, Turn, looks_like_limit

#: a program that prints nothing for this long (with nothing asked of you) is taken as hung
IDLE_LIMIT = float(os.environ.get("EKI_IDLE_LIMIT") or 30 * 60)
#: after a program says its turn is over, how long it gets to exit on its own
EXIT_GRACE = 10.0


class Channel:
    """The provider's side of a running program: write to it, ask you through it."""

    def __init__(self, proc: subprocess.Popen, turn: Turn, emit: Emit):
        self.proc, self.turn, self.emit = proc, turn, emit
        #: ask id → what the provider needs to answer it
        self.pending: Dict[str, Any] = {}
        #: the provider's own notes about the conversation (handshake done, request ids …)
        self.state: Dict[str, Any] = {}

    def write(self, obj: Dict[str, Any]) -> None:
        if self.proc.stdin is None or self.proc.stdin.closed:
            return
        try:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def close_input(self) -> None:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def ask(self, kind: str, payload: Dict[str, Any], key: Any) -> str:
        """Put a question to you. `key` is how the provider will know it again."""
        make = self.turn.extra.get("ask")
        if make is None:                      # nobody to ask (tests of a bare provider)
            return ""
        aid = make(kind, payload)
        self.pending[aid] = key
        return aid

    def retract(self, key: Any) -> None:
        """The program withdrew a question (it went on without the answer)."""
        for aid, k in list(self.pending.items()):
            if k == key or (isinstance(k, dict) and k.get("rid") == key):
                self.pending.pop(aid)
                if self.turn.extra.get("retract"):
                    self.turn.extra["retract"](aid)


class ProgramProvider(Provider):
    #: the program reads JSON on stdin while it works
    interactive = False

    def argv(self, turn: Turn) -> List[str]:
        raise NotImplementedError

    def opening(self, ch: Channel, turn: Turn) -> None:
        """What to say to an interactive program first."""

    def read(self, event: Dict[str, Any], emit: Emit, out: Outcome, ch: Channel) -> None:
        """Turn one line of the program's output into events; note the outcome."""
        raise NotImplementedError

    def answer(self, ch: Channel, key: Any, response: Dict[str, Any]) -> None:
        """Hand your answer to an ask back to the program."""

    def env(self, turn: Turn) -> Dict[str, str]:
        env = dict(os.environ)
        env["EKI_RUN"] = turn.run_id
        env["EKI_THREAD"] = turn.thread_id
        return env

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        out = Outcome()
        proc = subprocess.Popen(
            self.argv(turn), cwd=turn.cwd, env=self.env(turn),
            # a non-interactive CLI given a pipe on stdin waits for more input: DEVNULL
            stdin=subprocess.PIPE if self.interactive else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, process_group=0)
        if turn.extra.get("on_child"):
            turn.extra["on_child"](proc.pid)
        ch = Channel(proc, turn, emit)
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
        if self.interactive:
            self.opening(ch, turn)
        last = time.time()
        finished_at: Optional[float] = None
        try:
            while True:
                if turn.stop.is_set():
                    out.state, out.error = "cancelled", "stopped"
                    break
                if ch.pending:
                    last = time.time()                   # waiting on you isn't hanging
                    self._deliver(ch, turn)
                if out.finished and finished_at is None:
                    finished_at = time.time()
                    ch.close_input()
                if finished_at and time.time() - finished_at > EXIT_GRACE:
                    break
                try:
                    line = lines.get(timeout=0.3)
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
                    self.read(event, emit, out, ch)
        finally:
            for aid in list(ch.pending):
                if turn.extra.get("retract"):
                    turn.extra["retract"](aid)
            stop(proc)
        code = proc.returncode
        if out.state == "done" and not out.finished and code not in (0, None) and not out.error:
            tail = b"".join(err_tail).decode("utf-8", "replace").strip()
            out.state, out.error = "failed", (tail[-400:] or f"{self.name} exited {code}")
        if out.state == "done" and out.error:
            out.state = "failed"
        if out.state == "failed" and looks_like_limit(out.error):
            out.state = "limited"
        return out

    def _deliver(self, ch: Channel, turn: Turn) -> None:
        fetch = turn.extra.get("answers")
        if fetch is None:
            return
        for aid, response in fetch(list(ch.pending)):
            key = ch.pending.pop(aid, None)
            if key is not None:
                self.answer(ch, key, response)


def stop(proc: subprocess.Popen) -> None:
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
