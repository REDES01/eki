"""A card call that finishes after its build turn stopped waiting reaches the item's next try."""
from eki import callsback, callwait, db, selfwork, store
from test_callwait import built, call, finish, item  # noqa: F401 — fixtures and helpers
from test_self import says, src  # noqa: F401 — fixtures

SESSION = "/tmp/sessions/playtester/20261012-093800-1e31"


def left_with_a_call_out(conn, built):  # noqa: F811
    c = call(conn, built)
    store.update_run(conn, built["run_id"], state="failed", error="slots look stuck", ended_at=db.now())
    selfwork.tick(conn)
    assert item(conn, built["id"])["state"] == "left"
    finish(conn, c, "played to the end", path=SESSION)
    return c


def test_the_next_build_prompt_hands_over_the_session(conn, built):  # noqa: F811
    c = left_with_a_call_out(conn, built)
    assert [r["id"] for r in callsback.unread(conn, item(conn, built["id"]))] == [c]
    selfwork.retry(conn, built["id"])
    selfwork.tick(conn)
    cur = item(conn, built["id"])
    assert cur["state"] == "building" and cur["run_id"] != built["run_id"]
    p = store.run(conn, cur["run_id"])["prompt"]
    assert f"`playtester` run `{c}` from an earlier try finished" in p
    assert SESSION in p and "do not play again" in p
    assert callsback.unread(conn, cur) == []                       # that brief carried it


def test_a_call_a_wake_already_carried_is_not_repeated(conn, built):  # noqa: F811
    c = left_with_a_call_out(conn, built)
    it = item(conn, built["id"])
    store.create_run(conn, it["thread_id"], callwait.wake_prompt(conn, [store.run(conn, c)], False))
    assert callsback.unread(conn, it) == []


def test_a_wake_from_before_it_ended_does_not_count(conn, built):  # noqa: F811
    c = call(conn, built)
    it = item(conn, built["id"])
    store.create_run(conn, it["thread_id"], callwait.wake_prompt(conn, [store.run(conn, c)], True))
    store.update_run(conn, built["run_id"], state="failed", ended_at=db.now())
    finish(conn, c, "done late", path=SESSION)
    assert [r["id"] for r in callsback.unread(conn, it)] == [c]


def test_no_calls_no_lines(conn):
    assert callsback.brief_lines(conn, []) == ""
