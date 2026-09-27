"""A run that fails on a transient error is queued again and resumes its session."""
import time

from eki import db, observe, store
from conftest import run_inline


def ask(conn, prompt, **kw):
    with db.tx(conn):
        tid = store.create_thread(conn, prompt, None)
        rid = store.create_run(conn, tid, prompt, **kw)
    return tid, rid


def notes(conn, rid):
    import json
    return [json.loads(e["data"])["text"] for e in store.events_after(conn, rid) if e["kind"] == "note"]


def retried_then_done(conn, tid, rid):
    before = time.time()
    r = run_inline(conn, rid)
    assert r["state"] == "queued" and r["retries"] == 1 and r["provider"] == "fake"
    assert before + 29 <= r["retry_at"] <= time.time() + 31
    assert r["ended_at"] is None
    note = notes(conn, rid)[-1]
    assert note.startswith("transient error, trying again in 30 s") and "ECONNRESET" in note
    [j] = observe.entries(conn, kind="transient")
    assert j["run_id"] == rid and j["data"]["retry_in"] == 30 and j["data"]["retries"] == 1
    assert not observe.entries(conn, kind="run")
    sid = store.session(conn, tid, "fake")["session_id"]
    r = run_inline(conn, rid)
    assert r["state"] == "done" and r["provider"] == "fake"
    assert "fake done" in store.answer(conn, rid)
    assert store.session(conn, tid, "fake")["session_id"] == sid


def test_a_dropped_connection_is_tried_again(conn):
    tid, rid = ask(conn, "flaky steps=1 hi")
    retried_then_done(conn, tid, rid)


def test_a_picked_provider_is_tried_again_on_itself(conn):
    tid, rid = ask(conn, "flaky steps=1 hi", provider="fake")
    retried_then_done(conn, tid, rid)


def test_retries_back_off_then_fail(conn):
    _, rid = ask(conn, "flaky=4 steps=1")
    for n, wait in enumerate((30, 120, 300), 1):
        start = time.time()
        r = run_inline(conn, rid)
        assert r["state"] == "queued" and r["retries"] == n
        assert start + wait - 1 <= r["retry_at"] <= time.time() + wait + 1
    r = run_inline(conn, rid)
    assert r["state"] == "failed" and "ECONNRESET" in r["error"] and r["retries"] == 3
    assert len(observe.entries(conn, kind="transient")) == 3


def test_an_ordinary_failure_fails_at_once(conn):
    _, rid = ask(conn, "fail steps=1")
    r = run_inline(conn, rid)
    assert r["state"] == "failed" and r["retries"] == 0 and "fake failure" in r["error"]
    assert not observe.entries(conn, kind="transient")


def test_a_limit_still_goes_the_limit_way(conn, monkeypatch):
    monkeypatch.setenv("EKI_FAKE_LIMITED", "fake")
    _, rid = ask(conn, "limit steps=1")
    r = run_inline(conn, rid)
    assert r["state"] == "queued" and r["provider"] is None and "fake" in store.excluded(r)
    assert r["retries"] == 0 and not observe.entries(conn, kind="transient")
