# SPDX-License-Identifier: Apache-2.0
"""The candidate check, against checkouts that are fakes.

A real engine takes seconds to boot and needs a Mac; what's tested here is
the checker itself — that it isolates, stubs, copies, judges and cleans up —
using a stand-in `eki/cli.py` that speaks just the endpoints the checker
calls. `python -m eki.candidate .` against a real checkout is the other half.
"""
import json
import os
import sqlite3
import sys
import textwrap
import urllib.request
from pathlib import Path

import pytest

from eki import candidate
from eki import config as config_mod
from eki.store import Store

FAKE_CLI = textwrap.dedent('''
    """A stand-in engine: health, ask (through the configured provider), runs,
    conversations from $HOME/.eki/eki.db. MODE changes how it misbehaves."""
    import json, os, re, sqlite3, sys, urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    MODE = "__MODE__"
    args = sys.argv[1:]
    cfg = Path(args[args.index("-c") + 1]).read_text()
    port = int(args[args.index("--port") + 1])
    base = re.search(r'base_url: "([^"]+)"', cfg).group(1)
    db = Path(os.path.expanduser("~/.eki/eki.db"))
    Path(os.path.expanduser("~/.eki")).mkdir(parents=True, exist_ok=True)
    Path(os.path.expanduser("~/.eki/home-was")).write_text(os.environ["HOME"])
    RUNS = {}

    if MODE == "dies":
        print("ImportError: no module named surprise")
        raise SystemExit(3)
    if MODE == "drops-a-table" and db.exists():
        c = sqlite3.connect(db); c.execute("DROP TABLE conversations"); c.commit(); c.close()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def send(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/health": return self.send({"ok": True, "running": 0})
            if path == "/api/runs": return self.send(list(RUNS.values()))
            if path.startswith("/api/runs/"): return self.send(RUNS[path.rsplit("/", 1)[1]])
            if path == "/api/conversations":
                if not db.exists(): return self.send([])
                c = sqlite3.connect(db)
                rows = [{"id": r[0]} for r in c.execute("SELECT id FROM conversations")]
                if MODE == "loses-one": rows = rows[1:]
                return self.send(rows)
            self.send({}, 404)
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if MODE == "wrong-words":
                words = "something of my own"
            else:
                req = urllib.request.Request(base + "/chat/completions",
                    data=json.dumps({"messages": [], "stream": False}).encode(),
                    headers={"Content-Type": "application/json"})
                words = json.loads(urllib.request.urlopen(req).read())["choices"][0]["message"]["content"]
            RUNS["r1"] = {"id": "r1", "state": "done", "backend": "stub", "output": words}
            self.send({"run": "r1", "conversation": "c1"})

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
''')


def fake_checkout(tmp_path: Path, mode: str = "fine", tests_pass: bool = True) -> Path:
    root = tmp_path / f"checkout-{mode}"
    (root / "eki").mkdir(parents=True)
    (root / "eki" / "__init__.py").write_text("")
    (root / "eki" / "cli.py").write_text(FAKE_CLI.replace("__MODE__", mode))
    (root / "tests").mkdir()
    (root / "tests" / "test_it.py").write_text(
        f"def test_it():\n    assert {tests_pass}\n")
    return root


def real_db(tmp_path: Path, conversations: int = 3) -> Path:
    path = tmp_path / "real" / "eki.db"
    store = Store(path)
    for i in range(conversations):
        store.new_conversation(f"thread {i}")
    store.close()
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE schedules (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)")
    c.execute("INSERT INTO schedules VALUES ('s1', 1)")
    c.commit()
    c.close()
    return path


def by_name(report):
    return {c.name: c for c in report.checks}


# ---- the pieces ---------------------------------------------------------

def test_stub_speaks_enough_openai_for_the_adapter():
    with candidate.Stub("hello-there") as stub:
        models = json.loads(urllib.request.urlopen(f"{stub.base_url}/models").read())
        assert [m["id"] for m in models["data"]] == [candidate.STUB_MODEL]

        req = urllib.request.Request(
            f"{stub.base_url}/chat/completions",
            data=json.dumps({"messages": [], "stream": True}).encode(),
            headers={"Content-Type": "application/json"})
        said = ""
        for raw in urllib.request.urlopen(req).read().decode().splitlines():
            if raw.startswith("data:") and raw[5:].strip() not in ("", "[DONE]"):
                for choice in json.loads(raw[5:])["choices"]:
                    said += choice["delta"].get("content", "")
        assert said == "hello-there"
        assert stub.calls == 1


def test_each_stub_has_words_of_its_own():
    assert candidate.Stub().words != candidate.Stub().words


def test_seed_config_is_a_config_eki_can_read(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(candidate.seed_config("http://127.0.0.1:9/v1"))
    cfg = config_mod.load(path)
    assert [b.key for b in cfg.backends] == ["stub"]
    assert cfg.backends[0].kind == "openai_compat"
    assert cfg.backends[0].cost.tier == 0
    assert cfg.options["stub"] == {"base_url": "http://127.0.0.1:9/v1",
                                   "model": candidate.STUB_MODEL}
    # and the real tokenbar on :8777 is not what it would ask about quota
    assert "8777" not in cfg.quota_url


def test_copy_switches_schedules_off_and_leaves_the_original_alone(tmp_path):
    source = real_db(tmp_path)
    dest = tmp_path / "sandbox" / ".eki" / "eki.db"
    candidate.copy_db(source, dest)
    assert sqlite3.connect(dest).execute("SELECT enabled FROM schedules").fetchall() == [(0,)]
    assert sqlite3.connect(source).execute("SELECT enabled FROM schedules").fetchall() == [(1,)]
    assert candidate.conversations_now(dest) == 3


def test_python_is_the_checkouts_own_when_it_has_one(tmp_path):
    assert candidate.python_for(tmp_path) == sys.executable
    own = tmp_path / ".venv" / "bin" / "python"
    own.parent.mkdir(parents=True)
    own.write_text("")
    assert candidate.python_for(tmp_path) == str(own)
    assert candidate.python_for(tmp_path, "/somewhere/python") == "/somewhere/python"


def test_nothing_checked_is_not_fit():
    report = candidate.Report("x", [candidate.Check("tests", True, skipped=True)])
    assert not report.fit


# ---- the whole thing ------------------------------------------------------

def test_a_good_checkout_is_fit(tmp_path):
    report = candidate.check(fake_checkout(tmp_path), db=real_db(tmp_path))
    assert [c.name for c in report.checks] == list(candidate.ORDER)
    assert report.fit, report.lines()
    assert "3 conversations" in by_name(report)["data"].detail
    assert report.to_json()["fit"] is True


def test_the_candidate_never_sees_the_real_home(tmp_path):
    kept = []
    report = candidate.check(fake_checkout(tmp_path), db=real_db(tmp_path),
                             skip=["tests"], keep=True, say=kept.append)
    assert report.fit
    work = Path([line for line in kept if line.startswith("kept: ")][0][6:])
    for home in ("fresh", "upgrade"):
        assert (work / home / ".eki" / "home-was").read_text() == str(work / home)
    assert str(work) != os.path.expanduser("~")


def test_the_sandbox_is_gone_afterwards(tmp_path, monkeypatch):
    made = []
    real = candidate.tempfile.mkdtemp
    monkeypatch.setattr(candidate.tempfile, "mkdtemp",
                        lambda **kw: made.append(real(**kw)) or made[-1])
    candidate.check(fake_checkout(tmp_path), db=real_db(tmp_path), skip=["tests"])
    assert made and not Path(made[0]).exists()


def test_failing_tests_stop_everything_else(tmp_path):
    report = candidate.check(fake_checkout(tmp_path, tests_pass=False), db=real_db(tmp_path))
    checks = by_name(report)
    assert not report.fit
    assert not checks["tests"].ok and "failed" in checks["tests"].detail
    assert all(checks[n].skipped and checks[n].detail == "not reached"
               for n in ("boots", "answers", "data", "leavable"))


def test_an_engine_that_dies_says_why(tmp_path):
    report = candidate.check(fake_checkout(tmp_path, "dies"), skip=["tests"])
    boots = by_name(report)["boots"]
    assert not report.fit and not boots.ok
    assert "exited with 3" in boots.detail and "no module named surprise" in boots.detail


def test_an_answer_that_didnt_come_through_the_provider_fails(tmp_path):
    report = candidate.check(fake_checkout(tmp_path, "wrong-words"), skip=["tests"])
    assert not by_name(report)["answers"].ok
    assert "stub's words" in by_name(report)["answers"].detail


def test_a_candidate_that_loses_a_conversation_fails(tmp_path):
    report = candidate.check(fake_checkout(tmp_path, "loses-one"),
                             db=real_db(tmp_path), skip=["tests"])
    data = by_name(report)["data"]
    assert not data.ok and "sees 3" in data.detail and "candidate sees 2" in data.detail


def test_a_candidate_that_breaks_the_data_for_the_old_build_fails(tmp_path):
    """The check that makes rollback honest."""
    source = real_db(tmp_path)
    # the fake serves conversations from the table it then drops at next boot,
    # so give it a data check it passes and let leavable find the damage
    root = fake_checkout(tmp_path, "drops-a-table")
    cli = root / "eki" / "cli.py"
    cli.write_text(cli.read_text().replace(
        'if not db.exists(): return self.send([])',
        'return self.send([{"id": str(i)} for i in range(3)])'))
    report = candidate.check(root, db=source, skip=["tests"])
    checks = by_name(report)
    assert checks["data"].ok
    assert not checks["leavable"].ok and not report.fit
    # and the real database never noticed
    assert candidate.conversations_now(source) == 3


def test_no_database_yet_is_a_skip_not_a_failure(tmp_path):
    report = candidate.check(fake_checkout(tmp_path), db=tmp_path / "nothing.db",
                             skip=["tests"])
    assert report.fit
    assert by_name(report)["data"].skipped and by_name(report)["leavable"].skipped


def test_not_a_checkout(tmp_path):
    report = candidate.check(tmp_path)
    assert not report.fit and "not a checkout" in report.checks[0].detail


def test_command_line_exit_code_and_json(tmp_path, capsys):
    root = fake_checkout(tmp_path)
    code = candidate.main([str(root), "--json", "--skip", "tests",
                           "--db", str(tmp_path / "nothing.db")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["fit"] is True

    bad = fake_checkout(tmp_path, "dies")
    assert candidate.main([str(bad), "--skip", "tests"]) == 1
    assert "not fit to run" in capsys.readouterr().out
