from eki import db, store
from conftest import run_inline


def ask(conn, prompt, tid=None, **kw):
    with db.tx(conn):
        tid = tid or store.create_thread(conn, prompt, None)
        rid = store.create_run(conn, tid, prompt, **kw)
    return tid, rid


def test_a_run_goes_to_the_first_target_and_finishes(conn):
    tid, rid = ask(conn, "steps=2 hello")
    r = run_inline(conn, rid)
    assert r["state"] == "done" and r["provider"] == "fake"
    assert "fake done: steps=2 hello" in store.answer(conn, rid)
    assert "general" in r["why"]


def test_the_thread_stays_with_its_provider(conn):
    tid, rid = ask(conn, "steps=1 one", provider="fake2")
    run_inline(conn, rid)
    _, rid2 = ask(conn, "steps=1 two", tid=tid)
    r2 = run_inline(conn, rid2)
    assert r2["provider"] == "fake2" and "stays with fake2" in r2["why"]


def test_a_second_turn_resumes_the_session(conn):
    tid, rid = ask(conn, "steps=1 one")
    run_inline(conn, rid)
    sid = store.session(conn, tid, "fake")["session_id"]
    _, rid2 = ask(conn, "steps=1 two", tid=tid)
    run_inline(conn, rid2)
    assert store.session(conn, tid, "fake")["session_id"] == sid


def test_a_provider_joining_gets_what_it_missed(conn):
    from eki import worker
    tid, rid = ask(conn, "steps=1 first question")
    run_inline(conn, rid)
    _, rid2 = ask(conn, "second question", tid=tid, provider="fake2")
    turn = worker.build_turn(conn, store.run(conn, rid2), "fake2")
    assert turn.resume is None
    assert turn.history[0] == {"role": "user", "content": "steps=1 first question"}
    assert "fake done" in turn.history[1]["content"]


def test_a_limit_fails_over_to_the_next_target(conn, monkeypatch):
    monkeypatch.setenv("EKI_FAKE_LIMITED", "fake")
    tid, rid = ask(conn, "limit steps=1")
    r = run_inline(conn, rid)
    assert r["state"] == "queued" and r["provider"] is None and "fake" in store.excluded(r)
    assert "fake" in store.cooling(conn)
    r = run_inline(conn, rid)
    assert r["state"] == "done" and r["provider"] == "fake2"


def test_a_picked_provider_waits_out_its_limit(conn, monkeypatch):
    monkeypatch.setenv("EKI_FAKE_LIMITED", "fake")
    _, rid = ask(conn, "limit steps=1", provider="fake")
    r = run_inline(conn, rid)
    assert r["state"] == "queued" and r["retry_at"] and "waiting" in r["why"]


def test_a_bare_model_hands_off_to_a_harness(conn):
    tid, rid = ask(conn, "handoff please", provider="bare")
    r = run_inline(conn, rid)
    assert r["state"] == "handed_off"
    nxt = [x for x in store.thread_runs(conn, tid) if x["parent"] == rid][0]
    assert nxt["row"] == "code" and "bare" in store.excluded(nxt)
    assert "handed off by bare" in nxt["why"]


def test_a_failure_is_reported(conn):
    _, rid = ask(conn, "fail now")
    r = run_inline(conn, rid)
    assert r["state"] == "failed" and "fake failure" in r["error"]


def test_cancel_a_queued_run(conn):
    _, rid = ask(conn, "steps=1 x")
    assert store.cancel(conn, rid) == "cancelled"
    assert store.run(conn, rid)["state"] == "cancelled"


def test_an_interrupted_run_carries_on_in_its_session(conn):
    from eki import worker
    tid, rid = ask(conn, "steps=1 x")
    store.save_session(conn, tid, "fake", "abc", rid)
    store.update_run(conn, rid, provider="fake")
    turn = worker.build_turn(conn, store.run(conn, rid), "fake")
    assert turn.resume == "abc" and turn.prompt == worker.CARRY_ON


def test_a_rule_routed_run_keeps_the_rule_in_its_why(conn):
    _, rid = ask(conn, "fix steps=1 the test")
    r = run_inline(conn, rid)
    assert r["state"] == "done" and r["why"] == "rule: starts with fix → code → fake"
