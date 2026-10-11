"""Fresh runs: a resolve or a chore starts a new program session, never the builder's (eki/worker.py)."""
import json

from eki import chores, db, resolve, selfwork, store, worker
from eki.worker import CARRY_ON


def built_thread(conn):
    """A thread whose build run left a Claude session behind."""
    with db.tx(conn):
        tid = store.create_thread(conn, "self: a build", None)
        build = store.create_run(conn, tid, "build it", provider="claude")
        store.update_run(conn, build, state="done")
        store.save_session(conn, tid, "claude", "sess-build", build)
    return tid


def test_a_fresh_run_does_not_resume_the_builders_session(conn):
    tid = built_thread(conn)
    with db.tx(conn):
        rid = store.create_run(conn, tid, "resolve the conflict", provider="claude", fresh=True)
    r = store.run(conn, rid)
    assert r["fresh"] == 1
    turn = worker.build_turn(conn, r, "claude")
    assert turn.resume is None and turn.history == [] and turn.extra["full_history"] == []
    assert turn.prompt == "resolve the conflict"


def test_a_fresh_run_cut_off_by_a_restart_carries_on_in_its_own_session(conn):
    tid = built_thread(conn)
    with db.tx(conn):
        rid = store.create_run(conn, tid, "resolve the conflict", provider="claude", fresh=True)
        store.save_session(conn, tid, "claude", "sess-resolve", rid)
    turn = worker.build_turn(conn, store.run(conn, rid), "claude")
    assert turn.resume == "sess-resolve" and turn.prompt == CARRY_ON


def test_a_run_not_fresh_still_resumes(conn):
    tid = built_thread(conn)
    with db.tx(conn):
        rid = store.create_run(conn, tid, "and now this", provider="claude")
    r = store.run(conn, rid)
    assert r["fresh"] == 0
    assert worker.build_turn(conn, r, "claude").resume == "sess-build"


def test_a_resolve_run_is_fresh(conn):
    tid = built_thread(conn)
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     ("g1", "make a.txt better", "ask", "you", "planned", db.now()))
        iid = selfwork.new_item(conn, "g1", "Change line two", "put mine on line two", ["a.txt"], [], False)
        selfwork._set(conn, iid, thread_id=tid, summary="line two says mine", state="queued")
        rid = resolve._start(conn, selfwork.store_item(conn, iid), "abc123", ["a.txt"], "", "")
    r = store.run(conn, rid)
    assert r["fresh"] == 1 and r["thread_id"] == tid
    assert worker.build_turn(conn, r, "claude").resume is None


def test_a_chore_is_fresh(home, conn, text_fake):
    cfg = json.loads((home / "routing.json").read_text())
    cfg["self"] = {"local": "bare"}
    (home / "routing.json").write_text(json.dumps(cfg))
    with db.tx(conn):
        rid = chores.start(conn, "review", "it1", "look at this", "background")
    assert rid and store.run(conn, rid)["fresh"] == 1
