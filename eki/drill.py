# SPDX-License-Identifier: Apache-2.0
"""The restart drill: is "a restart at any moment loses nothing" still true?

The rule (ROADMAP.md, docs/self-build.md "A restart loses nothing") is easy
to state and easy to break without noticing: a new code path that holds a
pipe, a step not written down, a check that reads a cut-off test run as a
failure. So it is proven the only way that counts — by restarting a real
engine in the middle of real work, over and over, and looking at what's left.

Each case is one kind of work, cut off at one point:

    chat          a stub model streaming an answer
    agent chat    Claude Code (the fake from the tests) in a thread
    folder        Codex (the fake) working in a folder
    self-work     a change to eki through its steps: base check, agent
                  turn, candidate check, merge-queue apply, go-live
    resolve       a change whose rebase conflicts, resolved by the agent
    models        a local model server eki started

and after each it asks: did the work finish (nothing lost)? Did anything
happen twice — two programs at once, a line said twice, a second commit?
Was anything reported failed, or "couldn't resolve"? Is a step still shown
working when nothing is? Does the thread say the engine restarted?

Nothing of yours is touched. Every case runs in a sandbox: its own HOME
(so every `~/.eki` path lands there), a spare port, a copy of eki's code as
its "source", stub providers and the fake Claude Code and Codex from
tests/, and a scratch launchd job — `launchctl kickstart -k` restarts it the
way a swap restarts yours. The go-live's supervisor is the real one, pointed
at the scratch job (EKI_JOB) with a short watch.

    python -m eki.drill               # the full drill: a table, exit 0 if all ok
    python -m eki.drill --quick       # the candidate check's 'restart' (< 1 min)
    eki self drill

Standard library only, like eki/candidate.py: the quick drill is part of
judging a checkout whatever state its dependencies are in.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import signal
import socket
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
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import workers

TERMINAL = ("done", "failed", "cancelled", "interrupted")
#: what a thread must never say after a restart: a cut-off reported as a failure
BAD_WORDS = ("couldn't resolve", "tests ✗", "✗", "not fit to run", "gave up", "run ended failed")
#: where the drill's result is kept, for the weekly note (`last`)
RESULT = Path("~/.eki/self/drill.json").expanduser()
#: the full drill runs this often (the loop's housekeeping)
EVERY = 7 * 86400


# ---- the report ------------------------------------------------------------------------

@dataclass
class Result:
    work: str
    point: str
    problems: List[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_json(self) -> Dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def table(results: List[Result]) -> List[str]:
    """step × restart point → ok, or what went wrong."""
    w = max([len(r.work) for r in results] + [4])
    p = max([len(r.point) for r in results] + [5])
    out = [f"{'work':{w}}  {'restart':{p}}  result", f"{'-' * w}  {'-' * p}  ------"]
    for r in results:
        out.append(f"{r.work:{w}}  {r.point:{p}}  " + ("ok" if r.ok else "; ".join(r.problems))[:400])
    bad = sum(not r.ok for r in results)
    out.append(f"{len(results) - bad} of {len(results)} ok" if results else "nothing drilled")
    return out


def save(results: List[Result], quick: bool = False, path: Optional[Path] = None) -> Dict[str, Any]:
    got = {"at": int(time.time()), "quick": quick, "ok": all(r.ok for r in results) and bool(results),
           "results": [r.to_json() for r in results]}
    path = path or RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(got, indent=2, ensure_ascii=False))
    tmp.replace(path)
    return got


def last(path: Optional[Path] = None) -> Dict[str, Any]:
    """The last full drill's result, {} if none."""
    try:
        got = json.loads((path or RESULT).read_text())
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def due(now: Optional[float] = None, path: Optional[Path] = None) -> bool:
    """A week since the last full drill (or one never run)."""
    at = float(last(path).get("at") or 0)
    return (now or time.time()) - at >= EVERY


#: the weekly drill's worker, whichever engine started it
WEEKLY = "restart-drill-weekly"
#: a weekly drill that didn't finish (killed, a crash) is tried again only this long after
RETRY = 12 * 3600


def weekly(python: Optional[str] = None, code: Optional[Path] = None, now: Optional[float] = None,
           path: Optional[Path] = None, home: Optional[Path] = None) -> str:
    """The loop's housekeeping: the full drill once a week. It runs as a
    worker (eki/workers.py) — it takes minutes and restarts engines of its
    own, so it mustn't be a part of this one — and keeps its result for the
    weekly note. "running", "started" or "" (not due)."""
    now = now or time.time()
    path = path or RESULT
    for w in workers.find(home, key=WEEKLY):
        if w.alive():
            workers.hold(w.id)                  # this engine's, not an orphan to sweep
            return "running"
    if not due(now, path):
        return ""
    tried = path.with_name("drill.tried")
    try:
        if now - float(tried.read_text().strip() or 0) < RETRY:
            return ""
    except (OSError, ValueError):
        pass
    tried.parent.mkdir(parents=True, exist_ok=True)
    tried.write_text(str(int(now)))
    code = Path(code or Path(__file__).resolve().parent.parent)
    env = {k: v for k, v in os.environ.items() if not k.startswith("EKI_")}
    env["PYTHONPATH"] = str(code)
    workers.start([python or sys.executable, "-m", "eki.drill", "--save", "--json"], cwd=str(code),
                  env=env, merge=True, home=home, kind="drill", key=WEEKLY, run="")
    return "started"


def for_note(got: Optional[Dict[str, Any]] = None) -> str:
    """The last full drill, for the weekly note: its table, as it ran."""
    got = last() if got is None else got
    rows = [Result(r.get("work", ""), r.get("point", ""), list(r.get("problems") or []),
                   float(r.get("seconds") or 0)) for r in got.get("results") or []]
    if not rows or got.get("quick"):
        return ""
    when = time.strftime("%Y-%m-%d", time.localtime(float(got.get("at") or 0)))
    head = "all ok" if all(r.ok for r in rows) else f"{sum(not r.ok for r in rows)} not ok"
    return (f"**Restart drill** ({when}, `eki self drill`): {head} — the engine restarted in the "
            "middle of each kind of work, in a sandbox.\n\n```\n" + "\n".join(table(rows)) + "\n```")


# ---- the stub model ----------------------------------------------------------------------

class Stub:
    """An OpenAI-compatible model that says `w1 w2 … wN`, a word every
    `delay` seconds — slow enough to be cut off in the middle."""

    def __init__(self, words: int = 20, delay: float = 0.25):
        self.words = [f"w{i}" for i in range(1, words + 1)]
        self.delay = delay
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _json(self, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:                      # noqa: N802
                self._json({"object": "list", "data": [{"id": "drill-stub", "object": "model"}]})

            def do_POST(self) -> None:                     # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
                whole = " ".join(stub.words)
                if not body.get("stream"):
                    self._json({"choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": whole}}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for i, word in enumerate(stub.words):
                        piece = word if i == 0 else " " + word
                        event = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(stub.delay)
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except OSError:
                    pass                                   # the engine went away mid-answer

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def answer(self) -> str:
        return " ".join(self.words)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# ---- how the engine is run and restarted -----------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launchctl(*args: str) -> Tuple[int, str]:
    try:
        got = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return got.returncode, (got.stdout + got.stderr).strip()


def launchd_here() -> bool:
    return sys.platform == "darwin" and shutil.which("launchctl") is not None


class Launchd:
    """A scratch launchd job for the sandbox's engine, set up like yours
    (eki/agent.py): kept alive, and its stop doesn't take the programs it
    started (AbandonProcessGroup). A restart is `kickstart -k`, the way the
    supervisor restarts yours."""

    def __init__(self, label: str, argv: List[str], env: Dict[str, str], cwd: str, log: Path,
                 plist: Path):
        self.label, self.argv, self.env, self.cwd, self.log, self.plist = label, argv, env, cwd, log, plist

    @property
    def job(self) -> str:
        return f"gui/{os.getuid()}/{self.label}"

    def start(self) -> None:
        with self.plist.open("wb") as f:
            plistlib.dump({"Label": self.label, "ProgramArguments": self.argv,
                           "WorkingDirectory": self.cwd, "RunAtLoad": True, "KeepAlive": True,
                           "ThrottleInterval": 1, "AbandonProcessGroup": True,
                           "StandardOutPath": str(self.log), "StandardErrorPath": str(self.log),
                           "EnvironmentVariables": self.env}, f)
        code, out = _launchctl("bootstrap", f"gui/{os.getuid()}", str(self.plist))
        if code != 0:
            raise RuntimeError(f"launchd refused the drill's job: {out}")

    def pid(self) -> int:
        code, out = _launchctl("print", self.job)
        m = re.search(r"^\s*pid = (\d+)", out, re.M) if code == 0 else None
        return int(m.group(1)) if m else 0

    def restart(self) -> None:
        code, out = _launchctl("kickstart", "-k", self.job)
        if code != 0:
            raise RuntimeError(f"kickstart failed: {out}")

    def stop(self) -> None:
        _launchctl("bootout", self.job)
        for _ in range(100):
            if _launchctl("print", self.job)[0] != 0:
                return
            time.sleep(0.1)


class Process:
    """The same, without launchd (the quick drill; a Mac without it): a
    restart is what `kickstart -k` does to a job that abandons its process
    group — TERM to the engine alone, then start it again."""

    def __init__(self, argv: List[str], env: Dict[str, str], cwd: str, log: Path):
        self.argv, self.env, self.cwd, self.log = argv, env, cwd, log
        self.proc: Optional[subprocess.Popen] = None
        self.job = ""

    def start(self) -> None:
        with self.log.open("a") as out:
            self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
                                         stdout=out, stderr=out, start_new_session=True)

    def pid(self) -> int:
        return self.proc.pid if self.proc is not None and self.proc.poll() is None else 0

    def _end(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def restart(self) -> None:
        self._end()
        self.start()

    def stop(self) -> None:
        self._end()


# ---- the sandbox --------------------------------------------------------------------------

TINY_TEST = '''"""The drill's own test suite: one test that takes a moment, so a check
can be cut off in the middle of it."""
import time


def test_takes_a_moment():
    time.sleep({seconds})
'''

MODEL_SERVER = '''"""A stand-in local model server (MLX, llama.cpp, ComfyUI), for the drill."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = json.dumps({{"object": "list", "data": [{{"id": "drill-local"}}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''

SUPERVISOR = '''#!/bin/sh
# the drill's supervisor: the real one (a copy), with a short watch
exec /bin/bash "{real}" "$1" "$2" "{watch}" "$4"
'''

FAKE = '''#!/bin/sh
# the fake {name} from eki's tests, for the drill; anything but its
# streaming mode ({mode}) is a version check
case " $* " in
  *" {mode} "*) exec "{python}" "{script}" "$@" ;;
esac
echo "{name} 9.9.9 (the drill's fake)"
'''

#: the settings of a sandboxed engine: nothing learned, measured, watched or said
QUIET = {"skills_learn": "off", "auto_measure": "off", "notify_learned": False, "notify_goals": False,
         "claude_tools": False, "claude_screen": False, "self_fix": "off", "claude_probe": False}


def _git(where: Path, *args: str) -> str:
    got = subprocess.run(["git", "-C", str(where), "-c", "user.name=eki drill",
                          "-c", "user.email=drill@localhost", *args],
                         capture_output=True, text=True, timeout=60)
    if got.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(got.stderr or got.stdout).strip()[-300:]}")
    return got.stdout.strip()


def _get(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _send(url: str, body: Dict[str, Any], method: str = "POST", timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _listener(port: int) -> int:
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return 0
    return int(out[0]) if out and out[0].isdigit() else 0


class Sandbox:
    """One engine of eki's, run from a copy of `code`, in a home of its own,
    on a spare port, with stub and fake providers only."""

    def __init__(self, code: Path, python: str, *, launchd: bool = True, test_seconds: float = 4.0,
                 watch: int = 15):
        self.code, self.python = Path(code).resolve(), python
        self.root = Path(tempfile.mkdtemp(prefix="eki-drill-", dir="/tmp")).resolve()
        self.home = self.root / "home"
        self.eki = self.home / ".eki"
        self.source = self.root / "source"
        self.bin = self.root / "bin"
        self.config = self.root / "config.yaml"
        self.log = self.root / "engine.log"
        self.port = free_port()
        self.model_port = free_port()
        self.stub = Stub()
        self.test_seconds, self.watch = test_seconds, watch
        self._build()
        env = self._env()
        argv = [python, "-m", "eki.cli", "-c", str(self.config), "serve", "--port", str(self.port)]
        cwd = str(self.eki / "builds" / "current")
        self.job: Any = Launchd(f"local.eki.drill.{uuid.uuid4().hex[:8]}", argv, env, cwd, self.log,
                                self.root / "job.plist") if launchd else Process(argv, env, cwd, self.log)
        if launchd:
            env["EKI_JOB"] = self.job.job
        self.pids: List[int] = []

    # -- setting it up

    def _build(self) -> None:
        for d in (self.eki / "bin", self.eki / "watch", self.eki / "builds", self.bin):
            d.mkdir(parents=True, exist_ok=True)
        # the fakes from the code's own tests — or, for a checkout without
        # them, the drill's own: they speak the programs' protocols, not eki's
        tests = self.code / "tests"
        if not (tests / "fake_claude.py").exists():
            tests = Path(__file__).resolve().parent.parent / "tests"
        fakes = {"claude": ("--input-format", tests / "fake_claude.py"),
                 "codex": ("app-server", tests / "fake_codex.py")}
        for name, (mode, script) in fakes.items():
            if not script.exists():
                raise RuntimeError(f"no {script.name} in {tests} — the drill needs eki's tests beside it")
            path = self.bin / name
            path.write_text(FAKE.format(name=name, mode=mode, python=self.python, script=script))
            path.chmod(0o755)
        (self.bin / "model_server.py").write_text(MODEL_SERVER)
        # the source eki works on: a copy of this code, with a test suite that takes a moment
        shutil.copytree(self.code / "eki", self.source / "eki",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (self.source / "tests").mkdir()
        (self.source / "tests" / "test_drill.py").write_text(TINY_TEST.format(seconds=self.test_seconds))
        (self.source / "README.md").write_text("eki, copied for the restart drill\n")
        (self.source / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n")
        (self.home / ".gitconfig").write_text("[user]\n\tname = eki drill\n\temail = drill@localhost\n")
        _git(self.source, "init", "-q", "-b", "main")
        _git(self.source, "add", "-A")
        _git(self.source, "commit", "-q", "-m", "eki, for the restart drill")
        os.symlink(str(self.source), str(self.eki / "builds" / "current"))
        (self.root / "supervisor.sh").write_text((self.code / "eki" / "supervisor.sh").read_text())
        sup = self.eki / "bin" / "eki-supervisor"
        sup.write_text(SUPERVISOR.format(real=self.root / "supervisor.sh", watch=self.watch))
        sup.chmod(0o755)
        (self.eki / "settings.json").write_text(json.dumps(QUIET))
        # the model watch reads the web once a day: not from here
        (self.eki / "watch" / "state.json").write_text(json.dumps({"at": time.time() + 10 * 365 * 86400}))
        self.config.write_text(self._config())

    def _config(self) -> str:
        py, server = self.python, self.bin / "model_server.py"
        return (
            "db_path: ~/.eki/eki.db\n"
            "quota:\n"
            f"  url: {self.stub.url}/no-quota-here\n"
            "backends:\n"
            "  - key: stub\n"
            "    kind: openai_compat\n"
            "    label: Drill stub\n"
            "    cost: {tier: 0, note: \"restart drill\"}\n"
            "    capabilities: {context_tokens: 8000}\n"
            f"    options: {{base_url: \"{self.stub.url}\", model: drill-stub}}\n"
            "  - key: claude\n"
            "    kind: claude_code\n"
            "    label: Claude Code (the drill's fake)\n"
            "    cost: {tier: 50}\n"
            "    capabilities: {context_tokens: 200000, repo: true, tools: true}\n"
            f"    options: {{binary: \"{self.bin / 'claude'}\"}}\n"
            "  - key: codex\n"
            "    kind: codex\n"
            "    label: Codex (the drill's fake)\n"
            "    cost: {tier: 50}\n"
            "    capabilities: {context_tokens: 200000, repo: true, tools: true}\n"
            f"    options: {{binary: \"{self.bin / 'codex'}\"}}\n"
            "  - key: localm\n"
            "    kind: openai_compat\n"
            "    label: Drill local model\n"
            "    cost: {tier: 0}\n"
            "    capabilities: {context_tokens: 8000}\n"
            f"    options: {{base_url: \"http://127.0.0.1:{self.model_port}/v1\", model: drill-local}}\n"
            "local_models:\n"
            "  - key: localm\n"
            "    backend: localm\n"
            f"    port: {self.model_port}\n"
            f"    start: \"nohup {py} {server} {self.model_port} >/dev/null 2>&1 &\"\n"
            f"    stop: \"kill $(lsof -tiTCP:{self.model_port} -sTCP:LISTEN) 2>/dev/null\"\n"
        )

    def _env(self) -> Dict[str, str]:
        current = str(self.eki / "builds" / "current")
        return {
            "HOME": str(self.home),                       # every ~/.eki path lands here
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
            "PYTHONPATH": current, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "EKI_SOURCE": str(self.source), "EKI_CONFIG": str(self.config),
            "EKI_HEALTH_URL": f"{self.url}/api/health",   # the supervisor watches this engine…
            "EKI_JOB": "gui/0/local.eki.drill.none",      # …and restarts this job (set below)
            "EKI_CHECK_SKIP": "restart",                  # no drill inside the drill's checks
            "EKI_NO_NOTIFY": "1",                         # nothing on your screen
            "LANG": "en_US.UTF-8",
        }

    # -- running it

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def health(self) -> Dict[str, Any]:
        try:
            return _get(f"{self.url}/api/health", timeout=2) or {}
        except (OSError, ValueError, urllib.error.URLError):
            return {}

    def up(self, not_pid: int = 0, within: float = 60.0) -> Dict[str, Any]:
        deadline = time.time() + within
        while time.time() < deadline:
            h = self.health()
            if h.get("ok") and h.get("pid") != not_pid:
                self.pids.append(int(h["pid"]))
                return h
            time.sleep(0.2)
        raise RuntimeError(f"the engine didn't come up within {within:.0f}s: {self.tail()}")

    def start(self) -> None:
        self.job.start()
        self.up()
        (self.root / "job.json").write_text(json.dumps({"job": self.job.job, "pid": self.pids[-1]}))

    def restart(self) -> None:
        was = int(self.health().get("pid") or 0)
        self.job.restart()
        self.up(not_pid=was)
        (self.root / "job.json").write_text(json.dumps({"job": self.job.job, "pid": self.pids[-1]}))

    def tail(self, lines: int = 6) -> str:
        try:
            return " | ".join(self.log.read_text(errors="replace").strip().splitlines()[-lines:])[:500]
        except OSError:
            return ""

    def get(self, path: str) -> Any:
        return _get(self.url + path, timeout=15)

    def post(self, path: str, body: Dict[str, Any]) -> Any:
        return _send(self.url + path, body)

    def turns(self, cid: str) -> List[Dict[str, Any]]:
        try:
            return (self.get(f"/api/conversations/{cid}") or {}).get("turns") or []
        except (OSError, ValueError, urllib.error.URLError):
            return []

    def runs(self, cid: str) -> List[Dict[str, Any]]:
        rows = self.get("/api/runs?limit=500") or []
        return sorted((r for r in rows if r.get("conversation_id") == cid),
                      key=lambda r: (r.get("created_at") or 0, r.get("id")))

    def work(self) -> List[workers.Worker]:
        return workers.scan(self.eki / "work")

    def steps(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads((self.eki / "self" / "steps.json").read_text())
        except (OSError, ValueError):
            return {}
        return data.get("steps") or {} if isinstance(data, dict) else {}

    def swap(self) -> Dict[str, Any]:
        try:
            return json.loads((self.eki / "self" / "swap.json").read_text())
        except (OSError, ValueError):
            return {}

    def close(self, keep: bool = False) -> None:
        try:
            self.job.stop()
        finally:
            for w in self.work():                         # the programs the engine left
                if w.alive(strict=False):
                    w.signal(signal.SIGKILL)
            pid = _listener(self.model_port)
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            try:
                sup = int((self.eki / "self" / "swap.pid").read_text().strip() or 0)
                if sup:
                    os.killpg(sup, signal.SIGTERM)
            except (OSError, ValueError):
                pass
            self.stub.close()
            if not keep:
                shutil.rmtree(self.root, ignore_errors=True)


def sweep_stale(older: float = 30 * 60, now: Optional[float] = None,
                base: Path = Path("/tmp")) -> List[str]:
    """Sandboxes a drill left behind — its process killed mid-case (a check
    cut off by a restart of your engine): their engine, the programs it
    started and the folder go. Only this long after they were last touched."""
    now = now or time.time()
    gone = []
    for root in base.glob("eki-drill-*"):
        try:
            if now - root.stat().st_mtime < older:
                continue
            job = json.loads((root / "job.json").read_text()) if (root / "job.json").exists() else {}
        except (OSError, ValueError):
            job = {}
        if str(job.get("job") or "").startswith("gui/"):
            _launchctl("bootout", job["job"])
        pid = int(job.get("pid") or 0)
        if pid and pid in _pids_under(root):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        for w in workers.scan(root / "home" / ".eki" / "work"):
            if w.alive(strict=False):
                w.signal(signal.SIGKILL)
        shutil.rmtree(root, ignore_errors=True)
        gone.append(str(root))
    return gone


def _pids_under(root: Path) -> List[int]:
    """Processes whose command line names `root` — so a pid reused by
    something else is never killed."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line.split(None, 1)[0]) for line in out.splitlines()
            if str(root) in line and line.split(None, 1)[0].isdigit()]


# ---- watching a case --------------------------------------------------------------------

class Watch:
    """Looks at the sandbox while a case runs: two copies of one program
    alive at once is work done twice."""

    def __init__(self, sb: Sandbox):
        self.sb = sb
        self.doubled: List[str] = []

    def look(self) -> None:
        alive: Dict[Tuple[str, str], List[str]] = {}
        for w in self.sb.work():
            if not w.alive(strict=False):
                continue
            spec = w.spec
            kind = str(spec.get("kind") or "?")
            if kind not in ("claude", "codex", "tests", "candidate", "git"):
                continue
            who = str(spec.get("key") or spec.get("thread") or w.id)
            alive.setdefault((kind, who), []).append(w.id)
        for (kind, _), ids in alive.items():
            if len(ids) > 1:
                what = f"two {kind} programs at once"
                if what not in self.doubled:
                    self.doubled.append(what)

    def until(self, what: str, fn: Callable[[], Any], within: float, also: Callable[[], None] = lambda: None
              ) -> Any:
        deadline = time.time() + within
        while time.time() < deadline:
            also()
            self.look()
            try:
                got = fn()
            except (OSError, ValueError, urllib.error.URLError, KeyError):
                got = None
            if got:
                return got
            time.sleep(0.25)
        raise TimeoutError(what)


def _said(turns: List[Dict[str, Any]]) -> str:
    return "\n".join(str(t.get("content") or "") for t in turns if t.get("role") == "assistant")


def _once(text: str, tokens: List[str]) -> List[str]:
    """Each token said exactly once: missing is lost, twice is doubled."""
    problems = []
    counts = {t: len(re.findall(rf"(?<![\w]){re.escape(t)}(?![\w])", text)) for t in tokens}
    lost = [t for t, n in counts.items() if n == 0]
    twice = [t for t, n in counts.items() if n > 1]
    if lost:
        problems.append(f"lost {len(lost)} of {len(tokens)} lines ({lost[0]}…)")
    if twice:
        problems.append(f"said {len(twice)} lines twice ({twice[0]}…)")
    return problems


def thread_problems(sb: Sandbox, cid: str) -> List[str]:
    """What the thread says after it: the restart, where it cut a run off
    (a go-live waiting for a quiet moment may cut off nothing), and no
    failure."""
    problems = []
    turns = sb.turns(cid)
    said = _said(turns)
    runs = sb.runs(cid)
    if any(r.get("state") == "interrupted" for r in runs) and "restarted" not in said:
        problems.append("the thread doesn't say the engine restarted")
    bad = [w for w in BAD_WORDS if w in said]
    if bad:
        problems.append(f"the thread says {bad[0]!r}")
    failed = [r for r in runs if r.get("state") == "failed"]
    if failed:
        problems.append(f"a run reported failed: {str(failed[-1].get('error') or '')[:160]}")
    return problems


def settled(sb: Sandbox, cid: str) -> Optional[Dict[str, Any]]:
    """The thread's newest run, once nothing in it is going any more."""
    runs = sb.runs(cid)
    if not runs or any(r.get("state") not in TERMINAL for r in runs):
        return None
    view = sb.get(f"/api/conversations/{cid}") or {}
    return None if view.get("active_run") else runs[-1]


# ---- the kinds of work ------------------------------------------------------------------

class Work:
    """One kind of work: how it's started, the points a restart cuts it at,
    when it's finished and what must hold then."""

    name = ""
    points: Tuple[str, ...] = ()
    within = 90.0

    def begin(self, sb: Sandbox) -> Dict[str, Any]:
        raise NotImplementedError

    def at(self, sb: Sandbox, ctx: Dict[str, Any], point: str) -> bool:
        raise NotImplementedError

    def during(self, sb: Sandbox, ctx: Dict[str, Any]) -> None:
        pass

    def cuts(self, point: str) -> bool:
        """A restart at `point` cuts a run of the thread off (else the drill
        missed its moment, and proved nothing)."""
        return True

    def finished(self, sb: Sandbox, ctx: Dict[str, Any]) -> Any:
        return settled(sb, ctx["conversation"])

    def verify(self, sb: Sandbox, ctx: Dict[str, Any], end: Any) -> List[str]:
        raise NotImplementedError


class Chat(Work):
    """A model answering in a thread — the stub, a word at a time."""
    name = "chat"
    points = ("before the first word", "mid-answer")
    within = 60.0

    def begin(self, sb: Sandbox) -> Dict[str, Any]:
        got = sb.post("/api/ask", {"prompt": "Say the drill's words.", "backend": "stub"})
        return {"conversation": got["conversation"], "run": got["run"]}

    def at(self, sb: Sandbox, ctx: Dict[str, Any], point: str) -> bool:
        run = sb.get(f"/api/runs/{ctx['run']}") or {}
        if point == "before the first word":
            return run.get("state") == "running"
        return len(str(run.get("output") or "").split()) >= 4

    def verify(self, sb: Sandbox, ctx: Dict[str, Any], end: Any) -> List[str]:
        problems = []
        if end.get("state") != "done":
            problems.append(f"the answer was left {end.get('state')}: {str(end.get('error') or '')[:120]}")
        return problems + _once(_said(sb.turns(ctx["conversation"])), sb.stub.words) + \
            thread_problems(sb, ctx["conversation"])


class AgentChat(Work):
    """Claude Code in a thread (the fake), mid-turn."""
    name = "agent chat"
    points = ("as it starts", "mid-turn")
    backend, kind, count = "claude", "claude", 30

    def begin(self, sb: Sandbox) -> Dict[str, Any]:
        got = sb.post("/api/ask", {"prompt": f"[drill count {self.count}] count for the drill",
                                   "backend": self.backend, **self.extra(sb)})
        return {"conversation": got["conversation"], "run": got["run"]}

    def extra(self, sb: Sandbox) -> Dict[str, Any]:
        return {}

    def at(self, sb: Sandbox, ctx: Dict[str, Any], point: str) -> bool:
        if point == "as it starts":
            return True
        run = sb.get(f"/api/runs/{ctx['run']}") or {}
        return "d5 " in str(run.get("output") or "")

    def verify(self, sb: Sandbox, ctx: Dict[str, Any], end: Any) -> List[str]:
        cid = ctx["conversation"]
        problems = []
        if end.get("state") != "done":
            problems.append(f"the turn was left {end.get('state')}: {str(end.get('error') or '')[:120]}")
        programs = [w for w in sb.work() if w.spec.get("kind") == self.kind and w.spec.get("thread") == cid]
        if len(programs) > 1:
            problems.append(f"{len(programs)} {self.kind} programs started for one thread")
        return problems + _once(_said(sb.turns(cid)), [f"d{i}" for i in range(1, self.count + 1)]) + \
            thread_problems(sb, cid)


class Folder(AgentChat):
    """Codex (the fake) working in a folder, mid-turn."""
    name = "folder"
    points = ("mid-turn",)
    backend, kind = "codex", "codex"

    def extra(self, sb: Sandbox) -> Dict[str, Any]:
        folder = sb.root / "folder"
        folder.mkdir(exist_ok=True)
        (folder / "notes.txt").write_text("a folder for the drill\n")
        return {"repo": str(folder)}


class Models(Work):
    """A local model server eki started (MLX, llama.cpp, ComfyUI): a restart
    must leave it running, and the new engine must know it's eki's to stop."""
    name = "models"
    points = ("loaded", "its record lost")
    within = 30.0

    def begin(self, sb: Sandbox) -> Dict[str, Any]:
        sb.post("/api/models/localm/start", {})
        return {}

    def at(self, sb: Sandbox, ctx: Dict[str, Any], point: str) -> bool:
        if not _port_open(sb.model_port):
            return False
        ctx["pid"] = _listener(sb.model_port)
        if point == "its record lost":
            # the list of what eki started, gone (a crash mid-write, a
            # cleanup): the server's own environment still says so
            (sb.eki / "started.json").unlink(missing_ok=True)
        return bool(ctx["pid"])

    def cuts(self, point: str) -> bool:
        return False

    def finished(self, sb: Sandbox, ctx: Dict[str, Any]) -> Any:
        return sb.get("/api/models")

    def verify(self, sb: Sandbox, ctx: Dict[str, Any], end: Any) -> List[str]:
        problems = []
        if not _port_open(sb.model_port):
            problems.append("the model server went down with the engine")
        elif _listener(sb.model_port) != ctx.get("pid"):
            problems.append("the model server was started again, not kept")
        row = next((m for m in (end or {}).get("models") or [] if m.get("key") == "localm"), {})
        if not row.get("running"):
            problems.append("the new engine doesn't see it running")
        elif not row.get("started_by_hub"):
            problems.append("the new engine doesn't know eki started it (it would never unload it)")
        return problems


class SelfWork(Work):
    """A change to eki, through every step: its base checked, the agent's
    turn, the candidate check, the merge queue (put on top of a checkout that
    moved on, judged again) and the go-live — the supervisor swapping the
    new build in and the engine settling it."""
    name = "self-work"
    points = ("base check", "agent turn", "candidate check · tests", "candidate check · engine",
              "merge-queue apply", "go-live")
    within = 360.0
    count = 16
    apply = True
    check_base = True
    #: agent programs a case may start: one — a restart doesn't start another,
    #: and the resolve is the same thread's program
    agents = 1

    def begin(self, sb: Sandbox) -> Dict[str, Any]:
        got = sb.post("/api/self", {"request": f"[drill write drill.txt count {self.count}] "
                                               "add the drill's file\n\nA change for the restart drill.",
                                    "apply": self.apply, "check_base": self.check_base, "backend": "claude"})
        return {"conversation": got["conversation"], "run": got["run"], "item": got["item"], "moved": False}

    def _step(self, sb: Sandbox, kind: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return sb.steps().get(f"{kind}:{ctx['item']}") or {}

    def _alive(self, sb: Sandbox, kind: str) -> bool:
        return any(w.spec.get("kind") == kind and w.alive(strict=False) for w in sb.work())

    def at(self, sb: Sandbox, ctx: Dict[str, Any], point: str) -> bool:
        running = lambda k: self._step(sb, k, ctx).get("state") == "running"   # noqa: E731
        if point == "base check":
            return running("begin") and self._alive(sb, "tests")
        if point == "agent turn":
            run = sb.get(f"/api/runs/{ctx['run']}") or {}
            return running("agent") and "d4 " in str(run.get("output") or "")
        if point == "candidate check · tests":
            return running("check") and self._alive(sb, "tests")
        if point == "candidate check · engine":
            return running("check") and self._alive(sb, "candidate")
        if point == "merge-queue apply":
            return running("apply") and self._alive(sb, "tests")
        if point == "go-live":
            return sb.swap().get("state") == "waiting"
        return False

    def cuts(self, point: str) -> bool:
        return point != "go-live"               # the supervisor waits for a quiet moment

    def during(self, sb: Sandbox, ctx: Dict[str, Any]) -> None:
        """Once the agent is at it, the checkout moves on — so applying means
        putting the change on top and judging it again."""
        if not ctx["moved"] and self._step(sb, "agent", ctx):
            self.move(sb)
            ctx["moved"] = True

    def move(self, sb: Sandbox) -> None:
        (sb.source / "moved.txt").write_text("the checkout moved on while the change was made\n")
        _git(sb.source, "add", "moved.txt")
        _git(sb.source, "commit", "-q", "-m", "the checkout moves on")

    def _item(self, sb: Sandbox, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return sb.get(f"/api/self/items/{ctx['item']}") or {}

    def finished(self, sb: Sandbox, ctx: Dict[str, Any]) -> Any:
        it = self._item(sb, ctx)
        if it.get("state") in ("working", "queued") or not settled(sb, ctx["conversation"]):
            return None
        c = it.get("change_detail") or {}
        if self.apply and c.get("state") == "applying":
            return None                                 # the go-live hasn't settled yet
        return it

    def verify(self, sb: Sandbox, ctx: Dict[str, Any], end: Any) -> List[str]:
        problems = []
        c = end.get("change_detail") or {}
        want = "applied" if self.apply else "proposed"
        if c.get("state") != want:
            problems.append(f"the change ended {c.get('state') or 'nowhere'}"
                            + (f": {str(c.get('why') or c.get('verdict') or '')[:160]}" if c else ""))
        elif not c.get("fit"):
            problems.append("the change wasn't judged fit")
        if end.get("state") != ("done" if self.apply else "review"):
            problems.append(f"the item ended {end.get('state')}: {str(end.get('note') or '')[:120]}")
        for key, s in sb.steps().items():
            if s.get("state") in ("running", "interrupted"):
                problems.append(f"step {key.split(':')[0]} is still {s['state']} with nothing going")
            elif s.get("state") == "failed":
                problems.append(f"step {key.split(':')[0]} failed: {str(s.get('note') or '')[:120]}")
        view = sb.get("/api/self") or {}
        if view.get("working") or any(r.get("live") for r in view.get("merging") or []):
            problems.append("the board still shows it working")
        branch = c.get("branch") or f"self/{c.get('id')}"
        try:
            made = [s for s in _git(sb.source, "log", "--format=%s", branch).splitlines()
                    if s.startswith("self:")]
            if len(made) != 1:
                problems.append(f"{len(made)} commits for one change")
        except RuntimeError as e:
            problems.append(f"no branch for the change: {e}"[:160])
        if self.apply and c.get("state") == "applied":
            build = Path(str(c.get("build") or "")).name
            running = str(sb.health().get("build") or "")
            if build and running != build:
                problems.append(f"the engine runs {running}, not the change's build {build}")
        agent = [w for w in sb.work() if w.spec.get("kind") == "claude"]
        if len(agent) > self.agents:
            problems.append(f"{len(agent)} agent programs started, not {self.agents}")
        return problems + _once(_said(sb.turns(ctx["conversation"])), self.tokens()) + \
            thread_problems(sb, ctx["conversation"])

    def tokens(self) -> List[str]:
        return [f"d{i}" for i in range(1, self.count + 1)]


class Resolve(SelfWork):
    """A change whose rebase conflicts: the agent resolves it mid-rebase, and
    it goes on to be judged and go live."""
    name = "resolve"
    points = ("resolving",)

    def move(self, sb: Sandbox) -> None:
        (sb.source / "drill.txt").write_text("the checkout's own drill.txt\n")
        _git(sb.source, "add", "drill.txt")
        _git(sb.source, "commit", "-q", "-m", "the checkout adds drill.txt too")

    def at(self, sb: Sandbox, ctx: Dict[str, Any], point: str) -> bool:
        c = (self._item(sb, ctx).get("change_detail") or {})
        s = sb.steps().get(f"resolve:{c.get('id')}") or {}
        if s.get("state") != "running" or not s.get("run"):
            return False
        run = sb.get(f"/api/runs/{s['run']}") or {}
        return "d4 " in str(run.get("output") or "")

    def tokens(self) -> List[str]:
        return []                                   # it counts twice on purpose: the change, then the resolve


class QuickChat(Chat):
    points = ("mid-answer",)


class QuickCheck(SelfWork):
    """The quick drill's self-work: a candidate check cut off in its tests."""
    points = ("candidate check · tests",)
    within = 60.0
    apply = False
    check_base = False
    count = 6

    def during(self, sb: Sandbox, ctx: Dict[str, Any]) -> None:
        pass


FULL: List[Work] = [Chat(), AgentChat(), Folder(), Models(), SelfWork(), Resolve()]
QUICK: List[Work] = [QuickChat(), QuickCheck()]


# ---- running it ----------------------------------------------------------------------------

def drill_one(work: Work, point: str, code: Path, python: str, *, launchd: bool,
              keep: bool = False, test_seconds: float = 4.0) -> Result:
    """One case: a fresh sandbox, the work started, a restart at `point`, and
    what's left looked at."""
    began = time.time()
    res = Result(work.name, point)
    sb: Optional[Sandbox] = None
    try:
        sb = Sandbox(code, python, launchd=launchd, test_seconds=test_seconds)
        sb.start()
        look = Watch(sb)
        ctx = work.begin(sb)
        try:
            look.until(point, lambda: work.at(sb, ctx, point), work.within,
                       also=lambda: work.during(sb, ctx))
        except TimeoutError:
            res.problems.append(f"never reached “{point}” (the drill's timing, not a verdict)")
            return res
        sb.restart()
        try:
            end = look.until("finished", lambda: work.finished(sb, ctx), work.within,
                             also=lambda: work.during(sb, ctx))
        except TimeoutError:
            res.problems.append(f"never finished after the restart — {stuck(sb, ctx)}")
            return res
        res.problems += look.doubled + work.verify(sb, ctx, end)
        if work.cuts(point) and ctx.get("conversation") and \
                not any(r.get("state") == "interrupted" for r in sb.runs(ctx["conversation"])):
            res.problems.append("the restart cut nothing off (the drill's timing, not a verdict)")
    except Exception as e:                          # noqa: BLE001 — a case never stops the drill
        res.problems.append(f"the drill itself broke: {type(e).__name__}: {e}"[:300])
    finally:
        res.seconds = round(time.time() - began, 1)
        if sb is not None:
            keep_it = keep and not res.ok
            if keep_it:
                res.problems.append(f"sandbox kept: {sb.root}")
            sb.close(keep=keep_it)
    return res


def stuck(sb: Sandbox, ctx: Dict[str, Any]) -> str:
    """Where it stands, for a case that never finished."""
    bits = []
    cid = ctx.get("conversation")
    if cid:
        runs = sb.runs(cid)
        if runs:
            r = runs[-1]
            bits.append(f"its last run is {r.get('state')}" + (f" ({str(r.get('error'))[:100]})"
                                                               if r.get("error") else ""))
    open_steps = [f"{k.split(':')[0]} {s.get('state')}" for k, s in sb.steps().items()
                  if s.get("state") in ("running", "interrupted")]
    if open_steps:
        bits.append("steps: " + ", ".join(open_steps))
    if sb.swap():
        bits.append(f"swap {sb.swap().get('state')}")
    return "; ".join(bits) or sb.tail(3) or "no idea why"


def run(works: List[Work], code: Optional[Path] = None, python: Optional[str] = None, *,
        launchd: Optional[bool] = None, keep: bool = False, only: str = "",
        say: Optional[Callable[[str], None]] = None, test_seconds: float = 4.0) -> List[Result]:
    code = Path(code or Path(__file__).resolve().parent.parent)
    python = python or sys.executable
    launchd = launchd_here() if launchd is None else launchd
    say = say or (lambda _line: None)
    sweep_stale()
    out = []
    for work in works:
        if only and only not in work.name:
            continue
        for point in work.points:
            say(f"{work.name} · restart {point}…")
            r = drill_one(work, point, code, python, launchd=launchd, keep=keep, test_seconds=test_seconds)
            say(f"  {'ok' if r.ok else '; '.join(r.problems)} ({r.seconds:.0f}s)")
            out.append(r)
    return out


def quick(code: Path, python: Optional[str] = None) -> List[Result]:
    """The candidate check's 'restart': one restart in the middle of a stub
    run and one in the middle of a candidate check, under a minute. The
    engine is stopped the way launchd stops it (TERM, its programs left)."""
    return run(QUICK, code, python, launchd=False, test_seconds=3.0)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="eki.drill", description=__doc__.split("\n\n")[0])
    ap.add_argument("--quick", action="store_true", help="the candidate check's short drill")
    ap.add_argument("--only", default="", help="just the work whose name has this in it")
    ap.add_argument("--code", help="the eki to drill (default: this one)")
    ap.add_argument("--no-launchd", action="store_true", help="restart a plain process instead")
    ap.add_argument("--keep", action="store_true", help="keep a failing case's sandbox for a look")
    ap.add_argument("--save", action="store_true", help=f"keep the result in {RESULT}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    say = None if args.json else (lambda line: print(line, file=sys.stderr, flush=True))
    code = Path(args.code) if args.code else None
    if args.quick:
        results = run(QUICK, code, launchd=False, keep=args.keep, say=say, test_seconds=3.0)
    else:
        results = run(FULL, code, launchd=False if args.no_launchd else None, keep=args.keep,
                      only=args.only, say=say)
    if args.save:
        save(results, quick=args.quick)
    if args.json:
        print(json.dumps({"ok": all(r.ok for r in results) and bool(results),
                          "results": [r.to_json() for r in results]}, indent=2))
    else:
        print("\n".join(table(results)))
    return 0 if results and all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
