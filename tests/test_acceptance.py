"""Final acceptance: a goal with checks is done only when an independent judge accepts every check."""
import json

import pytest

from eki import acceptance, db, notifygoals, projects, selfwork, store, verdicts, workflow


def good(cid, verdict="passed", **over):
    rec = {"check_id": cid, "verdict": verdict, "evidence": f"ran `eki slot -- bin/check`: {cid} ok",
           "reasoning": "the output shows it", "counter_evidence": "none found", "limitation": "",
           "suggestion": ""}
    if verdict != "passed":
        rec.update(limitation="no screen to look at", suggestion="add a test that shows it")
    rec.update(over)
    return rec


@pytest.fixture
def at_proj(monkeypatch, proj):
    """An eki goal is judged at proj's HEAD here, not at the integration repo."""
    monkeypatch.setattr(acceptance, "_where", lambda conn, g: (str(proj), "HEAD", None))
    return proj


@pytest.fixture
def notify_on(home):
    path = home / "routing.json"
    data = json.loads(path.read_text())
    data["notify"] = {"topic": "eki-test-topic"}
    path.write_text(json.dumps(data))


def goal(conn, checks=(("c1", "the first"), ("c2", "the second")), items=1, state="landed", builder="fake",
         project=None):
    """A planned goal with checks and `items` items, each built by `builder` and now `state`."""
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, state, project, created_at) VALUES (?,?,?,?,?)",
                     (gid, "Make it work\n\nmore words", "planned", project, db.now()))
        verdicts.store_checks(conn, gid, checks)
        for n in range(items):
            iid = selfwork.new_item(conn, gid, f"item {n}", "do it", [f"eki/m{n}.py"], [], False)
            tid = store.create_thread(conn, f"build {iid}", None)
            rid = store.create_run(conn, tid, "build it", provider=builder)
            store.update_run(conn, rid, state="done")
            conn.execute("UPDATE items SET state=?, thread_id=?, run_id=?, touched=?, checks=? WHERE id=?",
                         (state, tid, rid, db.dumps([f"eki/m{n}.py"]), db.dumps([checks[n % len(checks)][0]] if checks else []),
                          iid))
    return gid


def g_of(conn, gid):
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()


def judges(conn, gid, scope=None):
    q = "SELECT * FROM judgings WHERE goal_id=?" + (" AND scope=?" if scope else "") + " ORDER BY created_at, rowid"
    return conn.execute(q, (gid, scope) if scope else (gid,)).fetchall()


def judge(conn, rid, *recs, end="done"):
    """The judge run runs, files its verdicts, and ends."""
    store.update_run(conn, rid, state="running")
    for rec in recs:
        verdicts.submit(conn, rid, rec)
    store.update_run(conn, rid, state=end, ended_at=db.now())


def test_a_builders_word_never_accepts_only_a_judge_does(conn, at_proj):
    gid = goal(conn)
    it = conn.execute("SELECT * FROM items WHERE goal_id=?", (gid,)).fetchone()
    with db.tx(conn):
        store.update_run(conn, it["run_id"], state="running")
        conn.execute("UPDATE items SET summary='all perfect, every check passes' WHERE id=?", (it["id"],))
    with pytest.raises(ValueError, match="is not a judge run"):
        verdicts.submit(conn, it["run_id"], good("c1"))
    store.update_run(conn, it["run_id"], state="done")

    assert acceptance.tick(conn)[0].startswith(f"goal {gid}: accepting, round 1")
    g = g_of(conn, gid)
    assert g["state"] == "accepting" and json.loads(g["accept"])["round"] == 1
    j = judges(conn, gid)
    assert [x["scope"] for x in j] == ["final"] and verdicts.listed(j[0]) == ["c1", "c2"]
    run = store.run(conn, j[0]["run_id"])
    assert run["provider"] == "fake2" and json.loads(j[0]["avoid"]) == ["fake"]    # never the builder
    assert store.thread(conn, run["thread_id"])["cwd"].endswith(f"accept-{gid}")
    assert acceptance.tick(conn) == [] and g_of(conn, gid)["state"] == "accepting"   # still judging

    store.update_run(conn, run["id"], state="running")
    verdicts.submit(conn, run["id"], good("c1"))
    assert acceptance.tick(conn) == [] and g_of(conn, gid)["state"] == "accepting"
    verdicts.submit(conn, run["id"], good("c2"))
    store.update_run(conn, run["id"], state="done", ended_at=db.now())
    assert "accepted" in acceptance.tick(conn)[0]
    assert g_of(conn, gid)["state"] == "accepted"


def test_not_started_while_an_item_is_open_or_without_checks(conn, at_proj):
    gid = goal(conn, items=2)
    with db.tx(conn):
        conn.execute("UPDATE items SET state='queued' WHERE id=(SELECT id FROM items WHERE goal_id=? LIMIT 1)",
                     (gid,))
    plain = goal(conn, checks=())
    assert acceptance.tick(conn) == []
    assert g_of(conn, gid)["state"] == "planned" and g_of(conn, plain)["state"] == "planned"


def test_one_uncertain_check_keeps_the_goal_open(conn, at_proj, home, notify_on):
    got = json.loads((home / "providers.json").read_text())
    got["fake3"] = {"kind": "fake"}
    (home / "providers.json").write_text(json.dumps(got))
    routing = json.loads((home / "routing.json").read_text())
    routing["rows"][1]["targets"] = ["fake", "fake2", "fake3"]
    (home / "routing.json").write_text(json.dumps(routing))

    gid = goal(conn, checks=(("c1", "one"), ("c2", "two"), ("c3", "it looks right")))
    acceptance.tick(conn)
    first = judges(conn, gid)[0]["run_id"]
    judge(conn, first, good("c1"), good("c2"), good("c3", "uncertain", limitation="no screen to look at"))
    said = acceptance.tick(conn)
    assert "c3 uncertain — a second judge" in said[0]
    sec = judges(conn, gid, "second")[0]
    run = store.run(conn, sec["run_id"])
    assert run["provider"] == "fake3"                       # off the builder (fake) and the first judge (fake2)
    assert json.loads(sec["avoid"]) == ["fake", "fake2"] and verdicts.listed(sec) == ["c3"]
    assert "the first judge could not settle it: no screen to look at" in run["prompt"]
    assert g_of(conn, gid)["state"] == "accepting"

    judge(conn, sec["run_id"], good("c3", "uncertain", limitation="still no screen"))
    acceptance.tick(conn)
    g = g_of(conn, gid)
    assert g["state"] == "judgment" and g["error"] == "needs you: c3: still no screen"
    note = conn.execute("SELECT * FROM notices WHERE kind='needs_you'").fetchone()
    assert note is not None and "still no screen" in note["body"]
    assert acceptance.tick(conn) == [] and g_of(conn, gid)["state"] == "judgment"   # never accepted by itself
    ended = conn.execute(notifygoals._ENDED, (db.now() - notifygoals.WINDOW, *notifygoals.OPEN)).fetchall()
    assert [r["id"] for r in ended] == [gid]                     # its end: judgment, not its items landing


def test_no_other_backend_for_the_second_judge_is_judgment(conn, at_proj):
    gid = goal(conn)
    acceptance.tick(conn)
    judge(conn, judges(conn, gid)[0]["run_id"], good("c1"))      # c2: no verdict at all
    acceptance.tick(conn)
    g = g_of(conn, gid)
    assert g["state"] == "judgment"                              # fake and fake2 both kept off
    assert g["error"].startswith("needs you: c2: the judge filed no verdict")


def test_a_failed_check_gets_a_fix_item_and_fails_once(conn, at_proj):
    gid = goal(conn, items=2)
    acceptance.tick(conn)
    judge(conn, judges(conn, gid)[0]["run_id"],
          good("c1"), good("c2", "failed", evidence="`eki x` printed nothing", suggestion="print the line"))
    said = acceptance.tick(conn)
    assert "c2 failed — fix item" in said[0]
    g = g_of(conn, gid)
    acc = json.loads(g["accept"])
    assert g["state"] == "planned" and acc["round"] == 2 and len(acc["fixes"]) == 1
    fix = conn.execute("SELECT * FROM items WHERE id=?", (acc["fixes"][0],)).fetchone()
    assert fix["title"] == "Fix check c2: the second" and fix["state"] == "waiting"
    assert "print the line" in fix["spec"] and "`eki x` printed nothing" in fix["spec"]
    assert json.loads(fix["checks"]) == ["c2"] and json.loads(fix["files"]) == ["eki/m1.py"]   # its tagged item's
    assert acceptance.tick(conn) == []                           # the fix isn't in yet

    with db.tx(conn):
        conn.execute("UPDATE items SET state='landed' WHERE id=?", (fix["id"],))
    assert "round 2" in acceptance.tick(conn)[0]
    again = judges(conn, gid)[-1]
    assert again["round"] == 2
    judge(conn, again["run_id"], good("c1"), good("c2", "failed", suggestion="still nothing printed"))
    acceptance.tick(conn)
    g = g_of(conn, gid)
    assert g["state"] == "judgment" and "c2: failed again: still nothing printed" in g["error"]
    assert conn.execute("SELECT COUNT(*) FROM items WHERE goal_id=?", (gid,)).fetchone()[0] == 3


def test_a_project_without_review_still_gets_final_acceptance(conn, proj, monkeypatch):
    wf = workflow.Workflow(router="on", target="", plan="split", parallel=4, checks=("self", "command"),
                           command="true", merge="auto", playtester="playtester", card="")
    monkeypatch.setattr(workflow, "of_project", lambda c, p: wf)
    pid = projects.add(conn, proj)
    gid = goal(conn, state="applied", project=pid)
    acceptance.tick(conn)
    assert g_of(conn, gid)["state"] == "accepting"
    j = judges(conn, gid)
    assert [x["scope"] for x in j] == ["final"]                  # no playtester on this card: no play run
    assert store.thread(conn, store.run(conn, j[0]["run_id"])["thread_id"])["cwd"].endswith(f"accept-{gid}")
    judge(conn, j[0]["run_id"], good("c1"), good("c2"))
    acceptance.tick(conn)
    assert g_of(conn, gid)["state"] == "accepted"


def test_a_playtester_card_adds_a_play_run_and_the_worst_stands(conn, proj, monkeypatch):
    wf = workflow.Workflow(router="on", target="", plan="split", parallel=4, checks=("self", "playtester"),
                           command="", merge="auto", playtester="playtester", card="")
    monkeypatch.setattr(workflow, "of_project", lambda c, p: wf)
    pid = projects.add(conn, proj)
    gid = goal(conn, state="applied", project=pid)
    acceptance.tick(conn)
    final, play = judges(conn, gid)
    assert (final["scope"], play["scope"]) == ("final", "play")
    run = store.run(conn, play["run_id"])
    argv = json.loads(run["prompt"])
    assert run["provider"] == "command" and argv[2].startswith("eki use playtester ")
    assert "| eki verdict --lines" in argv[2] and "Focus:" in argv[2]
    assert json.loads(g_of(conn, gid)["accept"])["runs"] == [final["run_id"], play["run_id"]]
    judge(conn, final["run_id"], good("c1"), good("c2"))
    assert acceptance.tick(conn) == []                           # the playtester hasn't ended
    judge(conn, play["run_id"], good("c1"), good("c2", "failed", suggestion="the button is hidden"))
    acceptance.tick(conn)
    assert g_of(conn, gid)["state"] == "planned"                 # play's failed beats final's passed


def test_a_round_that_ended_while_the_engine_was_down_concludes_on_the_next_tick(conn, at_proj):
    gid = goal(conn)
    acceptance.tick(conn)
    rid = judges(conn, gid)[0]["run_id"]
    judge(conn, rid, good("c1"), good("c2"))                    # the engine was down meanwhile
    fresh = db.connect()                                         # a new engine: nothing but the tables
    assert "accepted" in acceptance.tick(fresh)[0]
    assert g_of(fresh, gid)["state"] == "accepted"


def test_accepting_with_no_judge_run_starts_again(conn, at_proj):
    gid = goal(conn)
    with db.tx(conn):                                            # an older move left it with nothing running
        conn.execute("UPDATE goals SET state='accepting', accept=? WHERE id=?",
                     (db.dumps({"round": 1, "runs": [], "fixes": []}), gid))
    acceptance.tick(conn)
    acceptance.tick(conn)
    assert g_of(conn, gid)["state"] == "accepting" and len(judges(conn, gid)) == 1


def test_accept_by_you_and_rerun(conn, at_proj):
    gid = goal(conn)
    with pytest.raises(ValueError):
        acceptance.accept_by_you(conn, gid)
    acceptance.tick(conn)
    judge(conn, judges(conn, gid)[0]["run_id"], good("c1"))
    acceptance.tick(conn)
    assert g_of(conn, gid)["state"] == "judgment"
    assert acceptance.rerun(conn, gid) == "accepting"
    j = judges(conn, gid)[-1]
    assert j["round"] == 2 and len(judges(conn, gid)) == 2
    judge(conn, j["run_id"], good("c1"))
    acceptance.tick(conn)
    assert acceptance.accept_by_you(conn, gid) == "accepted"
    g = g_of(conn, gid)
    assert g["state"] == "accepted" and json.loads(g["accept"])["by"] == "you"
