"""Gate 2's own checks: what unit tests can't cover (docs/self-build.md, "Judging").

Run as `python -m eki.candidate` from the fresh worktree being judged, after
`bin/check`. It judges the checkout it lives in:

- engine boots: that checkout's engine starts in an empty home on port 0 and
  `eki engine status` says it is up;
- opens a copy of the db: a copy of the real eki.db (sqlite3's backup API —
  the real one is only ever read) opened, and migrated, by that checkout
  lists the same number of threads;
- the running build opens the migrated copy: otherwise rollback would be a lie.

Every check runs in a throwaway home; each side runs `-m` from its own folder
with no inherited PYTHONPATH, so each imports its own eki.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

CHECKOUT = Path(__file__).resolve().parent.parent
BOOT_WAIT = 30.0
#: what a sandbox must not inherit: the running build's paths and home
DROP = ("PYTHONPATH", "EKI_HOME", "EKI_BUILD_DIR", "EKI_SOURCE", "EKI_PORT")

Result = Tuple[str, bool, str]


def _env(code: Path, home: Path) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DROP}
    env.update({"PYTHONPATH": str(code), "EKI_HOME": str(home), "EKI_PORT": "0", "EKI_MACHINE": "ok"})
    return env


def _home(parent: Path, name: str) -> Path:
    h = parent / name
    h.mkdir()
    # only the fake program: a sandbox engine must never ask the real CLIs anything
    (h / "providers.json").write_text(json.dumps({"fake": {"kind": "fake"}}))
    return h


def _run(python: str, code: Path, home: Path, args: List[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([python, *args], cwd=str(code), env=_env(code, home), capture_output=True,
                          text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def _tail(p: subprocess.CompletedProcess) -> str:
    out = (p.stderr or p.stdout or "").strip().splitlines()
    return f"exit {p.returncode}" + (f": {out[-1]}" if out else "")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
    proc.wait()


def engine_boots(checkout: Path, home: Path, python: str) -> str:
    """'' when the checkout's engine came up in `home`; else what went wrong."""
    # the checkout makes its empty db first: two processes turning a brand-new
    # file to WAL at once can fail with "database is locked"
    p = _run(python, checkout, home, ["-c", "from eki import db; db.connect()"])
    if p.returncode != 0:
        return f"the checkout couldn't make an empty db ({_tail(p)})"
    log = open(home / "candidate-engine.log", "wb")
    proc = subprocess.Popen([python, "-m", "eki.cli", "engine", "run"], cwd=str(checkout),
                            env=_env(checkout, home), stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    try:
        last = ""
        deadline = time.time() + BOOT_WAIT
        while time.time() < deadline:
            if proc.poll() is not None:
                tail = (home / "candidate-engine.log").read_text(errors="replace").strip().splitlines()
                return f"the engine exited {proc.returncode}" + (f": {tail[-1]}" if tail else "")
            p = _run(python, checkout, home, ["-m", "eki.cli", "engine", "status"], timeout=20)
            if p.returncode == 0 and "engine: up" in p.stdout:
                return ""
            last = p.stdout.strip().splitlines()[0] if p.stdout.strip() else _tail(p)
            time.sleep(0.5)
        return f"not up within {int(BOOT_WAIT)}s ({last or 'no answer'})"
    finally:
        _stop(proc)


def threads_in(db_path: Path) -> int:
    """Threads in a db, read without writing to it."""
    if not db_path.exists():
        return 0
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(c.execute("SELECT count(*) FROM threads").fetchone()[0])
    except sqlite3.OperationalError:
        return 0                                         # a db that never got the schema
    finally:
        c.close()


def copy_db(db_path: Path, dest: Path) -> None:
    """A consistent copy of a live db, even mid-write (sqlite3's backup API)."""
    out = sqlite3.connect(str(dest))
    try:
        if db_path.exists():
            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                src.backup(out)
            finally:
                src.close()
    finally:
        out.close()


COUNT = "from eki import db; print(db.connect().execute('SELECT count(*) FROM threads').fetchone()[0])"


def opens_copy(checkout: Path, db_path: Path, home: Path, python: str) -> str:
    copy_db(db_path, home / "eki.db")
    want = threads_in(db_path)
    p = _run(python, checkout, home, ["-c", COUNT])
    if p.returncode != 0:
        return f"the checkout couldn't open the copy ({_tail(p)})"
    got = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    if got != str(want):
        return f"the copy lists {got or 'nothing'} threads, the db has {want}"
    return ""


def running_opens(running: Path, home: Path, python: str) -> str:
    if not (running / "eki" / "cli").is_dir():
        return f"{running} isn't an eki checkout or build"
    p = _run(python, running, home, ["-m", "eki.cli", "threads"])
    return "" if p.returncode == 0 else f"the running build couldn't list threads ({_tail(p)})"


def check(checkout: Path, db_path: Path, running: Path, python: str) -> List[Result]:
    checkout, running, db_path = Path(checkout).resolve(), Path(running).resolve(), Path(db_path)
    out: List[Result] = []

    def one(name: str, fn: Callable[[], str]) -> bool:
        try:
            why = fn()
        except Exception as e:                           # noqa: BLE001 — a failed line, never a crash
            why = f"{type(e).__name__}: {e}"
        out.append((name, not why, why))
        return not why

    tmp = Path(tempfile.mkdtemp(prefix="eki-candidate-"))
    try:
        one("engine boots", lambda: engine_boots(checkout, _home(tmp, "boot"), python))
        copy = _home(tmp, "copy")
        opened = one("opens a copy of the db", lambda: opens_copy(checkout, db_path, copy, python))
        one("the running build opens the migrated copy",
            lambda: running_opens(running, copy, python) if opened
            else "skipped: the checkout didn't open the copy")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from . import builds, paths
    ap = argparse.ArgumentParser(prog="python -m eki.candidate", description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=None, help="the db to copy (default: this home's eki.db)")
    ap.add_argument("--running", type=Path, default=None, help="the build running now")
    ap.add_argument("--python", default=None, help="the interpreter for both sides")
    a = ap.parse_args(argv)
    db_path = a.db or paths.db()
    running = a.running or builds.current() or builds.source()
    results = check(CHECKOUT, db_path, running, a.python or builds.python())
    for name, ok, why in results:
        print(f"✓ {name}" if ok else f"✗ {name}: {why}")
    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
