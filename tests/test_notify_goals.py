"""Goal-done notices: which goals count as ended, what the notice says, once each — no network."""
import json
import time

import pytest

from eki import db, housekeep, notify, notifygoals

TOPIC = "xz-secret-topic-9f3k"
_n = [0]


def set_topic(home, topic):
    path = home / "routing.json"
    data = json.loads(path.read_text())
    data["notify"] = {"topic": topic}
    path.write_text(json.dumps(data))


@pytest.fixture
def lit(home):
    set_topic(home, TOPIC)
    return home


def goal(conn, text="make the tests fast\nmore words", state="planned", at=None, **kw):
    _n[0] += 1
    gid = f"g{_n[0]}"
    cols = {"id": gid, "text": text, "state": state, "created_at": at or time.time(), **kw}
    with db.tx(conn):
        conn.execute(f"INSERT INTO goals ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     tuple(cols.values()))
    return gid


def item(conn, gid, state, at=None, **kw):
    _n[0] += 1
    t = at or time.time()
    cols = {"id": f"i{_n[0]}", "goal_id": gid, "title": "x", "state": state, "created_at": t,
            "updated_at": t, **kw}
    with db.tx(conn):
        conn.execute(f"INSERT INTO items ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     tuple(cols.values()))
    return cols["id"]


def project(conn, repo=None):
    _n[0] += 1
    pid = f"p{_n[0]}"
    with db.tx(conn):
        conn.execute("INSERT INTO projects (id, name, path, branch, created_at, repo) VALUES (?,?,?,?,?,?)",
                     (pid, "proj", f"/tmp/nowhere/{pid}", "main", time.time(), repo))
    return pid


def notices(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM notices ORDER BY created_at, rowid")]


def tick(conn):
    with db.tx(conn):
        return notifygoals.tick(conn)


def test_a_self_goal_all_live_is_one_goal_done(conn, lit):
    gid = goal(conn)
    item(conn, gid, "live")
    item(conn, gid, "landed")
    item(conn, gid, "dropped")
    assert tick(conn) == [f"notify: goal {gid} done"]
    [n] = notices(conn)
    assert n["key"] == f"goal:{gid}:done" and n["kind"] == "goal_done"
    assert n["title"] == "eki · goal done" and n["body"] == "make the tests fast" and n["click"] is None
    assert tick(conn) == [] and len(notices(conn)) == 1
    assert TOPIC not in "".join(tick(conn))


def test_a_left_item_says_how_many_left(conn, lit):
    gid = goal(conn)
    item(conn, gid, "live")
    item(conn, gid, "left")
    tick(conn)
    [n] = notices(conn)
    assert n["title"] == "eki · goal ended, 1 left"


def test_a_goal_still_building_is_not_ended(conn, lit):
    gid = goal(conn)
    item(conn, gid, "live")
    item(conn, gid, "building")
    other = goal(conn)
    item(conn, other, "proposed")                        # no PR yet: still open
    planning = goal(conn, state="planning")
    item(conn, planning, "live")
    assert tick(conn) == [] and notices(conn) == []


def test_a_goal_with_only_dropped_items_is_not_ended(conn, lit):
    gid = goal(conn)
    item(conn, gid, "dropped")
    goal(conn)                                           # planned, no items at all
    assert tick(conn) == []


def test_a_project_goal_clicks_through_to_its_pr(conn, lit):
    pid = project(conn, "xz/app")
    gid = goal(conn, project=pid, issue=7)
    item(conn, gid, "proposed", pr="https://github.com/xz/app/pull/12")
    tick(conn)
    [n] = notices(conn)
    assert n["title"] == "eki · goal done" and n["click"] == "https://github.com/xz/app/pull/12"


def test_an_issue_goal_without_a_pr_clicks_through_to_the_issue(conn, lit):
    pid = project(conn, "xz/app")
    gid = goal(conn, project=pid, issue=42)
    item(conn, gid, "unfit")
    tick(conn)
    [n] = notices(conn)
    assert n["title"] == "eki · goal ended, 1 left"
    assert n["click"] == "https://github.com/xz/app/issues/42"


def test_a_local_project_goal_has_no_click(conn, lit):
    gid = goal(conn, project=project(conn), issue=3)
    item(conn, gid, "applied")
    tick(conn)
    assert notices(conn)[0]["click"] is None


def test_a_goal_that_ended_two_days_ago_is_left_alone(conn, lit):
    old = time.time() - 2 * 86400
    gid = goal(conn, at=old)
    item(conn, gid, "live", at=old)
    goal(conn, state="failed", at=old)
    assert tick(conn) == [] and notices(conn) == []


def test_an_old_goal_whose_item_moved_today_counts(conn, lit):
    old = time.time() - 5 * 86400
    gid = goal(conn, at=old)
    item(conn, gid, "live", at=old)
    item(conn, gid, "landed")
    assert tick(conn) == [f"notify: goal {gid} done"]


def test_a_failed_goal_is_one_notice(conn, lit):
    gid = goal(conn, text="\n  plan the thing  \n", state="failed", error="the planner said no")
    assert tick(conn) == [f"notify: goal {gid} done"]
    [n] = notices(conn)
    assert n["title"] == "eki · goal ended" and n["body"] == "plan the thing"
    assert tick(conn) == []


def test_a_standing_round_is_like_any_goal(conn, lit):
    gid = goal(conn, text="round 3 of keep it tidy", source="standing", owner="eki", standing_id="s1")
    item(conn, gid, "live")
    item(conn, gid, "rolled back")
    tick(conn)
    [n] = notices(conn)
    assert n["title"] == "eki · goal ended, 1 left" and n["body"] == "round 3 of keep it tidy"


def test_the_body_is_cut_to_120(conn, lit):
    gid = goal(conn, text="x" * 300)
    item(conn, gid, "live")
    tick(conn)
    assert notices(conn)[0]["body"] == "x" * 120


def test_nothing_is_queued_when_the_topic_is_empty(conn, home):
    gid = goal(conn)
    item(conn, gid, "live")
    assert not notify.on("goal_done")
    assert tick(conn) == [] and notices(conn) == []
    set_topic(home, "")
    assert tick(conn) == [] and notices(conn) == []


def test_a_restart_neither_resends_nor_loses(conn, lit, home):
    gid = goal(conn)
    item(conn, gid, "live")
    tick(conn)
    conn.close()
    again = db.connect()                                 # a new engine: nothing in memory
    assert tick(again) == [] and len(notices(again)) == 1
    other = goal(again)
    item(again, other, "landed")
    assert tick(again) == [f"notify: goal {other} done"]


def test_housekeeping_runs_it_before_the_drain(conn, lit, monkeypatch):
    names = [n for n, _ in housekeep.STEPS]
    assert names.index("notifygoals") == names.index("notify") - 1
    monkeypatch.setattr(housekeep, "STEPS", [s for s in housekeep.STEPS if s[0] == "notifygoals"])  # no drain
    gid = goal(conn)
    item(conn, gid, "live")
    assert f"notify: goal {gid} done" in housekeep.tick(conn)
