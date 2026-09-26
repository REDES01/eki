"""The journal is written where things happen: the worker, the engine, the server."""
import json
import threading
import urllib.error
import urllib.request

from eki import api, db, engine, observe, queue, selfwork, server, store, train
from conftest import run_inline


def ask(conn, prompt, tid=None, **kw):
    with db.tx(conn):
        tid = tid or store.create_thread(conn, prompt, None)
        rid = store.create_run(conn, tid, prompt, **kw)
    return tid, rid


def test_a_done_run_leaves_one_run_row(conn):
    _, rid = ask(conn, "steps=1 hello")
    assert run_inline(conn, rid)["state"] == "done"
    rows = observe.entries(conn, kind="run")
    assert len(rows) == 1 and rows[0]["run_id"] == rid and rows[0]["provider"] == "fake"
    assert rows[0]["data"]["state"] == "done" and rows[0]["data"]["seconds"] >= 0
    assert rows[0]["data"]["handed_off"] is False and rows[0]["data"]["local"] is False
    assert not observe.entries(conn, kind="fault") and not observe.entries(conn, kind="correction")


def test_a_handoff_leaves_a_handoff_and_a_run_row(conn):
    tid, rid = ask(conn, "handoff please", provider="bare")
    assert run_inline(conn, rid)["state"] == "handed_off"
    nxt = [x for x in store.thread_runs(conn, tid) if x["parent"] == rid][0]
    h = observe.entries(conn, kind="handoff")
    assert len(h) == 1 and h[0]["run_id"] == rid and h[0]["provider"] == "bare"
    assert h[0]["data"] == {"reason": "needs tools", "to_run": nxt["id"]}
    run = [e for e in observe.entries(conn, kind="run") if e["run_id"] == rid]
    assert len(run) == 1 and run[0]["data"]["handed_off"] is True


def test_a_limited_run_leaves_a_limit_row(conn, monkeypatch):
    monkeypatch.setenv("EKI_FAKE_LIMITED", "fake")
    _, rid = ask(conn, "limit steps=1")
    assert run_inline(conn, rid)["state"] == "queued"
    lim = observe.entries(conn, kind="limit")
    assert len(lim) == 1 and lim[0]["provider"] == "fake" and lim[0]["run_id"] == rid
    assert lim[0]["data"]["pinned"] is False and lim[0]["data"]["reset_at"]
    assert not observe.entries(conn, kind="run")          # still queued: not ended yet
    assert run_inline(conn, rid)["state"] == "done"
    assert [e["run_id"] for e in observe.entries(conn, kind="run")] == [rid]


def test_a_worker_that_raises_leaves_a_fault_and_fails_the_run(conn, monkeypatch):
    from eki.providers.fake import Fake

    def broken(self, turn, emit):
        raise RuntimeError("provider broke")
    monkeypatch.setattr(Fake, "take", broken)
    tid, rid = ask(conn, "steps=1 hello")
    assert run_inline(conn, rid)["state"] == "failed"
    f = observe.entries(conn, kind="fault")
    assert len(f) == 1 and f[0]["run_id"] == rid and f[0]["thread_id"] == tid
    assert f[0]["data"]["where"] == "worker" and f[0]["data"]["ours"] is True
    assert "provider broke" in f[0]["data"]["traceback"]
    assert observe.entries(conn, kind="run")[0]["data"]["state"] == "failed"


def test_saying_no_in_the_same_thread_is_a_correction(conn):
    tid, rid = ask(conn, "steps=1 make it blue")
    run_inline(conn, rid)
    assert not observe.entries(conn, kind="correction")
    _, rid2 = ask(conn, "no, I meant green", tid=tid)
    run_inline(conn, rid2)
    c = observe.entries(conn, kind="correction")
    assert len(c) == 1 and c[0]["run_id"] == rid2 and c[0]["data"]["previous"] == rid


def test_a_failing_selfwork_tick_is_a_fault_and_the_tick_goes_on(conn, monkeypatch):
    monkeypatch.setattr(engine, "_last_self", [0.0])
    monkeypatch.setattr(engine, "refresh_quota", lambda: None)

    def boom(c):
        raise RuntimeError("self-work broke")
    monkeypatch.setattr(selfwork, "tick", boom)
    monkeypatch.setattr(queue, "tick", lambda c: [])
    monkeypatch.setattr(train, "tick", lambda c, **kw: [])
    assert engine.tick(conn) in (True, False)
    f = observe.entries(conn, kind="fault")
    assert len(f) == 1 and f[0]["data"]["where"] == "selfwork.tick"
    assert "self-work broke" in f[0]["data"]["traceback"] and f[0]["data"]["ours"] is True


def test_an_api_handler_that_raises_is_a_fault(conn, monkeypatch):
    def boom(c):
        raise RuntimeError("api broke")
    monkeypatch.setattr(api, "status", boom)
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/status"
        try:
            urllib.request.urlopen(url, timeout=10)
            code = 200
        except urllib.error.HTTPError as e:
            code, body = e.code, json.loads(e.read())
        assert code == 500 and "api broke" in body["error"]
    finally:
        srv.shutdown()
    f = observe.entries(conn, kind="fault")
    assert len(f) == 1 and f[0]["data"]["where"] == "server /api/status"
    assert "api broke" in f[0]["data"]["traceback"]
