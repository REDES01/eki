import json

import pytest

from eki import db, observe, paths, roadmap, selfpick, selfwork, store
from test_self import src  # noqa: F401 — the source fixture

ROADMAP = """# Roadmap

## Next

- [ ] **Faster answers**: the local model answers more.
- [ ] **Quieter board**: fewer lines.
"""


@pytest.fixture(autouse=True)
def world(home, monkeypatch):
    (home / "providers.json").write_text(json.dumps({
        "local": {"kind": "local"}, "fake": {"kind": "fake"}, "fake2": {"kind": "fake"}}))
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "answer", "title": "no tools", "needs": ["text"], "targets": ["local", "fake"]},
        {"key": "code", "title": "tools", "needs": ["tools"], "targets": ["fake", "fake2"]},
        {"key": "general", "title": "all", "needs": [], "targets": ["fake"]}]}))
    monkeypatch.setattr(roadmap, "read_main", lambda: ROADMAP)
    return home


def setting(**kv):
    p = paths.config("routing")
    data = json.loads(p.read_text())
    data["self"] = {**(data.get("self") or {}), **kv}
    p.write_text(json.dumps(data))


def loop_on():
    selfpick.set_loop(True)


def tb(path="eki/x.py", line=12, exc="KeyError"):
    return ("Traceback (most recent call last):\n"
            f'  File "/Users/someone/eki/{path}", line {line}, in go\n'
            "    thing['a']\n"
            f"{exc}: 'a'\n")


def fault(conn, **kw):
    return observe.fault(conn, "worker", tb(**kw))


def handoffs(conn, n=3, row="answer"):
    tid = store.create_thread(conn, "t", None)
    for i in range(n):
        rid = store.create_run(conn, tid, f"write a haiku {i}", row=row, priority="background")
        store.update_run(conn, rid, state="done")
        observe.record(conn, "handoff", run_id=rid, provider="local", data={"to": "fake"})


def goals(conn):
    return conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()


def end_all(conn):
    """The picked goals' runs and items are over: eki is idle again."""
    conn.execute("UPDATE goals SET state='planned' WHERE state IN ('drafting','planning')")
    conn.execute("UPDATE items SET state='dropped' WHERE state NOT IN ('proposed','locked')")
    conn.execute("UPDATE runs SET state='done' WHERE state IN ('queued','running')")


def test_with_the_loop_off_nothing_is_picked(conn, src):
    fault(conn)
    assert selfpick.settings()["loop"] is False
    assert selfpick.tick(conn) == [] and goals(conn) == []
    assert selfpick.line(conn) == "loop: off (eki self loop on)"
    assert selfpick.choose(conn).stage == "fault"          # the dry run still says what it would do


def test_busy_eki_picks_nothing_and_says_why(conn, src):
    loop_on()
    fault(conn)
    gid = store.new_id()
    conn.execute("INSERT INTO goals(id, text, state, created_at) VALUES (?,?,?,?)", (gid, "x", "planned", db.now()))
    iid = selfwork.new_item(conn, gid, "x", "", [], [], False)
    assert selfpick.idle(conn) == (False, f"item {iid} is waiting")
    assert selfpick.tick(conn) == [] and len(goals(conn)) == 1
    assert selfpick.line(conn) == f"loop: on — busy: item {iid} is waiting"
    conn.execute("UPDATE items SET state='dropped'")
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hello", priority="now")
    assert selfpick.idle(conn) == (False, f"run {rid} (now) is queued")
    assert selfpick.tick(conn) == []
    store.update_run(conn, rid, state="done")
    background = store.create_run(conn, tid, "later", priority="background")
    assert background and selfpick.idle(conn) == (True, "nothing queued")


def test_three_waiting_on_the_person_stop_it(conn, src):
    loop_on()
    fault(conn)
    gid = store.new_id()
    conn.execute("INSERT INTO goals(id, text, state, created_at) VALUES (?,?,?,?)", (gid, "x", "planned", db.now()))
    for _ in range(2):
        selfwork.new_item(conn, gid, "x", "", [], [], False)
    conn.execute("UPDATE items SET state='proposed'")
    tid = store.create_thread(conn, "goal thread", None)
    conn.execute("UPDATE goals SET thread_id=? WHERE id=?", (tid, gid))
    rid = store.create_run(conn, tid, "q", priority="background")
    store.update_run(conn, rid, state="done")
    conn.execute("INSERT INTO asks(id, run_id, thread_id, kind, payload, created_at) VALUES (?,?,?,?,?,?)",
                 ("a1", rid, tid, "question", "{}", db.now()))
    other = store.create_thread(conn, "not self-work", None)
    conn.execute("INSERT INTO asks(id, run_id, thread_id, kind, payload, created_at) VALUES (?,?,?,?,?,?)",
                 ("a2", rid, other, "question", "{}", db.now()))
    assert selfpick.waiting(conn) == 3
    assert selfpick.tick(conn) == [] and len(goals(conn)) == 1
    assert selfpick.line(conn) == "loop: stopped — 3 wait for you (eki self apply …)"
    setting(review_max=4)
    assert selfpick.line(conn) == "loop: on — idle, picks next pass"


def test_fault_then_cluster_then_roadmap_one_per_pass(conn, src):
    loop_on()
    fault(conn)
    handoffs(conn)
    p = selfpick.choose(conn)
    assert (p.stage, p.pick_key) == ("fault", "fault:eki/x.py:12 KeyError")
    assert p.why == "fault eki/x.py:12 KeyError, seen 1 times in 7 days"

    said = selfpick.tick(conn)
    [g] = goals(conn)
    assert said == [f"pick: goal {g['id']} — {p.why}"]
    assert (g["source"], g["owner"], g["why"], g["pick_key"]) == ("fault", "eki", p.why, p.pick_key)
    assert selfpick.tick(conn) == []                        # its item waits: eki is busy

    end_all(conn)
    conn.execute("UPDATE items SET state='proposed'")       # the fault's fix waits on the person
    p = selfpick.choose(conn)
    assert (p.stage, p.pick_key) == ("journal", "journal:handoff:answer")
    assert p.why == "3 handoffs on row answer in 7 days that could stay local"
    said = selfpick.tick(conn)
    g = goals(conn)[-1]
    assert said == [f"pick: goal {g['id']} — {p.why}"] and len(goals(conn)) == 2
    assert (g["source"], g["owner"], g["state"], g["why"], g["pick_key"]) == \
        ("journal", "eki", "planning", p.why, "journal:handoff:answer")
    plan = store.run(conn, g["plan_run"])
    assert plan["priority"] == "background" and "row answer" in plan["prompt"]
    assert selfpick.tick(conn) == []                        # planning: busy

    end_all(conn)
    selfwork.new_item(conn, g["id"], "keep it local", "", [], [], False)
    conn.execute("UPDATE items SET state='proposed' WHERE goal_id=?", (g["id"],))
    p = selfpick.choose(conn)                               # the cluster's goal has an open item
    assert (p.stage, p.pick_key, p.why) == ("roadmap", None, "2 open ROADMAP.md entries to rank")
    assert [e.title for e in p.payload] == ["Faster answers", "Quieter board"]
    said = selfpick.tick(conn)
    g = goals(conn)[-1]
    assert said == [f"pick: goal {g['id']} — {p.why}"]
    assert (g["source"], g["owner"], g["state"], g["why"]) == ("roadmap", "eki", "drafting", p.why)
    assert store.run(conn, g["draft_run"])["priority"] == "background"


def test_a_ranked_entry_is_not_picked_again(conn, src):
    loop_on()
    selfpick.tick(conn)
    g = goals(conn)[-1]
    first = roadmap.open_entries(ROADMAP)[0]
    conn.execute("UPDATE goals SET state='left', pick_key=? WHERE id=?", (f"roadmap:{first.key}", g["id"]))
    p = selfpick.choose(conn)
    assert [e.title for e in p.payload] == ["Quieter board"] and p.why == "1 open ROADMAP.md entries to rank"
    second = roadmap.open_entries(ROADMAP)[1]
    conn.execute("UPDATE goals SET pick_key=? WHERE id=?", (f"roadmap:{second.key}", g["id"]))
    gid = store.new_id()
    conn.execute("INSERT INTO goals(id, text, state, created_at, pick_key) VALUES (?,?,?,?,?)",
                 (gid, "x", "left", db.now(), f"roadmap:{first.key}"))
    assert selfpick.choose(conn) is None
    setting(picks_per_day=9)
    assert selfpick.tick(conn) == []


def test_picks_per_day_holds_after_the_goal_ends(conn, src):
    loop_on()
    setting(picks_per_day=1)
    handoffs(conn)
    assert len(selfpick.tick(conn)) == 1
    end_all(conn)
    assert selfpick.idle(conn)[0] and selfpick.choose(conn) is not None
    assert selfpick.picked_today(conn) == 1
    assert selfpick.tick(conn) == [] and len(goals(conn)) == 1
    setting(picks_per_day=2)
    assert len(selfpick.tick(conn)) == 1 and len(goals(conn)) == 2


def test_the_picker_never_writes_the_switch(conn, src):
    fault(conn)
    before = paths.config("routing").read_text()
    selfpick.choose(conn)
    selfpick.tick(conn)
    selfpick.line(conn)
    assert paths.config("routing").read_text() == before and goals(conn) == []
    loop_on()
    before = paths.config("routing").read_text()
    selfpick.choose(conn)
    selfpick.tick(conn)
    assert paths.config("routing").read_text() == before and len(goals(conn)) == 1


def test_set_loop_round_trips_and_keeps_the_rest(conn):
    setting(autonomy="apply")
    selfpick.set_loop(True)
    assert selfpick.settings()["loop"] is True and selfwork.settings()["autonomy"] == "apply"
    selfpick.set_loop(False)
    data = json.loads(paths.config("routing").read_text())
    assert data["self"]["loop"] is False and data["rows"]
