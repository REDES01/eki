# SPDX-License-Identifier: Apache-2.0
"""Is this checkout of eki fit to replace the one that's running?

eki changes itself by handing its own source to an agent. Before anything an
agent wrote is allowed near the running engine, it has to prove itself here,
as a candidate: on a spare port, in a home directory of its own, against a
provider that costs nothing.

The checks, in order — the first failure stops the rest:

  tests       the checkout's own test suite passes
  boots       the engine starts clean in an empty home
  answers     a question goes in, is routed, and the answer comes back
  data        it comes up on a copy of your real database and sees the same
              conversations
  leavable    the code running now can still open that copy afterwards, so
              going back is possible
  restart     a restart in the middle of a run and of a check loses nothing
              (eki/drill.py, the quick drill)

Nothing here touches your `~/.eki`, your logins or your quota. The candidate
runs with HOME pointed at a throwaway directory, which is where every
`~/.eki/...` path in the code then lands; the only provider it is given is a
stub this file serves itself; the database it sees is a copy with its
schedules switched off.

Standard library only, on purpose: the checker must run whatever state the
candidate's dependencies are in.

    python -m eki.candidate /path/to/checkout
    python -m eki.candidate /path/to/checkout --json
    python -m eki.candidate . --skip tests

See docs/self-build.md for where this sits.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import steps                                   # standard library only, too
from . import workers                                 # and this
from . import drill                                   # and this

ORDER = ("tests", "app", "boots", "answers", "data", "leavable", "restart")
STUB_MODEL = "candidate-stub"
TERMINAL = ("done", "failed", "cancelled", "interrupted")


# ---- the report --------------------------------------------------------

@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0
    skipped: bool = False


@dataclass
class Report:
    checkout: str
    checks: List[Check] = field(default_factory=list)

    @property
    def fit(self) -> bool:
        """Fit means something was actually checked and none of it failed."""
        ran = [c for c in self.checks if not c.skipped]
        return bool(ran) and all(c.ok for c in ran)

    def to_json(self) -> Dict[str, Any]:
        return {"checkout": self.checkout, "fit": self.fit,
                "checks": [asdict(c) for c in self.checks]}

    def lines(self) -> List[str]:
        out = [f"candidate: {self.checkout}"]
        for c in self.checks:
            mark = "skip" if c.skipped else ("ok  " if c.ok else "FAIL")
            took = f" ({c.seconds:.1f}s)" if c.seconds >= 0.05 else ""
            out.append(f"  {mark}  {c.name:9}{took}  {c.detail}".rstrip())
        out.append("fit to run" if self.fit else "not fit to run")
        return out


# ---- the stub provider -------------------------------------------------

class Stub:
    """An OpenAI-compatible server that says one thing.

    Enough of /v1/models and /v1/chat/completions (streamed or not) for
    eki's `openai_compat` adapter. What it says carries a nonce, so an
    answer that comes back is known to have come through here.
    """

    def __init__(self, words: Optional[str] = None):
        self.words = words or f"candidate-ok-{uuid.uuid4().hex[:8]}"
        self.calls = 0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:       # quiet
                pass

            def _json(self, code: int, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:                      # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    self._json(200, {"object": "list",
                                     "data": [{"id": STUB_MODEL, "object": "model"}]})
                else:
                    self._json(404, {"error": "not here"})

            def do_POST(self) -> None:                     # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    body = {}
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._json(404, {"error": "not here"})
                    return
                stub.calls += 1
                if not body.get("stream"):
                    self._json(200, {"choices": [{"index": 0, "finish_reason": "stop",
                                                  "message": {"role": "assistant",
                                                              "content": stub.words}}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                half = len(stub.words) // 2
                for piece in (stub.words[:half], stub.words[half:]):
                    event = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
                usage = {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
                self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> "Stub":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


# ---- pieces --------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed_config(stub_url: str) -> str:
    """A config.yaml with one provider: the stub. Read once, on first boot."""
    return (
        "db_path: ~/.eki/eki.db\n"
        "quota:\n"
        f"  url: {stub_url}/no-quota-here\n"
        "backends:\n"
        "  - key: stub\n"
        "    kind: openai_compat\n"
        "    label: Candidate stub\n"
        "    cost: {tier: 0, note: \"candidate check\"}\n"
        "    capabilities: {context_tokens: 8000}\n"
        f"    options: {{base_url: \"{stub_url}\", model: {STUB_MODEL}}}\n"
    )


def copy_db(source: Path, dest: Path) -> None:
    """A consistent copy of a database that may be in use, made harmless.

    SQLite's backup API reads a live file safely. In the copy, schedules are
    switched off — a candidate that fired them would do your scheduled work
    a second time.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
            tables = {r[0] for r in out.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "schedules" in tables:
                out.execute("UPDATE schedules SET enabled = 0")
            out.commit()
        finally:
            out.close()
    finally:
        src.close()


def python_for(checkout: Path, given: Optional[str] = None) -> str:
    """The checkout's own venv if it has one, else whatever is running this."""
    if given:
        return given
    own = checkout / ".venv" / "bin" / "python"
    return str(own) if own.exists() else sys.executable


def _get(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _post(url: str, body: Dict[str, Any], timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _tail(path: Path, lines: int = 8) -> str:
    try:
        text = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " | ".join(text[-lines:])[:600]


class Candidate:
    """The checkout's engine, running on a spare port in a home of its own."""

    def __init__(self, checkout: Path, python: str, home: Path, config: Path,
                 boot_seconds: float = 45.0):
        self.checkout, self.python, self.home, self.config = checkout, python, home, config
        self.boot_seconds = boot_seconds
        self.port = free_port()
        self.log = home / "engine.log"
        #: the candidate engine runs as a worker (eki/workers.py): a restart
        #: of the engine checking it leaves it be, and a check taken up
        #: again stops the one it left before starting its own
        self.worker: Optional[workers.Worker] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("EKI_")}
        env.update({
            "HOME": str(self.home),                    # every ~/.eki path lands here
            "EKI_CONFIG": str(self.config),
            "PYTHONPATH": str(self.checkout),
            "PYTHONDONTWRITEBYTECODE": "1",            # leave the checkout as found
            "PYTHONUNBUFFERED": "1",
        })
        return env

    def start(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        key = workers.key_of("candidate", os.path.realpath(self.checkout), self.home.name)
        for old in workers.find(key=key):
            old.kill(why="its check was taken up again")
            old.collect()
        self.worker = workers.start(
            [self.python, "-m", "eki.cli", "-c", str(self.config), "serve", "--port", str(self.port)],
            cwd=str(self.checkout), env=self.env(), out=self.log, merge=True,
            kind="candidate", key=key)
        deadline = time.time() + self.boot_seconds
        while time.time() < deadline:
            if not self.worker.alive(strict=False):
                raise RuntimeError(f"exited with {self.worker.exit} while starting: "
                                   f"{_tail(self.log)}")
            try:
                if (_get(f"{self.url}/api/health", timeout=2) or {}).get("ok"):
                    return
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.3)
        raise RuntimeError(f"not healthy after {self.boot_seconds:.0f}s: {_tail(self.log)}")

    def stop(self) -> None:
        if self.worker is None:
            return
        self.worker.kill(grace=10, why="checked")
        self.worker.collect()

    def __enter__(self) -> "Candidate":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


# ---- the checks ------------------------------------------------------------

def check_tests(checkout: Path, python: str, timeout: float = 1200.0) -> str:
    if not (checkout / "tests").is_dir():
        raise RuntimeError("no tests directory")
    env = {k: v for k, v in os.environ.items() if not k.startswith("EKI_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        # a worker (eki/workers.py): a restart doesn't cut it off, and the
        # check taken up again joins it — or takes its result, if it
        # finished while no engine was up — rather than starting over
        code, out, err = workers.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                                     key=_tests_key(checkout, python), cwd=str(checkout), env=env,
                                     timeout=timeout, kind="tests")
    except TimeoutError:
        raise RuntimeError(f"still running after {timeout:.0f}s") from None
    last = ([ln for ln in out.strip().splitlines() if ln.strip()] or ["no output"])[-1]
    if steps.cut_off(code):
        # stopped by a signal — a cancel, or lost with nothing written: a
        # check cut off, not tests that failed (2026-09-24: "tests ✗", a row of dots)
        raise steps.Interrupted(f"the tests were cut off (exit {code})")
    if code != 0:
        failed = [ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR"))]
        more = "; ".join(failed[:5]) or err.strip()[-300:]
        raise RuntimeError(f"{last.strip('= ')} — {more}"[:600])
    return last.strip("= ")


def _tests_key(checkout: Path, python: str) -> str:
    """The same tests of the same code: the folder, its commit and what's
    uncommitted in it — so a check joins only a run of exactly what it checks."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True,
                                  text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    return workers.key_of("pytest", os.path.realpath(checkout), python,
                          git("rev-parse", "HEAD"), git("status", "--porcelain"))


def check_answers(cand: Candidate, stub: Stub, timeout: float = 60.0) -> str:
    got = _post(f"{cand.url}/api/ask", {"prompt": "Reply with the word you were given."})
    rid = (got or {}).get("run")
    if not rid:
        raise RuntimeError(f"/api/ask gave no run: {got!r}"[:300])
    deadline = time.time() + timeout
    run: Dict[str, Any] = {}
    while time.time() < deadline:
        run = _get(f"{cand.url}/api/runs/{rid}") or {}
        if run.get("state") in TERMINAL:
            break
        time.sleep(0.3)
    state = run.get("state")
    if state != "done":
        why = run.get("error") or _tail(cand.log, 4)
        raise RuntimeError(f"run ended {state or 'nowhere'}: {why}"[:600])
    if stub.words not in (run.get("output") or ""):
        raise RuntimeError("the run finished but the stub's words aren't in its output: "
                           f"{(run.get('output') or '')[:200]!r}")
    return f"routed to {run.get('backend') or '?'}, answered through the run store"


def conversations_now(db: Path) -> int:
    """How many conversations the code running *this* sees in `db`."""
    from .store import Store
    return len(Store(db).conversations(1_000_000))


def check_data(cand: Candidate, expected: int) -> str:
    rows = _get(f"{cand.url}/api/conversations?limit=1000000", timeout=30)
    if not isinstance(rows, list):
        raise RuntimeError(f"/api/conversations gave {type(rows).__name__}, not a list")
    if len(rows) != expected:
        raise RuntimeError(f"the running code sees {expected} conversations, "
                           f"the candidate sees {len(rows)}")
    runs = _get(f"{cand.url}/api/runs?limit=5", timeout=30)
    if not isinstance(runs, list):
        raise RuntimeError("/api/runs did not list")
    return f"{expected} conversations, all there"


def check_leavable(db: Path, expected: int) -> str:
    """After the candidate has had the copy, can the code running now still
    open it? If not, rolling back would strand the data."""
    from .providers import ProviderStore
    from .runs import RunStore
    RunStore(db).recent(5)                  # not the owner: declares nothing dead
    ProviderStore(db).all()
    seen = conversations_now(db)
    if seen != expected:
        raise RuntimeError(f"had {expected} conversations before the candidate, {seen} after")
    return "the running code still reads it"


def check_restart(checkout: Path, python: str) -> str:
    """The rule "a restart at any moment loses nothing", tried on this
    checkout: its engine restarted in the middle of a stub run and of a
    candidate check (eki/drill.py, `quick`). A change that breaks it is
    never fit."""
    began = time.time()
    results = drill.quick(checkout, python)
    bad = [r for r in results if not r.ok]
    if bad:
        raise RuntimeError("; ".join(f"{r.work}, restarted {r.point}: {'; '.join(r.problems)}"
                                     for r in bad)[:600])
    return (f"restarted mid-answer and mid-check: nothing lost or doubled "
            f"({len(results)} cases, {time.time() - began:.0f}s)")


def skipped_here() -> set:
    """Checks this environment leaves out (EKI_CHECK_SKIP, comma-separated):
    the drill's own sandboxed engines don't run a drill inside a drill."""
    return {s.strip() for s in os.environ.get("EKI_CHECK_SKIP", "").split(",") if s.strip()}


# ---- putting it together ---------------------------------------------------

def default_db() -> Path:
    return Path("~/.eki/eki.db").expanduser()


def check_app(root: Path, report: "Report", step: Callable[[str, Callable[[], str]], bool],
              skipping: set) -> None:
    """The Mac app compiles — checked only when mac/ differs from what the
    installed app was built from (eki/appbuild.py); a Mac without Xcode's
    tools can't tell, and says so."""
    from . import appbuild
    if "app" in skipping or not appbuild.sources_hash(root):
        report.checks.append(Check("app", True, "no app here" if "app" not in skipping
                                   else "skipped on request", skipped=True))
        return
    if not appbuild.changed(root):
        report.checks.append(Check("app", True, "the app is unchanged", skipped=True))
        return
    import shutil
    if not shutil.which("swiftc"):                   # pragma: no cover — a Mac without Xcode's tools
        report.checks.append(Check("app", True, "no swiftc here to compile the app", skipped=True))
        return
    step("app", lambda: appbuild.typecheck(root))


def check(checkout: Path | str, *, python: Optional[str] = None,
          db: Optional[Path | str] = None, skip: Iterable[str] = (),
          keep: bool = False, say: Optional[Callable[[str], None]] = None) -> Report:
    """Run the checks against `checkout`. Never raises for a failing
    candidate — that is a report with `fit` false."""
    root = Path(checkout).expanduser().resolve()
    report = Report(checkout=str(root))
    skipping = set(skip) | skipped_here()
    say = say or (lambda _line: None)
    stopped = False

    def step(name: str, fn: Callable[[], str]) -> bool:
        nonlocal stopped
        if name in skipping:
            report.checks.append(Check(name, True, "skipped on request", skipped=True))
            return True
        if stopped:
            report.checks.append(Check(name, True, "not reached", skipped=True))
            return False
        say(f"{name}…")
        began = time.time()
        try:
            detail, ok = fn(), True
        except steps.Interrupted:
            raise                                     # cut off: no verdict at all
        except Exception as e:                        # noqa: BLE001 — a verdict, not a crash
            detail, ok = f"{type(e).__name__}: {e}" if not isinstance(e, RuntimeError) else str(e), False
        report.checks.append(Check(name, ok, detail, round(time.time() - began, 2)))
        stopped = stopped or not ok
        return ok

    if not (root / "eki" / "cli.py").exists():
        report.checks.append(Check("checkout", False, "no eki/cli.py here — not a checkout of eki"))
        return report

    py = python_for(root, python)
    work = Path(tempfile.mkdtemp(prefix="eki-candidate-"))
    try:
        step("tests", lambda: check_tests(root, py))
        check_app(root, report, step, skipping)

        # a fresh install: empty home, the stub as its only provider
        with Stub() as stub:
            home = work / "fresh"
            home.mkdir()
            cfg = work / "fresh.yaml"
            cfg.write_text(seed_config(stub.base_url))
            cand = Candidate(root, py, home, cfg)

            def boots() -> str:
                cand.start()
                return f"healthy on :{cand.port}, in an empty home"

            def answers() -> str:
                if cand.worker is None:               # boots was skipped
                    cand.start()
                return check_answers(cand, stub)

            try:
                step("boots", boots)
                step("answers", answers)
            finally:
                cand.stop()

            # an upgrade: the same, on a copy of the real data
            source = Path(db).expanduser() if db else default_db()
            if "data" in skipping:
                # leavable is a question about what the candidate did to the copy
                for name in ("data", "leavable"):
                    report.checks.append(Check(name, True, "skipped on request", skipped=True))
            elif not source.exists():
                note = f"no database at {source} to try"
                report.checks.append(Check("data", True, note, skipped=True))
                report.checks.append(Check("leavable", True, note, skipped=True))
            else:
                home2 = work / "upgrade"
                copy = home2 / ".eki" / "eki.db"
                expected = -1

                def with_data() -> str:
                    nonlocal expected
                    copy_db(source, copy)
                    expected = conversations_now(copy)
                    cfg2 = work / "upgrade.yaml"
                    cfg2.write_text(seed_config(stub.base_url))
                    with Candidate(root, py, home2, cfg2) as second:
                        return check_data(second, expected)

                step("data", with_data)
                step("leavable", lambda: check_leavable(copy, expected))
        step("restart", lambda: check_restart(root, py))
    finally:
        if keep:
            say(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="eki.candidate", description=__doc__.split("\n\n")[0])
    ap.add_argument("checkout", help="a checkout of eki to judge")
    ap.add_argument("--python", help="interpreter for the candidate "
                    "(default: the checkout's .venv, else this one)")
    ap.add_argument("--db", help="database to try the upgrade on (default: ~/.eki/eki.db)")
    ap.add_argument("--skip", action="append", default=[], choices=ORDER,
                    help="leave a check out; may be repeated")
    ap.add_argument("--keep", action="store_true", help="keep the sandbox for a look")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    quiet = args.json
    try:
        report = check(args.checkout, python=args.python, db=args.db, skip=args.skip,
                       keep=args.keep,
                       say=None if quiet else (lambda line: print(line, file=sys.stderr, flush=True)))
    except steps.Interrupted as e:
        print(f"cut off, not judged: {e}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print("\n".join(report.lines()))
    return 0 if report.fit else 1


if __name__ == "__main__":
    raise SystemExit(main())
