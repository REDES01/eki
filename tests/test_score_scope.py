"""The score counts only what you asked for: self-work and picture runs are left out."""
import json

import pytest

from eki import db, digest, observe, score, store


@pytest.fixture
def pics(home):
    p = home / "providers.json"
    cfg = json.loads(p.read_text())
    cfg["pics"] = {"kind": "comfyui", "base_url": "http://127.0.0.1:1"}
    cfg["lm"] = {"kind": "local", "base_url": "http://127.0.0.1:1/v1", "model": "m"}
    p.write_text(json.dumps(cfg))


def _thread(conn, owner=None):
    """A thread; owner 'goal' or 'item' makes it self-work's."""
    tid = store.create_thread(conn, "t", None)
    if owner == "goal":
        conn.execute("INSERT INTO goals(id, text, thread_id, created_at) VALUES (?,?,?,?)",
                     (store.new_id(), "g", tid, 1))
    elif owner == "item":
        conn.execute("INSERT INTO items(id, goal_id, title, thread_id, created_at, updated_at)"
                     " VALUES (?,?,?,?,?,?)", (store.new_id(), "g", "i", tid, 1, 1))
    return tid


def _ended(conn, t, provider="fake", owner=None, row=None, state="done", strip=False):
    """A real run that ended, journalled by observe.run_ended at time t."""
    rid = store.create_run(conn, _thread(conn, owner), "hi", provider=provider, row=row)
    store.update_run(conn, rid, state=state, ended_at=db.now())
    jid = observe.run_ended(conn, rid)
    conn.execute("UPDATE journal SET t=? WHERE id=?", (t, jid))
    if strip:                                  # a row written before scopes
        data = json.loads(conn.execute("SELECT data FROM journal WHERE id=?", (jid,)).fetchone()[0])
        data.pop("scope")
        conn.execute("UPDATE journal SET data=? WHERE id=?", (json.dumps(data), jid))
    return rid


def _fault(conn, t, run_id=None):
    jid = observe.record(conn, "fault", run_id=run_id, data={"frame": "eki/x.py:1"})
    conn.execute("UPDATE journal SET t=? WHERE id=?", (t, jid))


def _noise(conn, t, strip=False):
    """One of each run the score must not count."""
    ids = [_ended(conn, t, owner="goal", state="handed_off", strip=strip),
           _ended(conn, t + 0.1, owner="item", state="failed", strip=strip),
           _ended(conn, t + 0.2, provider="command", strip=strip),
           _ended(conn, t + 0.3, provider="pics", strip=strip),
           _ended(conn, t + 0.4, provider="fake", row="image", strip=strip)]
    for rid in ids:
        _fault(conn, t + 0.5, rid)
    return ids


def test_scope_rule(conn, pics):
    assert observe.scope(conn, _thread(conn, "goal"), "fake", None) == "self"
    assert observe.scope(conn, _thread(conn, "item"), "fake", None) == "self"
    assert observe.scope(conn, _thread(conn), "command", None) == "self"
    assert observe.scope(conn, _thread(conn), "pics", None) == "picture"
    assert observe.scope(conn, _thread(conn), "fake", "image-edit") == "picture"
    assert observe.scope(conn, _thread(conn), "fake", "code") == "chat"
    assert observe.scope(conn, None, None, None) == "chat"


def test_scope_sees_chore_threads_once_the_table_exists(conn):
    tid = _thread(conn)
    assert observe.scope(conn, tid, "fake", None) == "chat"      # no chore on it: not a chore thread
    conn.execute("INSERT INTO chores(id, kind, subject, thread_id, created_at) VALUES ('c1', 'review', 'i1', ?, 0)",
                 (tid,))
    assert observe.scope(conn, tid, "fake", None) == "self"


def test_run_rows_carry_their_scope(conn, pics):
    _ended(conn, 10, owner="goal")
    _ended(conn, 11, provider="pics")
    _ended(conn, 12)
    got = [e["data"]["scope"] for e in observe.entries(conn, kind="run")]
    assert got == ["self", "picture", "chat"]


@pytest.mark.parametrize("strip", [False, True], ids=["written", "old rows"])
def test_self_and_picture_runs_do_not_move_the_score(conn, pics, strip):
    _ended(conn, 10, provider="lm", strip=strip)
    _ended(conn, 11, provider="fake", state="handed_off", strip=strip)
    _fault(conn, 11.5)                                  # no run: counted
    alone = score.compute(conn, 0, 100)
    _noise(conn, 20, strip=strip)
    got = score.compute(conn, 0, 100)
    for k in ("runs", "local_share", "handoff_rate", "fault_rate", "correction_rate", "median_first_text"):
        assert got[k] == alone[k], k
    assert got["runs"] == 2 and got["local_share"] == 0.5
    assert got["fault_rate"] == 50.0 and got["handoff_rate"] == 50.0
    assert got["left_out"] == {"self": 3, "picture": 2}
    assert alone["left_out"] == {"self": 0, "picture": 0}


def test_a_fault_on_a_self_run_is_left_out_one_without_a_run_counts(conn):
    _ended(conn, 10)
    rid = _ended(conn, 11, owner="item")
    _fault(conn, 12, rid)
    assert score.compute(conn, 0, 100)["fault_rate"] == 0.0
    _fault(conn, 13)
    assert score.compute(conn, 0, 100)["fault_rate"] == 100.0


def test_last_runs_counts_only_chat(conn, pics):
    for i in range(3):
        _ended(conn, 10 + i)
    _noise(conn, 20)
    got = score.last_runs(conn, 2, 100)
    assert got["runs"] == 2 and got["since"] == 11


def _open_row(conn, build, healthy_at, before):
    conn.execute("INSERT INTO build_scores(build, healthy_at, before) VALUES (?,?,?)",
                 (build, healthy_at, json.dumps(before)))


def test_settle_rescopes_an_open_before_once_and_leaves_frozen_rows(conn, pics):
    for i in range(30):
        _ended(conn, 10 + i)
    _noise(conn, 50)
    conn.execute("INSERT INTO build_scores(build, healthy_at, before, after, measured_at, verdict)"
                 " VALUES ('old', 5, '{\"runs\": 99}', '{\"runs\": 98}', 6, 'same')")
    frozen = tuple(conn.execute("SELECT * FROM build_scores WHERE build='old'").fetchone())
    _open_row(conn, "b1", 100.0, {"runs": 35, "fault_rate": 14.3})
    score.settle(conn, now=110)
    before = json.loads(conn.execute("SELECT before FROM build_scores WHERE build='b1'").fetchone()[0])
    assert before["scoped"] is True and before["runs"] == 30 and before["fault_rate"] == 0.0
    assert before["since"] == 5                          # from the previous build's healthy time
    # once: a later pass keeps it, even with more runs written into its window
    _ended(conn, 60)
    score.settle(conn, now=120)
    again = json.loads(conn.execute("SELECT before FROM build_scores WHERE build='b1'").fetchone()[0])
    assert again == before
    assert tuple(conn.execute("SELECT * FROM build_scores WHERE build='old'").fetchone()) == frozen


def test_record_build_marks_before_scoped(conn):
    _ended(conn, 10)
    score.record_build(conn, "b1", 100.0)
    before = json.loads(conn.execute("SELECT before FROM build_scores").fetchone()[0])
    assert before["scoped"] is True and before["left_out"] == {"self": 0, "picture": 0}


def test_digest_says_what_was_not_counted(conn, pics):
    now = db.now()
    _ended(conn, now - 100)
    _noise(conn, now - 50)
    text = digest.write(conn, now).read_text()
    assert "not counted: 3 self-work, 2 picture runs" in text
    assert "| runs | 1 |" in text
