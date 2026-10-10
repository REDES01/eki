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


def _tiered(home):
    import json
    (home / "providers.json").write_text(json.dumps({
        "claude": {"kind": "fake", "models": {"fast": "claude-haiku-4-5"}}, "fake": {"kind": "fake"}}))
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "general", "title": "all", "targets": ["claude:fast", "fake"]}]}))


def test_route_writes_the_model_the_row_names(home, conn):
    _tiered(home)
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", row="general")
    assert worker.route(conn, store.run(conn, rid)) == "claude"
    assert store.run(conn, rid)["model"] == "claude-haiku-4-5"


def test_a_run_with_its_own_model_keeps_it(home, conn):
    _tiered(home)
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", row="general", model="opus")
    assert worker.route(conn, store.run(conn, rid)) == "claude"
    assert store.run(conn, rid)["model"] == "opus"


def test_a_reroute_drops_the_model_the_last_route_wrote(home, conn):
    _tiered(home)
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", row="general")
    worker.route(conn, store.run(conn, rid))
    store.update_run(conn, rid, provider=None, exclude='["claude"]')   # claude hit its limit
    assert worker.route(conn, store.run(conn, rid)) == "fake"
    assert store.run(conn, rid)["model"] is None


def test_effort_is_stored_and_survives_a_restart(conn):
    """Model and effort live in SQLite: a worker started again builds the same turn."""
    assert "effort" in cols(conn, "runs")
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", provider="fake", model="opus", effort="high")
    store.update_run(conn, rid, state="running")
    again = db.connect()                       # a fresh process: nothing held in memory
    extra = worker.build_turn(again, store.run(again, rid), "fake").extra
    assert (extra["model"], extra["effort"]) == ("opus", "high")
    plain = store.create_run(conn, tid, "hi", provider="fake")
    assert "effort" not in worker.build_turn(conn, store.run(conn, plain), "fake").extra
