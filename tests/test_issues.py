"""Issues in: a watched project's GitHub issues labelled eki become background goals."""
import json

import pytest

from eki import github, issues, projects, selfwork, store, workspace


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


@pytest.fixture(autouse=True)
def always_due(monkeypatch):
    monkeypatch.setattr(github, "due", lambda name, now=None: True)


def issue(n, title="Add greet", body="make greet.py", login="me", labels=("eki",)):
    return {"number": n, "title": title, "body": body, "author": {"login": login},
            "labels": [{"name": lb} for lb in labels]}


def listed(gh, *found):
    gh.answer(["issue", "list"], json.dumps(list(found)))


def goals(conn):
    return conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()


def test_an_issue_by_the_person_becomes_a_background_goal(conn, src, proj, gh_repo):
    pid = projects.watch(conn, proj)
    listed(gh_repo, issue(7))
    said = issues.tick(conn)
    [g] = goals(conn)
    assert g["issue"] == 7 and g["project"] == pid and g["owner"] == "eki" and g["source"] == "issue"
    assert g["state"] == "planning" and g["plan_run"] and not g["draft_run"]
    assert g["text"].startswith("Add greet\n\nmake greet.py") and "(GitHub issue #7 on me/proj)" in g["text"]
    assert any("issue #7 became goal" in s for s in said)
    assert ["issue", "list", "--repo", "me/proj", "--label", "eki", "--state", "open", "--limit", "100",
            "--json", "number,title,body,labels,author"] in gh_repo.calls()

    issues.tick(conn)
    assert len(goals(conn)) == 1


def test_an_issue_by_someone_else_is_skipped(conn, src, proj, gh_repo):
    projects.watch(conn, proj)
    listed(gh_repo, issue(8, login="stranger"))
    said = issues.tick(conn)
    assert goals(conn) == []
    assert any("#8 skipped" in s and "@stranger" in s for s in said)


def test_a_standing_issue_becomes_a_standing_goal(conn, src, proj, gh_repo):
    pid = projects.watch(conn, proj)
    listed(gh_repo, issue(9, labels=("eki", "standing")))
    issues.tick(conn)
    [st] = conn.execute("SELECT * FROM standing").fetchall()
    assert st["issue"] == 9 and st["project"] == pid and st["state"] == "on"
    assert "(GitHub issue #9 on me/proj)" in st["text"]
    assert goals(conn) == []
    issues.tick(conn)
    assert len(conn.execute("SELECT * FROM standing").fetchall()) == 1


def planned_with_item(conn, gid):
    conn.execute("UPDATE goals SET state='planned' WHERE id=?", (gid,))
    return selfwork.new_item(conn, gid, "greet", "make greet.py", ["greet.py"], [], False)


def test_a_closed_issue_stops_its_work(conn, src, proj, gh_repo):
    projects.watch(conn, proj)
    listed(gh_repo, issue(1), issue(2), issue(3, labels=("eki", "standing")))
    issues.tick(conn)
    g1, g2 = goals(conn)
    iid = planned_with_item(conn, g1["id"])
    done = selfwork.new_item(conn, g1["id"], "old", "x", [], [], False)
    conn.execute("UPDATE items SET state='proposed' WHERE id=?", (done,))
    conn.commit()

    listed(gh_repo)
    gh_repo.answer(["issue", "view"], "OPEN\n")
    issues.tick(conn)
    assert selfwork.store_item(conn, iid)["state"] == "waiting"
    assert conn.execute("SELECT state FROM goals WHERE id=?", (g2["id"],)).fetchone()["state"] == "planning"
    assert conn.execute("SELECT state FROM standing").fetchone()["state"] == "on"

    gh_repo.answer(["issue", "view"], "CLOSED\n")
    said = issues.tick(conn)
    assert ["issue", "view", "1", "--repo", "me/proj", "--json", "state", "--jq", ".state"] in gh_repo.calls()
    assert selfwork.store_item(conn, iid)["state"] == "dropped"
    assert selfwork.store_item(conn, done)["state"] == "proposed"
    g2 = conn.execute("SELECT * FROM goals WHERE id=?", (g2["id"],)).fetchone()
    assert g2["state"] == "left" and g2["error"] == "issue #2 closed"
    assert store.run(conn, g2["plan_run"])["state"] == "cancelled"
    assert conn.execute("SELECT state FROM standing").fetchone()["state"] == "dropped"
    assert any("issue #3 closed" in s for s in said)


def test_a_watched_project_off_github_runs_no_gh(conn, src, proj, fake_gh):
    workspace.git(proj, "remote", "add", "origin", "https://gitlab.com/me/proj.git")
    projects.watch(conn, proj)
    said = issues.tick(conn)
    assert fake_gh.calls() == []
    assert said == ["proj: not watching issues: origin isn't on GitHub"]


def test_a_gh_failure_is_a_log_line(conn, src, proj, gh_repo):
    projects.watch(conn, proj)
    gh_repo.answer(["issue", "list"], "HTTP 502: bad gateway\nmore\n", 1)
    assert issues.tick(conn) == ["proj: GitHub: HTTP 502: bad gateway"]


def test_not_due_does_nothing(conn, src, proj, gh_repo, monkeypatch):
    monkeypatch.setattr(github, "due", lambda name, now=None: False)
    projects.watch(conn, proj)
    assert issues.tick(conn) == [] and gh_repo.calls() == []


def test_a_drop_that_fails_leaves_the_goal_for_the_next_pass(conn, src, proj, gh_repo, monkeypatch):
    projects.watch(conn, proj)
    listed(gh_repo, issue(4))
    issues.tick(conn)
    [g] = goals(conn)
    iid = selfwork.new_item(conn, g["id"], "greet", "make greet.py", ["greet.py"], [], False)
    conn.commit()
    listed(gh_repo)
    gh_repo.answer(["issue", "view"], "CLOSED\n")
    real = selfwork.drop

    def broken(conn, iid):
        raise OSError("worktree busy")
    monkeypatch.setattr(selfwork, "drop", broken)
    said = issues.tick(conn)
    assert any("couldn't drop for issue #4" in s for s in said)
    assert selfwork.store_item(conn, iid)["state"] == "waiting"
    assert conn.execute("SELECT state FROM goals").fetchone()["state"] == "planning"

    monkeypatch.setattr(selfwork, "drop", real)
    issues.tick(conn)
    assert selfwork.store_item(conn, iid)["state"] == "dropped"
    g = conn.execute("SELECT * FROM goals").fetchone()
    assert g["state"] == "left" and g["error"] == "issue #4 closed"
