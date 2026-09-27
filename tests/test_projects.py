import json

import pytest

from eki import integration, projects, selfwork, store, workspace


@pytest.fixture
def src(tmp_path, monkeypatch):
    """eki's own source, so integration.repo() has something to clone."""
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
    (r / ".gitignore").write_text(".venv/\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def test_add_is_idempotent_and_defaults(conn, proj):
    pid = projects.add(conn, proj)
    assert projects.add(conn, str(proj) + "/") == pid
    p = projects.get(conn, pid)
    assert p["branch"] == "trunk" and p["name"] == "proj" and p["path"] == str(proj.resolve())
    assert p["check_cmd"] is None
    assert [r["id"] for r in projects.all(conn)] == [pid]
    assert projects.base(p) == workspace.head(proj)


def test_add_refuses_a_folder_that_is_no_repo_or_has_no_commit(conn, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError):
        projects.add(conn, plain)
    empty = tmp_path / "empty"
    empty.mkdir()
    workspace.git(empty, "init", "-q")
    with pytest.raises(ValueError):
        projects.add(conn, empty)
    assert projects.all(conn) == []


def test_base_is_the_branch_tip_not_head(conn, proj):
    pid = projects.add(conn, proj, branch="trunk")
    tip = workspace.head(proj)
    workspace.git(proj, "checkout", "-q", "-b", "side")
    (proj / "app.py").write_text("print('side')\n")
    workspace.git(proj, "commit", "-qam", "side")
    assert projects.base(projects.get(conn, pid)) == tip


def test_check_argv_three_ways(conn, proj):
    given = projects.get(conn, projects.add(conn, proj, check="make test"))
    assert json.loads(projects.check_argv(given)) == ["/bin/sh", "-c", "make test", "eki-judge"]
    conn.execute("UPDATE projects SET check_cmd=NULL")
    none = projects.get(conn, given["id"])
    assert projects.check_argv(none) is None
    (proj / "bin").mkdir()
    (proj / "bin" / "check").write_text("#!/bin/sh\nexit 0\n")
    assert projects.check_argv(none) is None               # not executable
    (proj / "bin" / "check").chmod(0o755)
    assert json.loads(projects.check_argv(none)) == ["/bin/sh", "-c", "bin/check", "eki-judge"]


def test_link_deps_links_an_untracked_venv_and_keeps_it_out_of_git(conn, proj, tmp_path):
    (proj / ".venv" / "bin").mkdir(parents=True)
    p = projects.get(conn, projects.add(conn, proj))
    wt = tmp_path / "wt"
    workspace.git(proj, "worktree", "add", "-q", "-b", "eki/x", str(wt), "trunk")
    assert projects.link_deps(p, wt) == [".venv"]
    assert (wt / ".venv").is_symlink()
    assert projects.link_deps(p, wt) == [".venv"]           # again: nothing new
    exclude = workspace.git(wt, "rev-parse", "--git-path", "info/exclude")
    excl = (wt / exclude) if not exclude.startswith("/") else exclude
    assert open(excl).read().splitlines().count(".venv") == 1
    assert workspace.git(wt, "status", "--porcelain") == ""


def test_repo_for_eki_itself_is_the_integration_repo(conn, src):
    gid = selfwork.submit(conn, "a thing", plan=False)
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    assert g["project"] is None and projects.repo_for(conn, g) == integration.repo()


def test_is_self(src, proj):
    assert projects.is_self(src) and not projects.is_self(proj)


def test_a_project_goal_plans_in_the_project_repo_without_moving_it(conn, src, proj):
    pid = projects.add(conn, proj)
    head, branch = workspace.head(proj), workspace.git(proj, "symbolic-ref", "--short", "HEAD")
    gid = selfwork.submit(conn, "make it better", owner="eki", source_kind="standing",
                          project=pid, standing_id="s1")
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    assert (g["project"], g["standing_id"], g["state"]) == (pid, "s1", "planning")
    assert projects.repo_for(conn, g) == proj.resolve()
    cwd = store.cwd_of(conn, store.run(conn, g["plan_run"])["thread_id"])
    assert cwd.endswith(f"plan-{gid}") and workspace.belongs(cwd, proj)
    assert workspace.git(proj, "branch", "--list", f"eki/plan-{gid}")
    assert workspace.head(cwd) == head
    assert workspace.head(proj) == head
    assert workspace.git(proj, "symbolic-ref", "--short", "HEAD") == branch
    assert workspace.git(proj, "status", "--porcelain") == ""
