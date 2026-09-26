"""The draft stage's schema: goals carry a wish, runs carry a per-run model."""
from eki import db, store, worker
from eki.providers.base import Outcome


def cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_db_has_the_columns(conn):
    assert {"wish", "draft_run", "drafted_at"} <= cols(conn, "goals")
    assert "model" in cols(conn, "runs")


def test_create_run_stores_the_model(conn):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", model="opus")
    assert store.run(conn, rid)["model"] == "opus"
    plain = store.create_run(conn, tid, "hi again")
    assert store.run(conn, plain)["model"] is None


def test_build_turn_passes_the_model(conn):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", provider="fake", model="opus")
    assert worker.build_turn(conn, store.run(conn, rid), "fake").extra["model"] == "opus"
    plain = store.create_run(conn, tid, "hi", provider="fake")
    assert worker.build_turn(conn, store.run(conn, plain), "fake").extra.get("model") is None


def test_handoff_does_not_carry_the_model(conn):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", provider="fake", model="opus")
    store.update_run(conn, rid, state="running")
    worker.finish(conn, store.run(conn, rid), "fake", Outcome(state="handed_off", reason="tools"))
    nxt = conn.execute("SELECT * FROM runs WHERE parent=?", (rid,)).fetchone()
    assert nxt is not None and nxt["model"] is None
