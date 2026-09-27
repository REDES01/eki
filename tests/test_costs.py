import json

import pytest

from eki import costs, db, observe, selfwork, store

DAY = 86400


@pytest.fixture(autouse=True)
def table(home):
    (home / "providers.json").write_text(json.dumps({
        "local": {"kind": "local"}, "fake": {"kind": "fake"}}))
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "answer", "title": "no tools", "needs": ["text"], "targets": ["local", "fake"]},
        {"key": "code", "title": "tools", "needs": ["tools"], "targets": ["fake"]},
        {"key": "general", "title": "all", "needs": [], "targets": ["fake"]}]}))
    return home


def run(conn, row, prompt="write a haiku about snow"):
    tid = store.create_thread(conn, "t", None)
    return store.create_run(conn, tid, prompt, row=row)


def at(conn, jid, t):
    conn.execute("UPDATE journal SET t=? WHERE id=?", (t, jid))


def handoffs(conn, row, n, t=None, prompt="write a haiku about snow"):
    rids = []
    for i in range(n):
        rid = run(conn, row, f"{prompt} {i}")
        jid = observe.record(conn, "handoff", run_id=rid, provider="local", data={"to": "fake"})
        if t is not None:
            at(conn, jid, t + i)
        rids.append(rid)
    return rids


def corrections(conn, row, n, t=None):
    rids = []
    for i in range(n):
        prev = run(conn, row, "explain the GIL")
        rid = run(conn, "answer", "no, I meant the other one")
        jid = observe.record(conn, "correction", run_id=rid,
                             data={"previous": prev, "prompt": "no, I meant the other one", "redo": False})
        if t is not None:
            at(conn, jid, t + i)
        rids.append(rid)
    return rids


def goal(conn, key, item_state=None, moved=None, state="planned"):
    gid, now = store.new_id(), db.now()
    conn.execute("INSERT INTO goals(id, text, wish, source, owner, state, created_at, why, pick_key)"
                 " VALUES (?,?,?,?,?,?,?,?,?)",
                 (gid, f"fix {key}", f"fix {key}", "journal", "eki", state, now, "w", f"journal:{key}"))
    if item_state is not None:
        selfwork.new_item(conn, gid, f"fix {key}", "", [], [], False)
        conn.execute("UPDATE items SET state=?, landed_at=? WHERE goal_id=?", (item_state, moved, gid))
    return gid


def week(conn, since_days=7):
    return costs.clusters(conn, db.now() - since_days * DAY)


def test_local_rows():
    assert costs.local_rows() == {"answer"}


def test_a_local_row_is_not_local_when_the_model_is_off_or_cannot(home):
    (home / "providers.json").write_text(json.dumps({"local": {"kind": "local", "off": True},
                                                     "fake": {"kind": "fake"}}))
    assert costs.local_rows() == set()
    (home / "providers.json").write_text(json.dumps({"local": {"kind": "local", "can": ["vision"]},
                                                     "fake": {"kind": "fake"}}))
    assert costs.local_rows() == set()


def test_three_handoffs_on_a_local_row_make_a_cluster(conn):
    rids = handoffs(conn, "answer", 3)
    (c,) = week(conn)
    assert (c.kind, c.row, c.count, c.key) == ("handoff", "answer", 3, "handoff:answer")
    assert {rid for rid, _ in c.examples} == set(rids)


def test_handoffs_on_a_row_with_no_local_target_make_none(conn):
    handoffs(conn, "code", 3)
    assert week(conn) == []


def test_rows_without_a_run_or_a_row_are_skipped(conn):
    handoffs(conn, None, 3)
    for _ in range(3):
        observe.record(conn, "handoff", run_id=None, data={})
        observe.record(conn, "correction", run_id="nope", data={"previous": "gone"})
    assert week(conn) == []


def test_corrections_cluster_by_the_row_of_the_run_corrected(conn):
    corrections(conn, "code", 3)
    (c,) = week(conn)
    assert (c.kind, c.row, c.count) == ("correction", "code", 3)
    assert c.examples[0][1] == "no, I meant the other one"


def test_two_is_below_the_threshold_and_the_threshold_is_a_setting(conn, home):
    handoffs(conn, "answer", 2)
    assert week(conn) == []
    cfg = json.loads((home / "routing.json").read_text())
    cfg["self"] = {"pick_min_cluster": 2}
    (home / "routing.json").write_text(json.dumps(cfg))
    assert [c.key for c in week(conn)] == ["handoff:answer"]


def test_rank_by_count_then_correction_first(conn):
    handoffs(conn, "answer", 3)
    corrections(conn, "answer", 3)
    corrections(conn, "code", 4)
    assert [c.key for c in week(conn)] == ["correction:code", "correction:answer", "handoff:answer"]


def test_old_rows_are_outside_the_window(conn):
    handoffs(conn, "answer", 3, t=db.now() - 8 * DAY)
    assert week(conn) == []


def test_after_an_old_landing_only_newer_rows_count(conn):
    now = db.now()
    handoffs(conn, "answer", 3, t=now - 20 * DAY)
    goal(conn, "handoff:answer", "landed", now - 10 * DAY)
    assert week(conn, 30) == []
    handoffs(conn, "answer", 3, t=now - 5 * DAY)
    (c,) = week(conn, 30)
    assert c.count == 3


@pytest.mark.parametrize("item_state,moved_days,state", [
    ("landed", 2, "planned"), ("live", 1, "planned"), ("building", None, "planned"),
    ("proposed", None, "planned"), (None, None, "drafting"), (None, None, "planning")])
def test_a_recent_landing_or_open_work_blocks_the_cluster(conn, item_state, moved_days, state):
    handoffs(conn, "answer", 3)
    corrections(conn, "code", 3)
    moved = db.now() - moved_days * DAY if moved_days else None
    goal(conn, "handoff:answer", item_state, moved, state)
    assert [c.key for c in week(conn)] == ["correction:code"]


def test_a_dropped_goal_does_not_block(conn):
    handoffs(conn, "answer", 3)
    goal(conn, "handoff:answer", "dropped")
    assert [c.key for c in week(conn)] == ["handoff:answer"]


def test_examples_are_scrubbed_at_most_five_and_cut(conn):
    long = "token=abc123secret " + "x" * 400
    handoffs(conn, "answer", 7, prompt=long)
    (c,) = week(conn)
    assert c.count == 7 and len(c.examples) == 5
    for _, p in c.examples:
        assert "abc123secret" not in p and "[secret]" in p and len(p) <= 200


def test_goal_text_names_the_row_count_and_runs(conn):
    rids = handoffs(conn, "answer", 3)
    (c,) = week(conn)
    text = costs.goal_text(c)
    assert text.splitlines()[0] == "make handoffs on row answer stay local"
    assert "answer" in text and "3" in text and "stay local" in text
    for rid in rids:
        assert f"- {rid}: write a haiku" in text

    corrections(conn, "code", 4)
    fix = next(c for c in week(conn) if c.kind == "correction")
    text = costs.goal_text(fix)
    assert text.splitlines()[0] == "fix corrections on row code"
    assert "4" in text and "answered right the first time" in text
