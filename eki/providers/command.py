"""A command in a folder, as a run: `bin/check` in a worktree, a rebase, a build.

Not a harness and not a model — a job runner, so a check or a build is a run
like any other: written down before it starts, followed with `eki follow`,
resumed after a restart (by running again: a check is idempotent). The
prompt is the command — a JSON list (`["sh", "-c", "…"]`) or a plain shell
line — and the thread's folder is where it runs. Output lines are text
events; exit 0 is `done`, anything else `failed` with the last of stderr.
The provider is built in (`providers.BUILTIN`), never in the routing table:
a run reaches it only when it's picked by name.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from typing import List, Optional

from .base import Emit, Outcome, Provider, Turn

#: a command that prints nothing for this long is taken as hung
IDLE_LIMIT = float(os.environ.get("EKI_COMMAND_IDLE") or 60 * 60)


def argv_of(prompt: str) -> List[str]:
    text = (prompt or "").strip()
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list) or not all(isinstance(a, str) for a in parsed):
            raise ValueError("a command is a JSON list of strings")
        return parsed
    return ["/bin/sh", "-c", text]


class Command(Provider):
    kind = "command"

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        out = Outcome()
        try:
            argv = argv_of(turn.prompt)
        except ValueError as e:
            out.state, out.error = "failed", str(e)
            return out
        env = dict(os.environ)
        env.update({"EKI_RUN": turn.run_id, "EKI_THREAD": turn.thread_id})
        env.update(self.cfg.get("env") or {})
        env.update(turn.extra.get("env") or {})
        try:
            proc = subprocess.Popen(argv, cwd=turn.cwd or None, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, process_group=0)
        except OSError as e:
            out.state, out.error = "failed", f"couldn't start {argv[0]}: {e}"
            return out
        if turn.extra.get("on_child"):
            turn.extra["on_child"](proc.pid)
        err_tail: List[bytes] = []
        lines: "queue.Queue[Optional[bytes]]" = queue.Queue()

        def pump(stream, is_err: bool) -> None:
            for line in stream:
                if is_err:
                    err_tail.append(line)
                    del err_tail[:-30]
                lines.put(line)
            lines.put(None)

        for stream, is_err in ((proc.stdout, False), (proc.stderr, True)):
            threading.Thread(target=pump, args=(stream, is_err), daemon=True).start()
        open_streams, last = 2, time.time()
        while open_streams:
            if turn.stop.is_set():
                _stop(proc)
                out.state, out.error = "cancelled", "stopped"
                break
            try:
                line = lines.get(timeout=0.3)
            except queue.Empty:
                if time.time() - last > IDLE_LIMIT:
                    _stop(proc)
                    out.state, out.error = "failed", f"no output for {int(IDLE_LIMIT)}s"
                    break
                continue
            if line is None:
                open_streams -= 1
                continue
            last = time.time()
            emit("text", {"text": line.decode("utf-8", "replace")})       # on this thread only
        code = proc.wait()
        if out.state == "done" and code != 0:
            tail = b"".join(err_tail).decode("utf-8", "replace").strip()
            out.state = "failed"
            out.error = (tail[-600:] or f"exited {code}")
        out.finished = True
        emit("usage", {"exit": code})
        return out


def _stop(proc: subprocess.Popen) -> None:
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
