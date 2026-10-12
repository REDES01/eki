"""The Station shows the machine line and every project's goals, grouped by project: eki first."""
import json

from eki import api_station, asks, db, store
from test_self_cli import goal_with
from test_web import get, web  # noqa: F401  (the fixture)


def _project(conn, pid, name):
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at) VALUES (?,?,?,?,?)",
                     (pid, name, f"/tmp/{pid}", "main", db.now()))


def _project_goal(conn, pid, **fields):
    iid = goal_with(conn, **fields)
    with db.tx(conn):
        conn.execute("UPDATE goals SET project=? WHERE id=(SELECT goal_id FROM items WHERE id=?)", (pid, iid))
    return iid


def test_station_has_the_machine_line_and_goals_by_project(web, conn):
    own = goal_with(conn, state="building")
    _project(conn, "p2", "zebra")
    _project(conn, "p1", "Apples")
    tid = store.create_thread(conn, "build", "/tmp")
    rid = store.create_run(conn, tid, "x", provider="fake")
    left = _project_goal(conn, "p1", state="left", error="the agent changed nothing", run_id=rid)
    later = _project_goal(conn, "p2", state="queued")
    with db.tx(conn):
        asks.create(conn, rid, tid, "question", {"questions": [{"question": "which one?"}]})
    code, body = get(web + "/api/station")
    st = json.loads(body)
    m = st["machine"]
    assert set(m) == {"agents", "agents_cap", "checks", "checks_cap", "browsers", "browsers_cap", "load", "cores"}
    assert m["agents"] == 1 and m["agents_cap"] >= 1 and m["browsers_cap"] >= 1
    groups = st["self"]["groups"]
    assert [(g["project"], g["name"]) for g in groups] == [(None, "eki"), ("p1", "Apples"), ("p2", "zebra")]
    assert [it["id"] for g in groups[0]["goals"] for it in g["items"]] == [own]
    apples = groups[1]["goals"][0]
    assert apples["stands"] == "for you" and apples["items"][0]["id"] == left
    assert "the agent changed nothing" in apples["items"][0]["note"]
    assert [it["id"] for g in groups[2]["goals"] for it in g["items"]] == [later]
    assert [a["questions"][0]["question"] for a in st["asks"]] == ["which one?"]
    assert "goals" in st["self"]                # the CLI's list stays


def test_each_project_keeps_its_last_twenty_goals(conn):
    _project(conn, "p1", "busy")
    for _ in range(22):
        _project_goal(conn, "p1", state="live")
    goal_with(conn, state="live")
    groups = api_station.station(conn)["self"]["groups"]
    assert [len(g["goals"]) for g in groups] == [1, 20]


def test_a_project_drafts_ask_is_shown(conn):
    _project(conn, "p1", "one")
    tid = store.create_thread(conn, "draft", "/tmp")
    rid = store.create_run(conn, tid, "x", provider="fake")
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, draft_run, project) "
                     "VALUES (?,?,?,?,?,?,?,?)", (gid, "wish", "ask", "you", "drafting", db.now(), rid, "p1"))
        asks.create(conn, rid, tid, "question", {"questions": [{"question": "draft?"}]})
    assert [a["questions"][0]["question"] for a in api_station.station(conn)["asks"]] == ["draft?"]
