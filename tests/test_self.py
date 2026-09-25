import json
from pathlib import Path

import pytest

from eki import selfbrief, selfwork, store, workspace
from conftest import run_inline

PLAN = """Here is the plan.

ITEMS:
```json
[{"title": "Add a", "spec": "make eki/a.py", "files": ["eki/a.py"], "deps": [], "independent": false},
 {"title": "Add b", "spec": "make eki/b.py", "files": ["eki/b.py"], "deps": [], "independent": false}]
```
"""


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    (r / "eki" / "x.py").write_text("# x\n")
    (r / "bin" / "check").write_text("#!/bin/sh\nexit ${EKI_TEST_CHECK_EXIT:-0}\n")
    (r / "bin" / "check").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def says(tmp_path, monkeypatch, text):
    p = tmp_path / "says.txt"
    p.write_text(text)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))


def item(conn, iid):
    return selfwork.store_item(conn, iid)


def test_a_goal_is_planned_built_judged_and_proposed(conn, src, tmp_path, monkeypatch):
    gid = selfwork.submit(conn, "add two modules")
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    plan_run = store.run(conn, g["plan_run"])
    assert g["state"] == "planning" and "add two modules" in plan_run["prompt"]
    assert store.cwd_of(conn, plan_run["thread_id"]).endswith(f"plan-{gid}")

    says(tmp_path, monkeypatch, PLAN)
    run_inline(conn, plan_run["id"])
    said = selfwork.tick(conn)
    assert any("2 item(s) planned" in s for s in said)
    items = selfwork.items_in(conn, ("building",))          # both start: disjoint write-sets
    assert [i["title"] for i in items] == ["Add a", "Add b"]
    assert not workspace.git(src, "branch", "--list", f"eki/plan-{gid}")
    a, b = items
    assert a["worktree"] and a["branch"] == f"self/{a['id']}" and a["base"] == workspace.head(src)
    build = store.run(conn, b["run_id"])
    assert "Add a" in build["prompt"] and "stay out of their files" in build["prompt"]
    assert selfwork.tick(conn) == []                                      # nothing new: nothing said

    says(tmp_path, monkeypatch, "SUMMARY: made a.\n\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/a.py")
    run_inline(conn, a["run_id"])
    selfwork.tick(conn)
    a = item(conn, a["id"])
    assert a["state"] == "judging" and a["commit_sha"] and json.loads(a["touched"]) == ["eki/a.py"]
    assert a["summary"] == "made a."
    judge = store.run(conn, a["run_id"])
    assert judge["provider"] == "command" and judge["prompt"] == selfwork.CHECK
    assert ".venv" not in workspace.git(src, "show", "--stat", "--format=", a["commit_sha"])
    run_inline(conn, judge["id"])
    selfwork.tick(conn)
    a = item(conn, a["id"])
    assert a["state"] == "proposed"
    assert workspace.git(src, "log", "-1", "--format=%s", a["branch"]) == "self: Add a"
    assert (src / "eki" / "a.py").exists() is False                       # the source is untouched


def test_overlapping_write_sets_take_turns_and_deps_wait(conn, src):
    gid = selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    first = selfwork.items_in(conn, ("waiting",))[0]
    with conn:
        second = selfwork.new_item(conn, gid, "also x", "…", ["eki/*.py"], [], False)
        third = selfwork.new_item(conn, gid, "after", "…", ["docs/new.md"], [first["id"]], False)
        fourth = selfwork.new_item(conn, gid, "free", "…", ["README.md"], [], True)
    selfwork.tick(conn)
    states = {i["id"]: i["state"] for i in conn.execute("SELECT id, state FROM items")}
    assert states[first["id"]] == "building" and states[second] == "waiting"
    assert states[third] == "waiting" and states[fourth] == "building"
    wt = item(conn, first["id"])["worktree"]
    (Path(wt) / "eki" / "x.py").write_text("# by first\n")
    sha = workspace.commit_all(wt, "first's change")
    selfwork._set(conn, first["id"], state="proposed", commit_sha=sha)
    selfwork.tick(conn)
    states = {i["id"]: i["state"] for i in conn.execute("SELECT id, state FROM items")}
    assert states[second] == "building" and states[third] == "waiting"      # its dep isn't in main yet
    workspace.git(src, "merge", "-q", "--ff-only", sha)                    # you merge it
    said = selfwork.tick(conn)
    states = {i["id"]: i["state"] for i in conn.execute("SELECT id, state FROM items")}
    assert states[first["id"]] == "applied" and states[third] == "building"
    assert any("applied" in x for x in said)


def test_failed_checks_get_one_more_try_then_unfit(conn, src, tmp_path, monkeypatch):
    selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    selfwork.tick(conn)
    it = selfwork.items_in(conn, ("building",))[0]
    says(tmp_path, monkeypatch, "SUMMARY: tried.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")

    def step(expect):
        selfwork.tick(conn)
        cur = item(conn, it["id"])
        assert cur["state"] == expect, (expect, cur["state"], cur["error"])
        return cur

    run_inline(conn, it["run_id"])
    cur = step("judging")
    run_inline(conn, cur["run_id"])
    cur = step("building")                     # failed checks: one more try, at once
    assert cur["tries"] == 2 and "previous attempt" in store.run(conn, cur["run_id"])["prompt"]
    run_inline(conn, cur["run_id"])
    cur = step("judging")
    run_inline(conn, cur["run_id"])
    cur = step("unfit")
    assert cur["tries"] == 2


def test_person_and_no_change_are_left_for_you(conn, src, tmp_path, monkeypatch):
    selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    selfwork.submit(conn, "y", plan=False, files=["eki/y.py"])
    selfwork.tick(conn)
    x, y = selfwork.items_in(conn, ("building",))
    says(tmp_path, monkeypatch, "ITEM: person needs a real trackpad\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    run_inline(conn, x["run_id"])
    monkeypatch.delenv("EKI_FAKE_TOUCH")
    says(tmp_path, monkeypatch, "ITEM: done\n")
    run_inline(conn, y["run_id"])
    selfwork.tick(conn)
    assert item(conn, x["id"])["state"] == "left" and "trackpad" in item(conn, x["id"])["error"]
    assert item(conn, y["id"])["state"] == "left" and "changed nothing" in item(conn, y["id"])["error"]
    selfwork.retry(conn, y["id"])
    assert item(conn, y["id"])["state"] == "waiting"
    selfwork.drop(conn, x["id"])
    assert item(conn, x["id"])["state"] == "dropped" and not (workspace.path_for(x["id"])).exists()


def test_a_plan_that_cant_be_read_fails_the_goal(conn, src, tmp_path, monkeypatch):
    gid = selfwork.submit(conn, "vague")
    says(tmp_path, monkeypatch, "I would rather not.\n")
    run_inline(conn, conn.execute("SELECT plan_run FROM goals WHERE id=?", (gid,)).fetchone()[0])
    selfwork.tick(conn)
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    assert g["state"] == "failed" and "ITEMS" in g["error"]


def test_reading_briefs():
    items = selfbrief.items_in(PLAN)
    assert [i["title"] for i in items] == ["Add a", "Add b"] and items[0]["files"] == ["eki/a.py"]
    with pytest.raises(ValueError):
        selfbrief.items_in("ITEMS:\n[]")
    assert selfbrief.outcome("blah\nSUMMARY: it works\nnow.\nITEM: partial rest later") == \
        ("partial", "rest later", "it works\nnow.")
    assert selfbrief.outcome("no verdict")[0] == "done"
    assert selfwork.expand(["eki/cli/"], ["eki/cli/a.py", "eki/x.py"]) == {"eki/cli/a.py"}
    assert selfwork.expand(["new.py"], ["eki/x.py"]) == {"new.py"}
    assert "*" in selfwork.expand([], ["eki/x.py"])
