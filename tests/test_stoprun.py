"""Runs go through eki/stopkind.py: a limit waits, a broken setup fails saying what to fix,
a lost worker is resumed free — and none of it costs an item a try."""
import json
import subprocess
import time

import pytest

from eki import db, engine, notify, scheduler, selfwork, stopkind, store, worker, workspace
from eki.providers.base import Outcome
from conftest import run_inline

TRANSIENT = [
    "API Error: Connection dropped (ECONNRESET)",
    'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    "API Error: 500 Internal server error",
    "API Error: 503 upstream connect error",
    "request to https://api.anthropic.com/v1/messages failed, reason: socket hang up",
    "TypeError: fetch failed",
    "connect ETIMEDOUT 160.79.104.10:443",
    "stream error: network error",
    "api error: connection reset by peer",
]


@pytest.mark.parametrize("text", TRANSIENT)
def test_what_was_transient_still_is(text):
    stop = stopkind.classify(stopkind.Failure(state="failed", error=text, now=1000.0))
    assert stop.kind == "transient" and stop.retry_at == 1000.0 + stopkind.TRANSIENT_WAITS[0]


@pytest.mark.parametrize("text", ["", "fake failure", "checks failed", "API Error: 400 invalid_request_error"])
def test_lasting_errors_are_judgment(text):
    assert stopkind.classify(stopkind.Failure(state="failed", error=text, now=1.0)).kind == "judgment"


def test_a_failing_check_is_judgment_not_a_crash():
    for text in ("exited 1", "Traceback (most recent call last):\n  timed out"):
        stop = stopkind.classify(stopkind.Failure(state="failed", error=text, provider="command", now=1.0))
        assert stop.kind == "judgment"
    gone = "couldn't start bin/check: [Errno 2] No such file or directory: '/x/wt'"
    assert stopkind.classify(stopkind.Failure(state="failed", error=gone, provider="command")).kind == "environment"


def ask(conn, prompt, **kw):
    with db.tx(conn):
        tid = store.create_thread(conn, prompt, None)
        rid = store.create_run(conn, tid, prompt, **kw)
    return tid, rid


def notify_on(home):
    data = json.loads((home / "routing.json").read_text())
    data["notify"] = {"topic": "stoprun-topic"}
    (home / "routing.json").write_text(json.dumps(data))


def notices(conn):
    return [dict(r) for r in conn.execute("SELECT key, kind, body FROM notices")]


def _repo(r):
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    (r / "eki" / "x.py").write_text("# x\n")
    (r / "bin" / "check").write_text("#!/bin/sh\nexit 0\n")
    (r / "bin" / "check").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def test_a_pinned_limit_waits_for_its_reset_then_runs_and_costs_no_try(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("EKI_SOURCE", str(_repo(tmp_path / "src")))
    gid = selfwork.submit(conn, "a goal", plan=False, files=["eki/f.py"])
    iid = conn.execute("SELECT id FROM items WHERE goal_id=?", (gid,)).fetchone()[0]
    scheduler.start_ready(conn)
    it = conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    assert it["state"] == "building"
    rid, tries = it["run_id"], it["tries"]
    store.update_run(conn, rid, prompt="limit steps=1", provider="fake", pinned=1)
    monkeypatch.setenv("EKI_FAKE_LIMITED", "fake")
    before = time.time()
    r = run_inline(conn, rid)
    assert r["state"] == "queued" and r["provider"] == "fake" and "reset" in r["why"]
    assert before + 59 <= r["retry_at"] <= time.time() + 61       # the fake's limit resets in 60 s
    assert json.loads(r["stop"])["kind"] == "quota" and json.loads(r["stop"])["retry_at"] == r["retry_at"]
    assert store.cooling(conn)["fake"]["until"] == r["retry_at"]
    spawned = []
    monkeypatch.setattr(engine, "spawn", lambda c, x: spawned.append(x))
    engine.spawn_ready(conn)
    assert rid not in spawned                                       # not before its reset
    store.update_run(conn, rid, retry_at=time.time() - 1)
    engine.spawn_ready(conn)
    assert rid in spawned
    monkeypatch.setenv("EKI_FAKE_LIMITED", "nobody")
    conn.execute("DELETE FROM cooldowns")
    assert run_inline(conn, rid)["state"] == "done"
    assert conn.execute("SELECT tries FROM items WHERE id=?", (iid,)).fetchone()[0] == tries


def test_a_long_quota_says_so_once_and_never_asks(conn, home):
    notify_on(home)
    now = time.time()
    rids = [ask(conn, f"q{i}", provider="claude")[1] for i in range(2)]
    for rid in rids:
        store.update_run(conn, rid, state="running")
        out = Outcome(state="limited", error="You've hit your weekly limit · resets Thu 9am",
                      reset_at=float(int(now) + 3 * 86400))
        worker.finish(conn, store.run(conn, rid), "claude", out)
        r = store.run(conn, rid)
        assert r["state"] == "queued" and r["retry_at"] == int(now) + 3 * 86400
        assert json.loads(r["stop"])["kind"] == "quota"
    got = notices(conn)
    assert [n["kind"] for n in got] == ["heads_up"]
    assert got[0]["key"] == f"quota:claude:{int(now) + 3 * 86400}"
    assert got[0]["body"].startswith("Claude Code's weekly limit resets ") and "waits till then" in got[0]["body"]
    assert "heads_up" in notify.KINDS and "heads_up" in notify.DEFAULTS["events"]


def test_a_lost_worker_is_resumed_free_up_to_the_cap(conn):
    _, rid = ask(conn, "steps=50 delay=1")
    for n in range(1, stopkind.LOST_CAP + 2):
        proc = subprocess.Popen(["sleep", "60"])                   # its worker, killed
        proc.kill()
        proc.wait()
        store.update_run(conn, rid, state="running", pid=proc.pid, heartbeat=1.0, attempt=n)
        engine.reap(conn)
        r, stop = store.run(conn, rid), json.loads(store.run(conn, rid)["stop"])
        assert r["state"] == "queued" and r["ended_at"] is None and r["pid"] is None
        if n <= stopkind.LOST_CAP:
            assert stop["kind"] == "lost" and stop["n"] == n and not r["retry_at"]
        else:                                                       # past the cap: the transient rule
            assert stop["kind"] == "transient" and r["retry_at"] > time.time() + 30
    assert not [e for e in store.events_after(conn, rid) if e["kind"] == "state"
                and json.loads(e["data"])["state"] == "failed"]


def test_a_missing_folder_fails_saying_what_to_fix(conn, home):
    notify_on(home)
    _, rid = ask(conn, "x", provider="claude")
    store.update_run(conn, rid, state="running")
    err = "FileNotFoundError: [Errno 2] No such file or directory: '/Users/x/gone/wt'"
    worker.finish(conn, store.run(conn, rid), "claude", Outcome(state="failed", error=err))
    r = store.run(conn, rid)
    stop = json.loads(r["stop"])
    assert r["state"] == "failed" and stop["kind"] == "environment"
    assert "/Users/x/gone/wt" in stop["fix"] and r["error"].startswith(stop["fix"])
    assert not notices(conn)                         # the item's hold asks, not the run
