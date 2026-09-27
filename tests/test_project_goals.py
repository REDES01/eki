"""A goal on a person's own repo: plan → build on eki/<id> → its check → proposed → applied once merged."""
import json
from pathlib import Path

import pytest

from eki import checkslots, paths, projects, projectwork, queue, selfwork, store, workspace
from conftest import run_inline

PLAN = """Read the README.

ITEMS:
[{"title": "Add greet", "spec": "make greet.py", "files": ["greet.py"], "deps": [], "independent": false}]
"""


@pytest.fixture
def src(tmp_path, monkeypatch):
    """eki's own source, so the queue has an integration repo to look at."""
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "x.py").write_text("# x\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    (r / "README.md").write_text("# proj\n")
    (r / ".venv").mkdir()                               # untracked, not ignored
    (r / ".venv" / "marker").write_text("venv\n")
    (r / "notes.txt").write_text("the person's own work in progress\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "app.py", "README.md")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def says(tmp_path, monkeypatch, text):
    p = tmp_path / "says.txt"
    p.write_text(text)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))


def snapshot(proj):
    return (workspace.head(proj), workspace.git(proj, "symbolic-ref", "--short", "HEAD"),
            workspace.git(proj, "status", "--porcelain", "--untracked-files=no"),
            {p.name: p.read_text() for p in proj.iterdir() if p.is_file()}, sorted(p.name for p in proj.iterdir()))


def planned(conn, tmp_path, monkeypatch, proj, check):
    pid = projects.add(conn, proj, check=check)
    gid = selfwork.submit(conn, "add a greeting", project=pid)
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    assert g["project"] == pid and g["state"] == "planning"
    plan = store.run(conn, g["plan_run"])
    assert "a project at" in plan["prompt"] and str(proj.resolve()) in plan["prompt"]
    assert "ITEMS:" in plan["prompt"] and "don't depend on each other" in plan["prompt"]
    wt = store.cwd_of(conn, plan["thread_id"])
    assert wt.endswith(f"plan-{gid}") and workspace.belongs(wt, proj)
    assert workspace.head(wt) == workspace.head(proj)
    says(tmp_path, monkeypatch, PLAN)
    run_inline(conn, plan["id"])
    said = selfwork.tick(conn)
    assert any("1 item(s) planned" in s for s in said)
    assert not workspace.git(proj, "branch", "--list", f"eki/plan-{gid}")
    return pid, selfwork.items_in(conn, ("building",))[0]


def built(conn, tmp_path, monkeypatch, it):
    says(tmp_path, monkeypatch, "SUMMARY: made greet.\n\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "greet.py")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    return selfwork.store_item(conn, it["id"])


def test_a_project_goal_lands_as_a_branch_and_is_applied_once_merged(conn, src, proj, tmp_path, monkeypatch):
    before = snapshot(proj)
    pid, it = planned(conn, tmp_path, monkeypatch, proj, "true")
    assert it["branch"] == f"eki/{it['id']}" and it["base"] == before[0]
    assert workspace.belongs(it["worktree"], proj)
    build = store.run(conn, it["run_id"])
    assert "You are changing proj" in build["prompt"] and f"eki/{it['id']}" in build["prompt"]
    assert "`true`" in build["prompt"] and "ITEM: done" in build["prompt"]
    assert build["priority"] == "now"

    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging" and json.loads(it["touched"]) == ["greet.py"]
    judge = store.run(conn, it["run_id"])
    assert judge["provider"] == "command" and json.loads(judge["prompt"]) == ["/bin/sh", "-c", "true", "eki-judge"]
    assert checkslots.is_judge(judge)                     # a project's check takes a check slot
    run_inline(conn, judge["id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed"
    assert workspace.git(proj, "log", "-1", "--format=%s", it["branch"]) == "eki: Add greet"
    assert ".venv" not in workspace.git(proj, "show", "--stat", "--format=", it["commit_sha"])
    assert snapshot(proj) == before                       # the person's checkout never moved

    rp = paths.config("routing")
    rp.write_text(json.dumps({**json.loads(rp.read_text()), "self": {"autonomy": "apply"}}))
    queue.tick(conn)
    queue._admit(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed" and it["queued_at"] is None   # never in eki's queue
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "proposed"

    workspace.git(proj, "merge", "-q", "--no-edit", it["branch"])     # the person merges it
    said = selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "applied"
    assert any("applied" in s and "trunk" in s for s in said)


def test_a_red_check_is_retried_once_then_unfit(conn, src, proj, tmp_path, monkeypatch):
    before = snapshot(proj)
    _, it = planned(conn, tmp_path, monkeypatch, proj, "false")
    it = built(conn, tmp_path, monkeypatch, it)
    run_inline(conn, it["run_id"])
    said = selfwork.tick(conn)
    assert any("one more try" in s for s in said)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "building" and it["tries"] == 2               # started again at once
    assert "failed its checks" in store.run(conn, it["run_id"])["prompt"]
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging"
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "unfit"
    assert snapshot(proj) == before


def test_no_check_is_proposed_not_judged_and_the_venv_is_linked(conn, src, proj, tmp_path, monkeypatch):
    _, it = planned(conn, tmp_path, monkeypatch, proj, None)
    wt = Path(it["worktree"])
    assert (wt / ".venv").is_symlink() and (wt / ".venv" / "marker").exists()
    exclude = Path(workspace.git(wt, "rev-parse", "--git-path", "info/exclude"))
    exclude = exclude if exclude.is_absolute() else wt / exclude
    assert ".venv" in exclude.read_text().splitlines()
    assert "the project's own tests" in store.run(conn, it["run_id"])["prompt"]
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "proposed" and it["verdict"] == projectwork.NOT_JUDGED
    assert json.loads(it["touched"]) == ["greet.py"]


def test_drop_removes_the_worktree_from_the_project(conn, src, proj, tmp_path, monkeypatch):
    _, it = planned(conn, tmp_path, monkeypatch, proj, "true")
    selfwork.drop(conn, it["id"])
    assert not Path(it["worktree"]).exists()
    assert not workspace.git(proj, "branch", "--list", it["branch"])     # nothing committed: branch gone
    selfwork.retry(conn, it["id"])
    selfwork.tick(conn)
    again = selfwork.store_item(conn, it["id"])
    assert again["state"] == "building" and workspace.belongs(again["worktree"], proj)
