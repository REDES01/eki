# SPDX-License-Identifier: Apache-2.0
"""Work that outlives the engine (docs/self-build.md, "Runs cut off by a swap").

The engine used to be the parent of everything it started — Claude Code and
Codex, the test runs of a check, the candidate engine, a slow git step — so
a restart took them all down and they were redone. Now each of those is a
*worker*: started in a session of its own, so the engine going away doesn't
take it along, with what it prints written to files the engine reads rather
than a pipe it holds:

    ~/.eki/work/<id>/
        spec.json    what was started, and for whom: command, folder, the
                     run, step and thread it serves (the engine writes this)
        state.json   the program's pid and start time, and its exit code
                     once it ends (the keeper writes this)
        out, err     what it printed
        in           a fifo, for a program the engine talks to
        read         how far the engine has read `out`, and handled it

A small keeper — this file, run as a script, stdlib only — starts the
program, holds its `in` open (so no engine going away is an end of input),
and waits for it, so an exit while no engine is up is still written down.
It always drains `in`, passing it on to the program as the program reads
(up to INPUT_CAP waiting), and the engine never waits on it: its writes go
through the event loop, and a program that takes none of its input for
STALL seconds is stopped and its step taken up again. On 2026-09-24 one
blocking write to a program that wasn't reading froze the whole engine.

A new engine looks here (`scan`): a worker still running is taken up again
where its reading stood; one that finished is concluded from its files; one
dead with nothing written is lost, and its step goes the resume / re-run way
(eki/steps.py). A pid is only believed with its start time — a pid reused by
another program is not a worker. A person's cancel still kills the worker,
on purpose (`Worker.kill`).
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

HOME = Path("~/.eki/work").expanduser()
#: finished work is forgotten after a week
KEEP = 7 * 86400
#: a worker nobody took up is killed after this long (`sweep`)
ORPHAN_GRACE = 10 * 60
#: an uncollected result is taken as the answer only this soon after it ended
FRESH = 30 * 60
#: how often an idle reader looks at a file again
POLL = 0.05
#: a keeper that hasn't said the program's pid in this long never will
STARTING = 15.0
#: input waiting for a program — in the engine, and again in its keeper
INPUT_CAP = int(os.environ.get("EKI_WORKER_INPUT_CAP") or 8 << 20)
#: a program that takes none of its input for this long has stopped reading
STALL = float(os.environ.get("EKI_WORKER_STALL") or 60)

log = logging.getLogger("eki.workers")

#: workers this engine has in hand: not orphans, whatever their run says
_held: Dict[str, float] = {}
#: when this engine first saw a worker nobody had in hand
_orphan_since: Dict[str, float] = {}


# ---- one worker ------------------------------------------------------------------------

def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


def started_at(pid: int) -> str:
    """When process `pid` started, as the system says it — with the pid, what
    tells a worker from another program that got its pid later. "" if none."""
    if pid <= 0:
        return ""
    try:
        got = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(got.stdout.split())


def _pid_there(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Worker:
    """A worker's folder, and what it says about the program."""

    def __init__(self, path: Path):
        self.dir = Path(path)
        self.id = self.dir.name
        self._believed = False              # its pid checked against its start time once

    def __repr__(self) -> str:
        return f"Worker({self.id})"

    @property
    def spec(self) -> Dict[str, Any]:
        return _read_json(self.dir / "spec.json")

    @property
    def state(self) -> Dict[str, Any]:
        return _read_json(self.dir / "state.json")

    def update(self, **fields: Any) -> None:
        """What the worker serves, as it changes (a thread's next run)."""
        spec = self.spec
        spec.update({k: v for k, v in fields.items() if v is not None})
        _write_json(self.dir / "spec.json", spec)

    @property
    def pid(self) -> int:
        return int(self.state.get("pid") or 0)

    @property
    def group(self) -> int:
        """The keeper leads the worker's session: its group is the worker."""
        return int(self.state.get("keeper") or self.spec.get("keeper") or 0)

    @property
    def exit(self) -> Optional[int]:
        code = self.state.get("exit")
        return None if code is None else int(code)

    def alive(self, strict: bool = True) -> bool:
        """Still running. `strict` checks the pid's start time against the one
        written down (once is enough while this engine holds it): a pid that
        came back as another program is not this worker."""
        st = self.state
        if st.get("exit") is not None:
            return False
        pid = int(st.get("pid") or 0)
        if not pid:
            # the keeper hasn't said yet: starting, if it's still there
            keeper = int(self.spec.get("keeper") or 0)
            young = time.time() - float(self.spec.get("at") or 0) < STARTING
            return young and _pid_there(keeper)
        if not _pid_there(pid):
            return False
        if strict and not self._believed:
            if started_at(pid) != st.get("started"):
                return False
            self._believed = True
        return True

    def lost(self) -> bool:
        """Dead with no result: cut off, not finished — not a program whose
        keeper is still writing down how it ended."""
        if self.exit is not None or self.alive():
            return False
        return not self._keeper_there() and self.exit is None

    def ended(self) -> bool:
        """Over, with all that will ever be said about how: its exit written
        down, or its keeper gone too without a word (cut off). The program
        ends a moment before its keeper writes that down; a look in between
        is no verdict — under load it once read as -9 for good."""
        if self.exit is not None:
            return True
        if self.alive(strict=False):
            return False
        return not self._keeper_there()

    def _keeper_there(self) -> bool:
        """The keeper still runs (and so will write the exit down). One this
        engine started is its child: asked with waitpid, which also reaps
        it, so a keeper that died never lingers here as a zombie."""
        keeper = self.group
        if keeper <= 0:
            return False
        try:
            done, _ = os.waitpid(keeper, os.WNOHANG)
            return not done
        except ChildProcessError:
            pass                            # a keeper an engine before this one started
        if not _pid_there(keeper):
            return False
        was = self.state.get("keeper_started")
        return not was or started_at(keeper) == was

    def ended_at(self) -> float:
        return float(self.state.get("ended") or 0)

    def text(self, name: str = "out") -> str:
        try:
            return (self.dir / name).read_bytes().decode("utf-8", "replace")
        except OSError:
            return ""

    def said_after(self, offset: int, needle: bytes) -> bool:
        """Did it print `needle` past `offset`? (A turn's result it gave while
        no engine was reading.)"""
        try:
            with open(self.dir / "out", "rb") as f:
                f.seek(offset)
                return needle in f.read()
        except OSError:
            return False

    # ---- how far the engine has read ----

    def reading(self) -> Tuple[int, int]:
        """(pos, seen): where to read `out` from again, and how far every line
        was handled. Lines between them are read again only for what the
        engine still owes the program (a request it hadn't answered)."""
        try:
            raw = (self.dir / "read").read_text().split()
            return int(raw[0]), int(raw[1])
        except (OSError, ValueError, IndexError):
            return 0, 0

    def note_reading(self, pos: int, seen: int) -> None:
        # one fixed-width line, written in place: cheap enough for every event
        line = f"{pos:020d} {seen:020d}\n".encode()
        fd = os.open(self.dir / "read", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            os.pwrite(fd, line, 0)
        finally:
            os.close(fd)

    # ---- ending it ----

    def signal(self, sig: int) -> bool:
        for target in (self.group, self.pid):
            if target <= 0:
                continue
            try:
                if target == self.group:
                    os.killpg(target, sig)
                else:
                    os.kill(target, sig)
                return True
            except (ProcessLookupError, PermissionError):
                continue
        return False

    def kill(self, grace: float = 3.0, why: str = "cancelled") -> None:
        """Stopped on purpose — a person's cancel, an orphan swept up. The
        whole session goes: the program and whatever it started."""
        if not self.alive():
            return                          # gone — or its pid is someone else's now
        self.signal(signal.SIGTERM)
        deadline = time.time() + grace
        while time.time() < deadline and self.alive(strict=False):
            time.sleep(0.05)
        if self.alive(strict=False):
            self.signal(signal.SIGKILL)
            time.sleep(0.1)
        if self.exit is None:
            st = self.state
            st.update(exit=-int(signal.SIGKILL), ended=time.time(), why=why)
            _write_json(self.dir / "state.json", st)
        release(self.id)

    def collect(self) -> None:
        """Its result was taken: not an answer for anyone else."""
        self.update(collected=True)
        release(self.id)

    def remove(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- starting and finding --------------------------------------------------------------

def _home(home: Optional[Path]) -> Path:
    return Path(home or HOME)


def hold(wid: str) -> None:
    """This engine has the worker in hand (not an orphan)."""
    _held[wid] = time.time()
    _orphan_since.pop(wid, None)


def release(wid: str) -> None:
    _held.pop(wid, None)


def held(wid: str) -> bool:
    return wid in _held


def start(argv: List[str], *, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
          talk: bool = False, merge: bool = False, out: Optional[Path] = None,
          home: Optional[Path] = None, **meta: Any) -> Worker:
    """Start `argv` as a worker: its own session, its output in files. `talk`
    gives it an `in` to be written to (Claude Code, Codex); `merge` puts its
    stderr with its stdout; `out` puts its stdout somewhere else (a log the
    caller keeps). `meta` — run, step, thread, kind, key — is what it serves."""
    if "run" not in meta:
        from . import nesting               # the run this is for, as the task knows it
        meta["run"] = nesting.current()[1]
    wid = uuid.uuid4().hex[:12]
    d = _home(home) / wid
    d.mkdir(parents=True, exist_ok=True)
    if talk:
        os.mkfifo(d / "in", 0o600)
    spec = {"argv": [str(a) for a in argv], "cwd": str(cwd or ""), "talk": talk,
            "merge": merge, "out": str(out or ""), "at": time.time(), "input_cap": INPUT_CAP,
            **{k: v for k, v in meta.items() if v not in (None, "")}}
    _write_json(d / "spec.json", spec)
    keeper = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "keep", str(d)],
                              cwd=cwd or None, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=True, close_fds=True)
    spec["keeper"] = keeper.pid
    _write_json(d / "spec.json", spec)
    hold(wid)
    return Worker(d)


def scan(home: Optional[Path] = None) -> List[Worker]:
    """Every worker written down, oldest first."""
    root = _home(home)
    try:
        dirs = [p for p in root.iterdir() if p.is_dir() and (p / "spec.json").exists()]
    except OSError:
        return []
    ws = [Worker(p) for p in dirs]
    return sorted(ws, key=lambda w: float(w.spec.get("at") or 0))


def find(home: Optional[Path] = None, **match: Any) -> List[Worker]:
    """Workers whose spec says all of `match` (run=…, thread=…, key=…)."""
    return [w for w in scan(home) if all(w.spec.get(k) == v for k, v in match.items())]


def key_of(*parts: Any) -> str:
    return hashlib.sha1("\0".join(str(p) for p in parts).encode()).hexdigest()[:16]


def run(argv: List[str], *, key: str, cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None, timeout: float = 0.0, merge: bool = False,
        finished: bool = True, home: Optional[Path] = None, **meta: Any) -> Tuple[int, str, str]:
    """Run `argv` to its end as a worker and give (exit, out, err) — joining
    one with the same `key` still running from before a restart instead of
    starting a second, or taking the result of one that finished while no
    engine was up (`finished=False`: only a running one is joined — for a
    step that changes things before it runs again). A worker lost with no
    result gives -9: cut off (steps.cut_off), not failed. On timeout it is
    killed and TimeoutError raised."""
    w = None
    for old in reversed(find(home, key=key)):
        if old.spec.get("collected"):
            continue
        if old.alive():
            w = old
            hold(w.id)
            break
        if finished and old.exit is not None and time.time() - old.ended_at() < FRESH:
            w = old
            break
    if w is None:
        w = start(argv, cwd=cwd, env=env, merge=merge, home=home, key=key, **meta)
    since = time.time()
    while not w.ended():
        if timeout and time.time() - since > timeout:
            w.kill(why="timed out")
            w.collect()
            raise TimeoutError(f"still running after {timeout:.0f}s")
        time.sleep(0.1)
    code = w.exit
    out, err = w.text("out"), w.text("err")
    w.collect()
    return (-int(signal.SIGKILL) if code is None else code), out, err


def sweep(owned: Callable[[Worker], bool], home: Optional[Path] = None,
          now: Optional[float] = None, grace: float = ORPHAN_GRACE, keep: float = KEEP,
          note: Optional[Callable[..., Any]] = None) -> Dict[str, int]:
    """Housekeeping, now and then: finished work older than a week is
    removed; a worker running for nobody — not in this engine's hand, its
    run not going (`owned` says) — is killed once it has been so for `grace`,
    and `note` says so in the journal."""
    now = now or time.time()
    done = {"removed": 0, "killed": 0}
    for w in scan(home):
        if w.alive():
            if held(w.id) or owned(w):
                _orphan_since.pop(w.id, None)
                continue
            first = _orphan_since.setdefault(w.id, now)
            if now - first >= grace:
                spec = w.spec
                w.kill(why="an orphan: no run was using it")
                _orphan_since.pop(w.id, None)
                done["killed"] += 1
                if note:
                    note("history", what="orphan worker killed", worker=w.id,
                         command=" ".join(spec.get("argv") or [])[:200],
                         run=spec.get("run"), step=spec.get("step"),
                         why=f"nothing took it up for {int(grace // 60)} min")
            continue
        _orphan_since.pop(w.id, None)
        ended = w.ended_at() or float(w.spec.get("at") or 0)
        if now - ended >= keep:
            w.remove()
            done["removed"] += 1
    return done


# ---- a worker as the code that talks to a program sees it ---------------------------------

class _Out:
    """A worker's output file, read like a pipe: lines as they come, the end
    when the program has ended and everything it wrote is read."""

    def __init__(self, worker: Worker, name: str, offset: int = 0):
        self.worker = worker
        self.path = worker.dir / name
        self.offset = offset
        self._f = None
        self._last = time.time()

    def _file(self):
        if self._f is None:
            try:
                self._f = open(self.path, "rb")
            except OSError:
                return None
        return self._f

    async def readline(self) -> bytes:
        while True:
            f = self._file()
            if f is not None:
                f.seek(self.offset)
                line = f.readline()
                if line.endswith(b"\n"):
                    self.offset += len(line)
                    self._last = time.time()
                    return line
            if not self.worker.alive(strict=False):
                # everything it wrote before it ended is there now
                f = self._file()
                if f is None:
                    return b""
                f.seek(self.offset)
                rest = f.read()
                self.offset += len(rest)
                return rest
            # quick while it's talking; a quiet program is looked at less often
            await asyncio.sleep(POLL if time.time() - self._last < 5 else POLL * 5)

    async def read(self) -> bytes:
        f = self._file()
        if f is None:
            return b""
        f.seek(self.offset)
        rest = f.read()
        self.offset += len(rest)
        return rest

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None


class InputStalled(BrokenPipeError):
    """The program stopped taking its input: its work is cut off (taken up
    again, eki/steps.py), not failed."""


class _In:
    """The worker's fifo, written without ever blocking the engine: what the
    fifo has no room for waits here and goes as the loop finds room
    (`add_writer`). The keeper holds its own end open, so closing this is
    not an end of input for the program.

    A program that takes none of its input for `stall` seconds, or lets more
    than `cap` pile up here, has stopped reading: nothing more is written, what
    waited is dropped, and `on_stall` is told (Proc stops the worker, so its
    step goes the resume way)."""

    def __init__(self, worker: Worker, on_stall: Optional[Callable[[str], None]] = None,
                 cap: int = 0, stall: float = 0.0):
        self.worker = worker
        self.on_stall = on_stall
        self.cap = cap or INPUT_CAP
        self.stall = stall or STALL
        self.stalled = ""                   # why writing stopped, once it has
        self._fd: Optional[int] = None
        self._pending = bytearray()
        self._since = 0.0                   # when the fifo last took something, while some waited
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._timer: Optional[asyncio.TimerHandle] = None

    @property
    def waiting(self) -> int:
        return len(self._pending)

    def _open(self) -> int:
        if self._fd is None:
            try:
                self._fd = os.open(self.worker.dir / "in", os.O_WRONLY | os.O_NONBLOCK)
            except OSError as e:
                raise BrokenPipeError(f"the worker isn't reading: {e}") from None
        return self._fd

    @staticmethod
    def _put(fd: int, data: Any) -> int:
        try:
            return os.write(fd, data)
        except (BlockingIOError, InterruptedError):
            return 0

    def write(self, data: bytes) -> None:
        if self.stalled:
            raise InputStalled(self.stalled)
        fd = self._open()
        if len(self._pending) > self.cap:
            # one message of any size goes; a backlog past the cap doesn't grow
            self._stop(f"more than {self.cap // 1024} KB of its input waiting")
            raise InputStalled(self.stalled)
        if not self._pending:
            data = data[self._put(fd, data):]
            if not data:
                return
            self._since = time.monotonic()
        self._pending += data
        self._arm()

    def _arm(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._wait_out()                # not on a loop: a thread may wait, briefly
            return
        if self._loop is None and self._fd is not None:
            self._loop = loop
            loop.add_writer(self._fd, self._flush)
            self._timer = loop.call_later(self.stall, self._check)

    def _disarm(self) -> None:
        if self._loop is not None and self._fd is not None:
            try:
                self._loop.remove_writer(self._fd)
            except (ValueError, RuntimeError):
                pass
        if self._timer is not None:
            self._timer.cancel()
        self._loop = self._timer = None

    def _flush(self) -> None:
        if self._fd is None:
            return
        try:
            n = self._put(self._fd, self._pending)
        except OSError as e:
            self._stop(f"its input closed: {e}")
            return
        if n:
            del self._pending[:n]
            self._since = time.monotonic()
        if not self._pending:
            self._disarm()

    def _check(self) -> None:
        self._timer = None
        if not self._pending or self._loop is None:
            return
        idle = time.monotonic() - self._since
        if idle >= self.stall:
            self._stop(f"it took none of its input for {idle:.0f}s")
        else:
            self._timer = self._loop.call_later(self.stall - idle, self._check)

    def _wait_out(self) -> None:
        while self._pending and self._fd is not None and not self.stalled:
            idle = time.monotonic() - self._since
            if idle >= self.stall:
                self._stop(f"it took none of its input for {idle:.0f}s")
                raise InputStalled(self.stalled)
            select.select([], [self._fd], [], min(1.0, self.stall - idle))
            self._flush()

    def _stop(self, why: str) -> None:
        self.stalled = f"the worker stopped reading: {why}"
        log.warning("%s: %s", self.worker.id, self.stalled)
        self.close()
        if self.on_stall is not None:
            try:
                self.on_stall(self.stalled)
            except Exception:               # noqa: BLE001 — noticing never blocks the engine
                log.exception("stalled worker %s", self.worker.id)

    def close(self) -> None:
        self._disarm()
        self._pending.clear()
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


class Proc:
    """A worker in the shape of asyncio's Process — stdin, stdout, stderr,
    returncode, wait, kill — so a session talks to one the way it talked to
    its child. `detached`: the engine going away leaves it running."""

    detached = True

    def __init__(self, worker: Worker, offset: int = 0):
        self.worker = worker
        self.stdin = _In(worker, on_stall=self._stalled)
        self.stdout = _Out(worker, "out", offset)
        self.stderr = _Out(worker, "err")
        self._code: Optional[int] = None
        hold(worker.id)

    @property
    def pid(self) -> int:
        return self.worker.pid

    @property
    def returncode(self) -> Optional[int]:
        if self._code is None and self.worker.ended():
            code = self.worker.exit
            # gone without a word: cut off
            self._code = -int(signal.SIGKILL) if code is None else code
        return self._code

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(POLL * 2)
        return self._code                                   # type: ignore[return-value]

    @property
    def stalled(self) -> str:
        """Why it was stopped for not reading its input; "" if it wasn't."""
        return self.stdin.stalled

    def _stalled(self, why: str) -> None:
        """It stopped reading: stopped, so its session ends as cut off and
        its step is taken up again (eki/steps.py) — not left deaf."""
        st = self.worker.state
        st.setdefault("why", why)
        try:
            _write_json(self.worker.dir / "state.json", st)
        except OSError:
            pass
        self.worker.signal(signal.SIGTERM)

    def terminate(self) -> None:
        self.worker.signal(signal.SIGTERM)

    def kill(self) -> None:
        self.worker.signal(signal.SIGKILL)

    def detach(self) -> None:
        """Stop reading and writing; the program carries on."""
        self.stdin.close()
        self.stdout.close()
        self.stderr.close()
        release(self.worker.id)


async def start_proc(argv: List[str], cwd: Optional[str], env: Optional[Dict[str, str]],
                     **meta: Any) -> Proc:
    """A program the engine talks to, started as a worker."""
    w = await asyncio.to_thread(start, argv, cwd=cwd, env=env, talk=True, **meta)
    # the keeper opens `in` before it starts the program: once it has said
    # the program's pid, there is someone to write to
    deadline = time.time() + STARTING
    while time.time() < deadline and not w.state.get("pid") and w.exit is None:
        await asyncio.sleep(0.01)
    return Proc(w)


def attach(worker: Worker) -> Proc:
    """A worker from before a restart, read again from where the engine was."""
    pos, _ = worker.reading()
    return Proc(worker, pos)


class Reading:
    """How far a session has got through its worker's output, kept in the
    worker's folder so the next engine reads on from there — no line twice,
    and nothing the program is waiting on dropped.

    `seen` is the end of the last line fully handled. A request the program
    made that isn't answered yet (a permission, a tool call to eki's own
    server) holds the read-again point at its line, so a new engine reads it
    again and answers it; of the lines up to `seen`, only those requests are
    handled again (`again`) — never one already answered."""

    def __init__(self, proc: Any):
        self.proc = proc if isinstance(proc, Proc) else None
        pos, seen = self.proc.worker.reading() if self.proc else (0, 0)
        self.seen = max(pos, seen)
        self._owed: Dict[str, int] = {}
        #: what the engine before this one still owed, when it went
        self._was_owed = set(_read_json(self.proc.worker.dir / "owed")) if self.proc else set()
        self._line = (0, 0)

    def before_line(self) -> None:
        if self.proc:
            self._line = (self.proc.stdout.offset, self._line[1])

    def after_line(self) -> Tuple[int, int]:
        """(start, end) of the line just read."""
        if self.proc:
            self._line = (self._line[0], self.proc.stdout.offset)
        return self._line

    def replaying(self) -> bool:
        """The line just read was handled by an engine before this one."""
        return bool(self.proc) and self._line[1] <= self.seen

    def again(self, key: Any) -> bool:
        """Of a line read again: a request still owed, to be handled now."""
        return str(key) in self._was_owed

    def owe(self, key: Any) -> None:
        if self.proc:
            self._owed.setdefault(str(key), self._line[0])
            self._save(owed=True)

    def paid(self, key: Any) -> None:
        self._was_owed.discard(str(key))
        if self._owed.pop(str(key), None) is not None:
            self._save(owed=True)

    def handled(self, end: Optional[int]) -> None:
        if self.proc and end is not None and end > self.seen:
            self.seen = end
            self._save()

    def _save(self, owed: bool = False) -> None:
        pos = min([self.seen, *self._owed.values()])
        try:
            if owed:
                _write_json(self.proc.worker.dir / "owed", dict(self._owed))  # type: ignore[union-attr]
            self.proc.worker.note_reading(pos, self.seen)           # type: ignore[union-attr]
        except OSError:
            pass


class Tagged(asyncio.Queue):
    """A session's event queue: each event carries where its line ended, so
    the reading moves on only once the event has been handled."""

    pos: Optional[int] = None

    def put_nowait(self, item: Any) -> None:
        if isinstance(item, dict) and self.pos is not None:
            item.setdefault("_pos", self.pos)
        super().put_nowait(item)


def owners(workers: Iterable[Worker]) -> Dict[str, List[Worker]]:
    """Workers by the thread they serve."""
    out: Dict[str, List[Worker]] = {}
    for w in workers:
        cid = str(w.spec.get("thread") or "")
        if cid:
            out.setdefault(cid, []).append(w)
    return out


# ---- the keeper --------------------------------------------------------------------------

def _relay(fifo: int, feed: int, cap: int) -> None:
    """Drain `in` always, and pass it on as the program reads. Up to `cap`
    waits here; past it the keeper stops draining, `in` fills, and the
    engine sees a program that stopped reading. Once the program is gone,
    what comes is dropped."""
    held: collections.deque = collections.deque()
    size = [0]
    cond = threading.Condition()

    def take() -> None:
        while True:
            with cond:
                while size[0] >= cap:
                    cond.wait()
            try:
                chunk = os.read(fifo, 65536)
            except InterruptedError:
                continue
            except OSError:
                return
            if not chunk:
                return
            with cond:
                held.append(chunk)
                size[0] += len(chunk)
                cond.notify_all()

    def give() -> None:
        gone = False
        while True:
            with cond:
                while not held:
                    cond.wait()
                chunk = held.popleft()
            view = memoryview(chunk)
            while view and not gone:
                try:
                    view = view[os.write(feed, view):]
                except InterruptedError:
                    continue
                except OSError:
                    gone = True
            with cond:
                size[0] -= len(chunk)
                cond.notify_all()

    for fn in (take, give):
        threading.Thread(target=fn, daemon=True).start()


def _keep(d: Path) -> int:
    """Start the program, stay its parent, write down how it ended. The
    signal that stops the worker reaches the program too (one session); the
    keeper outlives it long enough to say so."""
    spec = _read_json(d / "spec.json")
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *a: None)
    state: Dict[str, Any] = {"keeper": os.getpid(), "keeper_started": started_at(os.getpid())}
    fifo = -1
    if spec.get("talk"):
        # the program reads a pipe of the keeper's; `in` is drained into it
        fifo = os.open(d / "in", os.O_RDWR)
        stdin, feed = os.pipe()
    else:
        stdin, feed = os.open(os.devnull, os.O_RDONLY), -1
    out_path = Path(spec["out"]) if spec.get("out") else d / "out"
    out = open(out_path, "ab")
    err = out if spec.get("merge") else open(d / "err", "ab")
    try:
        proc = subprocess.Popen(spec["argv"], stdin=stdin, stdout=out, stderr=err,
                                cwd=spec.get("cwd") or None)
    except OSError as e:
        err.write(f"couldn't start {spec.get('argv')}: {e}\n".encode())
        err.flush()
        state.update(exit=127, ended=time.time())
        _write_json(d / "state.json", state)
        return 127
    if fifo >= 0:
        os.close(stdin)
        _relay(fifo, feed, int(spec.get("input_cap") or INPUT_CAP))
    state.update(pid=proc.pid, started=started_at(proc.pid), at=time.time())
    _write_json(d / "state.json", state)
    while True:
        try:
            code = proc.wait()
            break
        except InterruptedError:
            continue
    state.update(exit=code, ended=time.time())
    _write_json(d / "state.json", state)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "keep":
        sys.exit(_keep(Path(sys.argv[2])))
    sys.exit(2)
