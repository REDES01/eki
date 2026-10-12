"""'eki merges here' stays here, and a 'github' row keeps its folder: base, PRs, retiring, goal add, the listing."""
import pytest

from eki import db, github, projectchats, projects, projectsetup, prs, selfwork, standing, workspace
from eki.cli import main
from eki.cli.goal import path_line
from test_prs import proposed
from test_project_goals import src  # noqa: F401 — src is a fixture


def lands(conn, pid, value, repo=None):
    with db.tx(conn):
        conn.execute("UPDATE projects SET lands=?, repo=?, folder=CASE WHEN ? IS NULL THEN folder ELSE path END "
                     "WHERE id=?", (value, repo, repo, pid))
    return projects.get(conn, pid)


def test_base_on_a_here_project_ignores_origin(conn, proj, gh_repo):
    pid = projects.add(conn, proj)
    old = workspace.git(proj, "rev-parse", "HEAD")
    (proj / "more.py").write_text("x = 1\n")
    workspace.git(proj, "add", "more.py")
    workspace.git(proj, "commit", "-q", "-m", "local only")
    new = workspace.git(proj, "rev-parse", "HEAD")
    assert projects.base(projects.get(conn, pid)) == old              # unset: origin's branch, fetched
    workspace.git(proj, "update-ref", "-d", "refs/remotes/origin/trunk")
    assert projects.base(lands(conn, pid, "here")) == new
    assert not workspace.git(proj, "rev-parse", "--verify", "-q", "refs/remotes/origin/trunk", check=False)


def test_prs_tick_makes_no_gh_call_for_a_here_project(conn, src, proj, gh_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(github, "due", lambda name, now=None: True)
    pid, it = proposed(conn, tmp_path, monkeypatch, proj)
    lands(conn, pid, "here")
    (gh_repo.dir / "calls.jsonl").unlink(missing_ok=True)
    assert prs.tick(conn) == []
    assert gh_repo.calls() == []
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed" and it["pr"] is None and it["why"] is None
    assert not workspace.git(gh_repo.bare, "branch", "--list", f"eki/{it['id']}")


@pytest.mark.parametrize("said", ["here", "github"])
def test_retire_and_merge_leave_a_row_that_said(conn, proj, gh_repo, said):
    pid = projects.add(conn, proj)
    standing.add(conn, str(proj), "keep it tidy", project=pid)
    lands(conn, pid, said)
    assert projectsetup._retire(conn) == []
    assert projectsetup._chat_row(conn, "me/proj") is None
    p = projects.get(conn, pid)
    assert (p["state"] or "on") == "on" and p["repo"] is None


def test_retire_still_retires_a_row_that_never_said(conn, proj, gh_repo):
    pid = projects.add(conn, proj)
    standing.add(conn, str(proj), "keep it tidy", project=pid)
    assert len(projectsetup._retire(conn)) == 1
    assert projects.get(conn, pid)["state"] == "retired"


def test_goal_add_lands_on_the_here_row(conn, capsys, proj, gh_repo):
    pid = projects.add(conn, proj)
    lands(conn, pid, "here")
    assert main(["goal", "add", str(proj), "keep the tests fast"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0] == "proj: eki merges here" and "clone" not in "\n".join(out)
    assert conn.execute("SELECT project FROM standing WHERE id=?", (out[-1],)).fetchone()[0] == pid
    assert projects.by_repo(conn, "me/proj") is None
    assert not any(c[:2] == ["repo", "clone"] for c in gh_repo.calls())


def test_goal_add_lands_on_the_github_row_in_its_folder(conn, capsys, proj, gh_repo):
    pid = projects.add(conn, proj)
    lands(conn, pid, "github", "me/proj")
    assert main(["goal", "add", str(proj), "keep the tests fast", "--check", "make x"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0] == f"me/proj: pull requests from {proj.resolve()}"
    assert conn.execute("SELECT project FROM standing WHERE id=?", (out[-1],)).fetchone()[0] == pid
    p = projects.get(conn, pid)
    assert p["path"] == str(proj.resolve()) and p["check_cmd"] == "make x"
    assert [r["id"] for r in conn.execute("SELECT id FROM projects").fetchall()] == [pid]


def test_the_listing_says_worktrees_of_the_folder_for_a_github_row(conn, proj, gh_repo):
    pid = projects.add(conn, proj)
    p = lands(conn, pid, "github", "me/proj")
    got = projectchats.entry(conn, p)
    assert got["background"] == f"worktrees of {proj.resolve()}; changes come as PRs on me/proj"
    assert got["kind"] == "github"


def test_path_line_says_you_chose_here(conn, proj, gh_repo):
    pid = projects.add(conn, proj)
    assert "GitHub me/proj" in path_line(projects.get(conn, pid))
    assert path_line(lands(conn, pid, "here")) == "  path: local — eki merges here (you chose it)"
