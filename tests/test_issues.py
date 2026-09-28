"""Issues in: an open issue labelled eki in any repo the person owns becomes a goal, with no registration."""
import json

import pytest

from conftest import run_inline
from eki import github, issues, paths, prfollow, projects, projectsetup, prs, selfwork, store, workspace
from test_project_goals import PLAN, says, snapshot

PR = "https://github.com/me/proj/pull/7"


@pytest.fixture
def src(tmp_path, monkeypatch):
    """eki's own source, so a project is never mistaken for it."""
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "x.py").write_text("# x\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def gh(proj, gh_repo):
    """GitHub knows me/proj (the bare repo behind the person's `proj` folder); gh clones it for real."""
    gh_repo.repo("me/proj", gh_repo.bare)
    return gh_repo


def issue(n, title="Add greet", body="make greet.py", login="me", labels=("eki",), repo="me/proj"):
    return {"number": n, "title": title, "body": body, "author": {"login": login},
            "labels": [{"name": lb} for lb in labels], "repository": {"nameWithOwner": repo}}


def found(gh, *listed):
    gh.answer(["search", "issues"], json.dumps(list(listed)))


def goals(conn):
    return conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()


def clone(conn, repo="me/proj"):
    return projects.by_repo(conn, repo)


def settle(conn, repo="me/proj"):
    """The engine runs the setup run; the housekeeping step settles it."""
    run_inline(conn, clone(conn, repo)["setup_run"])
    projectsetup.tick(conn)
    return clone(conn, repo)


def discovered(conn, gh, *listed):
    """First pass makes the project; the clone lands; the next pass (soon, no faked time) takes them."""
    found(gh, *listed)
    first = issues.tick(conn)
    assert settle(conn)["setup"] == "ready"
    return first, issues.tick(conn)


def test_an_issue_in_a_repo_i_own_makes_the_project_then_a_goal(conn, src, gh):
    found(gh, issue(7))
    said = issues.tick(conn)
    p = clone(conn)
    dest = paths.home() / "projects" / "me" / "proj"
    assert p["name"] == p["repo"] == "me/proj" and p["path"] == str(dest) and p["setup"] == "cloning"
    assert "me/proj: issue #7 waits — cloning" in said and goals(conn) == []
    assert ["search", "issues", "--label", "eki", "--state", "open", "--owner", "me", "--limit", "100",
            "--json", "number,title,body,labels,author,repository"] in gh.calls()
    assert ["api", "user", "--jq", ".login"] in gh.calls()
    assert issues.tick(conn) == []                     # not due yet: the clone isn't ready

    p = settle(conn)
    assert p["setup"] == "ready" and p["branch"] == "trunk" and (dest / "app.py").exists()
    said = issues.tick(conn)                           # soon() was called: no need to fake time
    [g] = goals(conn)
    assert g["issue"] == 7 and g["project"] == p["id"] and g["owner"] == "eki" and g["source"] == "issue"
    assert g["state"] == "planning" and g["plan_run"] and not g["draft_run"]
    assert g["text"].startswith("Add greet\n\nmake greet.py") and "(GitHub issue #7 on me/proj)" in g["text"]
    assert any("issue #7 became goal" in s for s in said)
    github.soon("issues")
    issues.tick(conn)
    assert len(goals(conn)) == 1 and conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_an_issue_by_someone_else_is_skipped(conn, src, gh):
    found(gh, issue(8, login="stranger"))
    said = issues.tick(conn)
    assert goals(conn) == [] and clone(conn) is None
    assert said == ["me/proj: issue #8 skipped — by @stranger, not me; only the person's words count"]


def test_a_standing_issue_becomes_a_standing_goal_on_the_clone(conn, src, gh):
    discovered(conn, gh, issue(9, labels=("eki", "standing")))
    [st] = conn.execute("SELECT * FROM standing").fetchall()
    assert st["issue"] == 9 and st["project"] == clone(conn)["id"] and st["state"] == "on"
    assert "(GitHub issue #9 on me/proj)" in st["text"] and goals(conn) == []
    github.soon("issues")
    issues.tick(conn)
    assert len(conn.execute("SELECT * FROM standing").fetchall()) == 1


def test_a_setup_fault_makes_the_issue_wait(conn, src, gh):
    found(gh, issue(5))
    issues.tick(conn)
    conn.execute("UPDATE runs SET prompt=? WHERE id=?",
                 (json.dumps(["/bin/sh", "-c", "echo 'lockfile broken' >&2; exit 1"]), clone(conn)["setup_run"]))
    assert settle(conn)["setup"] == "fault"
    github.soon("issues")                              # a fault isn't `soon`: the next pass on the clock
    said = issues.tick(conn)
    assert said == ["me/proj: issue #5 waits — fault: clone failed: lockfile broken"] and goals(conn) == []


def planned_with_item(conn, gid):
    conn.execute("UPDATE goals SET state='planned' WHERE id=?", (gid,))
    return selfwork.new_item(conn, gid, "greet", "make greet.py", ["greet.py"], [], False)


def test_a_closed_issue_stops_its_work(conn, src, gh):
    discovered(conn, gh, issue(1), issue(2), issue(3, labels=("eki", "standing")))
    g1, g2 = goals(conn)
    iid = planned_with_item(conn, g1["id"])
    done = selfwork.new_item(conn, g1["id"], "old", "x", [], [], False)
    conn.execute("UPDATE items SET state='proposed' WHERE id=?", (done,))
    conn.commit()

    found(gh)
    gh.answer(["issue", "view"], "OPEN\n")
    issues.tick(conn)
    assert selfwork.store_item(conn, iid)["state"] == "waiting"
    assert conn.execute("SELECT state FROM standing").fetchone()["state"] == "on"

    gh.answer(["issue", "view"], "CLOSED\n")
    github.soon("issues")
    said = issues.tick(conn)
    assert ["issue", "view", "1", "--repo", "me/proj", "--json", "state", "--jq", ".state"] in gh.calls()
    assert selfwork.store_item(conn, iid)["state"] == "dropped"
    assert selfwork.store_item(conn, done)["state"] == "proposed"
    g2 = conn.execute("SELECT * FROM goals WHERE id=?", (g2["id"],)).fetchone()
    assert g2["state"] == "left" and g2["error"] == "issue #2 closed"
    assert store.run(conn, g2["plan_run"])["state"] == "cancelled"
    assert conn.execute("SELECT state FROM standing").fetchone()["state"] == "dropped"
    assert any("issue #3 closed" in s for s in said)


def test_a_gh_failure_is_one_line(conn, src, gh):
    gh.answer(["search", "issues"], "HTTP 502: bad gateway\nmore\n", 1)
    assert issues.tick(conn) == ["GitHub: HTTP 502: bad gateway"]


def test_not_due_does_nothing(conn, src, gh, monkeypatch):
    monkeypatch.setattr(github, "due", lambda name, now=None: False)
    assert issues.tick(conn) == [] and gh.calls() == []


def test_a_retired_folder_row_finishes_its_pr_and_takes_nothing_new(conn, src, proj, gh, monkeypatch):
    old = projects.add(conn, proj)
    gid = selfwork.submit(conn, "greet", plan=True, draft=False, owner="eki", source_kind="issue",
                          project=old, issue=7)
    conn.execute("UPDATE goals SET state='planned' WHERE id=?", (gid,))
    iid = selfwork.new_item(conn, gid, "greet", "make greet.py", ["greet.py"], [], False)
    conn.execute("UPDATE items SET state='proposed', pr=?, pr_state='open' WHERE id=?", (PR, iid))
    conn.commit()
    projectsetup.tick(conn)
    row = projects.get(conn, old)
    assert row["state"] == "retired" and row["repo"] == "me/proj"

    _, said = discovered(conn, gh, issue(7), issue(8))
    new = clone(conn)
    assert new["id"] != old
    assert [(g["project"], g["issue"]) for g in goals(conn)] == [(old, 7), (new["id"], 8)]
    github.soon("issues")
    issues.tick(conn)
    assert len(goals(conn)) == 2                       # #7 is never taken twice after the move

    monkeypatch.setattr(github, "due", lambda name, now=None: True)
    gh.answer(["pr", "view"], json.dumps({"state": "OPEN", "comments": [], "reviews": []}))
    prs.tick(conn)
    prfollow.tick(conn)
    views = [c for c in gh.calls() if c[:3] == ["pr", "view", PR]]
    assert len(views) >= 2 and selfwork.store_item(conn, iid)["pr_state"] == "open"


def test_a_dropped_project_ignores_what_was_open_and_a_new_issue_revives_it(conn, src, gh):
    discovered(conn, gh, issue(4))
    p = clone(conn)
    old_run = p["setup_run"]
    projectsetup.drop(conn, "me/proj")
    assert clone(conn)["state"] == "dropped"

    github.soon("issues")
    assert issues.tick(conn) == []                     # #4 was open at the drop
    assert clone(conn)["state"] == "dropped"

    found(gh, issue(4), issue(5))
    github.soon("issues")
    said = issues.tick(conn)
    p = clone(conn)
    assert p["state"] == "on" and p["setup"] == "cloning" and p["setup_run"] != old_run
    assert "me/proj: issue #5 waits — cloning" in said and not any("#4" in s for s in said)
    assert settle(conn)["setup"] == "ready"
    issues.tick(conn)
    assert [g["issue"] for g in goals(conn)] == [4, 5]


def test_the_persons_own_folder_is_never_touched(conn, src, proj, gh, tmp_path, monkeypatch):
    before = snapshot(proj)
    worktrees = workspace.git(proj, "worktree", "list")
    branches = workspace.git(proj, "branch", "--list")
    status = workspace.git(proj, "status", "--porcelain")

    discovered(conn, gh, issue(7))
    [g] = goals(conn)
    plan = store.run(conn, g["plan_run"])
    assert workspace.belongs(store.cwd_of(conn, plan["thread_id"]), clone(conn)["path"])
    says(tmp_path, monkeypatch, PLAN)
    run_inline(conn, plan["id"])
    selfwork.tick(conn)
    [it] = selfwork.items_in(conn, ("building",))
    says(tmp_path, monkeypatch, "SUMMARY: made greet.\n\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "greet.py")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "proposed"

    monkeypatch.setattr(github, "due", lambda name, now=None: True)
    gh.answer(["pr", "create"], f"Creating pull request\n{PR}\n")
    prs.tick(conn)
    assert selfwork.store_item(conn, it["id"])["pr"] == PR
    assert workspace.git(gh.bare, "rev-parse", f"refs/heads/eki/{it['id']}")

    assert snapshot(proj) == before
    assert workspace.git(proj, "worktree", "list") == worktrees
    assert workspace.git(proj, "branch", "--list") == branches
    assert workspace.git(proj, "status", "--porcelain") == status
    assert (proj / "notes.txt").read_text() == "the person's own work in progress\n"
