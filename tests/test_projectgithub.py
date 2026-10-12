"""A new project from GitHub (eki/projectgithub.py) and POST /api/projects's GitHub keys (api_projects.create):
where the clone goes, the row and the clone run, reuse, Start new with a GitHub repository, and the warning."""
import json
import os
import subprocess

import pytest

from conftest import run_inline
from eki import api_projects, apiroutes, github, paths, projectchats, projectgithub, projects, workspace


def bare(tmp_path, name="atlas"):
    """A throwaway repository to clone from: one commit on main."""
    src = tmp_path / f"{name}-src"
    src.mkdir()
    (src / "README.md").write_text(f"# {name}\n")
    workspace.git(src, "init", "-q", "-b", "main")
    workspace.git(src, "add", "README.md")
    workspace.git(src, "commit", "-q", "-m", "first")
    out = tmp_path / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(out)], check=True, capture_output=True)
    return out


def signed_in(fake_gh):
    fake_gh.answer(["auth", "status"], "")
    fake_gh.answer(["api", "user"], "me")


def dest(name):
    return projectchats.norm(paths.user_home() / "Projects" / name)


def clone_runs(conn):
    return conn.execute("SELECT r.*, t.title FROM runs r JOIN threads t ON t.id=r.thread_id "
                        "WHERE t.title LIKE 'clone %'").fetchall()


# ---- clone_to ---------------------------------------------------------------------------------

def test_clone_to_a_new_folder(home):
    got = projectgithub.clone_to("https://github.com/me/atlas.git")
    assert got == {"repo": "me/atlas", "folder": dest("atlas"), "exists": False, "reuse": False, "error": ""}


def test_clone_to_reuses_a_clone_of_it(tmp_path, fake_gh):
    fake_gh.repo("me/atlas", bare(tmp_path))
    os.makedirs(paths.user_home() / "Projects")
    subprocess.run(["gh", "repo", "clone", "me/atlas", dest("atlas")], check=True)
    got = projectgithub.clone_to("me/atlas")
    assert got["exists"] and got["reuse"] and got["error"] == ""


def test_clone_to_a_foreign_folder_names_it(home):
    os.makedirs(dest("atlas"))
    got = projectgithub.clone_to("me/atlas")
    assert got["exists"] and not got["reuse"]
    assert dest("atlas") in got["error"] and "me/atlas" in got["error"]


def test_clone_to_a_bad_name(home):
    assert projectgithub.clone_to("not a repo")["error"] == "say OWNER/REPO or a GitHub URL"


def test_the_route(conn):
    fn, m = apiroutes.match("GET", "/api/github/clone-to")
    assert fn is api_projects.clone_to
    assert fn(conn, m, {"repo": "me/atlas"}, {})["folder"] == dest("atlas")


# ---- start ------------------------------------------------------------------------------------

def test_start_makes_the_row_and_the_clone_run_without_waiting(conn, tmp_path, fake_gh):
    signed_in(fake_gh)
    fake_gh.repo("me/atlas", bare(tmp_path))
    got = api_projects.create(conn, {"github": "https://github.com/me/atlas", "description": "Maps"})
    where = dest("atlas")
    assert got["folder"] == where and got["lands"] == "github" and got["cloning"] is True
    assert not os.path.exists(where)                               # never made here: gh makes it
    p = projects.get(conn, got["id"])
    assert (p["name"], p["path"], p["folder"], p["branch"], p["repo"], p["lands"], p["description"]) == \
        ("atlas", where, where, "", "me/atlas", "github", "Maps")
    runs = clone_runs(conn)
    assert len(runs) == 1 and runs[0]["title"] == f"clone me/atlas to {where}"
    assert json.loads(runs[0]["prompt"]) == ["gh", "repo", "clone", "me/atlas", where]
    assert runs[0]["state"] == "queued" and runs[0]["provider"] == "command"
    assert run_inline(conn, runs[0]["id"])["state"] == "done"
    assert os.path.isdir(os.path.join(where, ".git"))
    assert api_projects.show(conn, got["id"])["cloning"] is False
    assert not any(c[:2] == ["repo", "create"] for c in fake_gh.calls())


def test_start_with_a_name_and_lands_here(conn, tmp_path, fake_gh):
    fake_gh.repo("me/atlas", bare(tmp_path))
    got = api_projects.create(conn, {"github": "me/atlas", "name": "Maps", "lands": "here"})
    p = projects.get(conn, got["id"])
    assert p["name"] == "Maps" and p["lands"] == "here" and p["repo"] is None
    assert got["cloning"] is True and api_projects.show(conn, got["id"])["cloning"] is True   # no repo: by folder


def test_start_reuses_a_clone_and_the_same_folder_again_is_the_same_project(conn, tmp_path, fake_gh):
    signed_in(fake_gh)
    fake_gh.repo("me/atlas", bare(tmp_path))
    os.makedirs(paths.user_home() / "Projects")
    subprocess.run(["gh", "repo", "clone", "me/atlas", dest("atlas")], check=True)
    got = api_projects.create(conn, {"github": "me/atlas"})
    assert clone_runs(conn) == [] and got["cloning"] is False
    assert projects.get(conn, got["id"])["branch"] == "main"
    again = api_projects.create(conn, {"github": "git@github.com:me/atlas.git"})
    assert again["id"] == got["id"] and again["existing"]
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_start_refuses_a_bad_name_and_a_foreign_folder(conn, home):
    with pytest.raises(ValueError, match="say OWNER/REPO or a GitHub URL"):
        api_projects.create(conn, {"github": "what?"})
    os.makedirs(dest("atlas"))
    with pytest.raises(ValueError, match="isn't a clone of me/atlas"):
        api_projects.create(conn, {"github": "me/atlas"})
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


# ---- Start new with a GitHub repository -------------------------------------------------------

def test_create_repo_is_private_by_default(conn, fake_gh, monkeypatch):
    signed_in(fake_gh)
    fake_gh.answer(["repo", "create"], "")
    real = github._gh

    def gh(args, cwd=None):                     # what real gh does: the origin is set
        out = real(args, cwd)
        if args[:2] == ["repo", "create"]:
            workspace.git(args[args.index("--source") + 1], "remote", "add", "origin",
                          f"https://github.com/me/{args[2]}.git")
        return out
    monkeypatch.setattr(github, "_gh", gh)
    got = api_projects.create(conn, {"name": "atlas", "create_repo": True})
    made = [c for c in fake_gh.calls() if c[:2] == ["repo", "create"]]
    assert made == [["repo", "create", "atlas", "--private", "--source", got["folder"], "--remote", "origin"]]
    assert got["lands"] == "github" and "warning" not in got
    assert projects.get(conn, got["id"])["repo"] == "me/atlas"
    pub = api_projects.create(conn, {"name": "open", "create_repo": True, "private": False})
    assert [c for c in fake_gh.calls() if c[:3] == ["repo", "create", "open"]][0][3] == "--public"
    assert pub["lands"] == "github"


def test_a_gh_failure_leaves_the_project_landing_here_with_a_warning(conn, fake_gh):
    signed_in(fake_gh)
    fake_gh.answer(["repo", "create"], "GraphQL: Name already exists on this account", 1)
    got = api_projects.create(conn, {"name": "atlas", "create_repo": True})
    assert got["lands"] == "here" and "Name already exists on this account" in got["warning"]
    p = projects.get(conn, got["id"])
    assert p["lands"] == "here" and p["repo"] is None and os.path.isdir(got["folder"])


def test_create_repo_is_only_for_start_new(conn, proj, fake_gh):
    got = api_projects.create(conn, {"name": "proj", "folder": str(proj), "create_repo": True})
    assert not any(c[:2] == ["repo", "create"] for c in fake_gh.calls()) and got["lands"] == "here"


# ---- lands for a folder -----------------------------------------------------------------------

def test_lands_here_for_a_folder(conn, proj):
    got = api_projects.create(conn, {"name": "proj", "folder": str(proj), "lands": "here"})
    assert got["lands"] == "here" and got["cloning"] is False and projects.get(conn, got["id"])["lands"] == "here"


def test_pull_requests_need_a_github_remote(conn, proj, fake_gh):
    signed_in(fake_gh)
    with pytest.raises(ValueError, match="GitHub remote"):
        api_projects.create(conn, {"name": "proj", "folder": str(proj), "lands": "github"})
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_pull_requests_for_a_folder_on_github(conn, proj, gh_repo):
    got = api_projects.create(conn, {"name": "proj", "folder": str(proj), "lands": "github"})
    assert got["lands"] == "github" and projects.get(conn, got["id"])["repo"] == "me/proj"
