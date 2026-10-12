"""Build, check and fix through the work model: eki's items and a project's go the same
ladder through workrun.feed, and the build handlers start each run once (eki/buildfeed.py,
eki/workhandlers_build.py)."""
import json

import pytest

from eki import buildfeed, db, integration, projects, selfwork, store, workrun
from eki.workmodel import Event


def goal_with(conn, gid, iid, state, project=None, **more):
    now = db.now()
    conn.execute("INSERT INTO goals(id, text, state, created_at, project, owner) VALUES (?, 'do it', 'planned', ?, ?, "
                 "'you')", (gid, now, project))
    cols = {"id": iid, "goal_id": gid, "title": f"item {iid}", "state": state, "created_at": now,
            "updated_at": now, **more}
    conn.execute(f"INSERT INTO items({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))


def ladder(conn, gid, iid):
    """Feed one item build → red → one more try → red again → (the fixer → red) and say what came of each."""
    seen = []

    def feed(kind, **data):
        acts = workrun.feed(conn, gid, iid, Event(kind, db.now(), data))
        row = conn.execute("SELECT state, tries FROM items WHERE id=?", (iid,)).fetchone()
        seen.append((row["state"], row["tries"], [(a.kind, a.data.get("role", a.data.get("gate"))) for a in acts]))

    built = {"verdict": "done", "changed": True, "touched": ["a.py"], "branch": "b", "can_fix": True}
    red = {"result": "red", "ended": "failed", "review": "ok", "can_fix": True, "tail": "FAILED test_a"}
    feed("item_finished", **built)
    feed("check_verdict", **red)
    feed("item_started")
    feed("item_finished", **built)
    feed("check_verdict", **red)
    if seen[-1][0] == "fixing":
        feed("item_finished", **built)
        feed("check_verdict", **red)
    return seen


def test_eki_and_a_project_item_climb_the_same_ladder(conn, proj):
    pid = projects.add(conn, proj)
    conn.execute("UPDATE projects SET check_cmd='make test' WHERE id=?", (pid,))
    goal_with(conn, "g1", "a", "building", tries=1)
    goal_with(conn, "g2", "p", "building", project=pid, tries=1)
    assert workrun.load(conn, "g2")[2].gates[:2] == ("check", "review")
    mine, theirs = ladder(conn, "g1", "a"), ladder(conn, "g2", "p")
    judging = ("judging", 1, [("run_gate", 1), ("start_run", "review")])
    again = [judging, ("waiting", 1, []), ("building", 2, [("start_run", "build")]),
             ("judging", 2, [("run_gate", 1), ("start_run", "review")])]
    assert mine[:4] == theirs[:4] == again
    assert mine[4:] == [("fixing", 2, [("start_run", "fix")]),                 # eki's own get one fixer run
                        ("judging", 2, [("run_gate", 1), ("start_run", "review")]), ("unfit", 2, [])]
    assert theirs[4:] == [("unfit", 2, [])]                                   # a project's don't (difference h)
    fix = json.loads(conn.execute("SELECT action FROM work_actions WHERE item_id='a' AND action LIKE '%\"fix\"%'")
                     .fetchone()[0])
    assert fix["data"] == {"role": "fix", "stage": "gate1", "tries": 2, "failure": "FAILED test_a"}
    why = conn.execute("SELECT work FROM items WHERE id='a'").fetchone()[0]
    assert json.loads(why)["why"] == "unfit — left for you"


def test_gate_one_starts_once_across_a_crash_and_the_item_waits_for_it(conn, monkeypatch):
    monkeypatch.setattr(integration, "repo", lambda: "/nowhere")         # eki's repo is never made here
    tid = store.create_thread(conn, "self: a", None)
    build = store.create_run(conn, tid, "build it", priority="background")
    store.update_run(conn, build, state="done")
    goal_with(conn, "g1", "a", "building", tries=1, thread_id=tid, run_id=build, touched='["eki/x.py"]',
              base="b" * 40, commit_sha="c" * 40, branch="self/a")
    workrun.feed(conn, "g1", "a", Event("item_finished", db.now(), {"verdict": "done", "touched": ["eki/x.py"]}))
    it = selfwork.store_item(conn, "a")
    assert it["state"] == "judging" and it["run_id"] == build
    assert buildfeed.conclude_judging(conn, it) == []          # its actions not yet carried: the old run isn't judged
    real, calls = store.create_run, []

    def crash_once(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("the engine died here")
        return real(*a, **kw)
    monkeypatch.setattr(store, "create_run", crash_once)
    workrun.carry(conn)                                         # the gate's start rolled back; the review's done
    assert selfwork.store_item(conn, "a")["run_id"] == build
    workrun.carry(conn)
    workrun.carry(conn)
    it = selfwork.store_item(conn, "a")
    gates = conn.execute("SELECT * FROM runs WHERE provider='command'").fetchall()
    assert len(gates) == 1 and it["run_id"] == gates[0]["id"] and gates[0]["prompt"] == selfwork.CHECK
    assert gates[0]["priority"] == "background"                 # the build's own
    assert it["review"] == "none: no judge in tests" and it["review_run"] is None   # conftest turns the judge off
    assert not conn.execute("SELECT 1 FROM work_actions WHERE done_at IS NULL").fetchall()


def test_a_person_retry_is_refused_while_a_run_goes_and_drop_always_lands(conn):
    goal_with(conn, "g1", "a", "judging", tries=1)
    with pytest.raises(ValueError, match="is judging"):
        selfwork.retry(conn, "a")
    selfwork.drop(conn, "a")
    it = selfwork.store_item(conn, "a")
    assert it["state"] == "dropped" and json.loads(it["work"])["why"] == "dropped"
