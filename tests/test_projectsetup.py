"""eki's own clone of a GitHub repo: clone, guess, install once, fault and retry, retire, drop."""
import json
import os
import subprocess

import pytest

from conftest import run_inline
from eki import db, github, paths, projects, projectsetup, selfwork, standing, store, workspace


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


def make_bare(tmp_path, files):
    """A repo on 'GitHub' (a bare repo), default branch trunk, holding `files`."""
    w = tmp_path / "upstream"
    w.mkdir()
    for name, text in files.items():
        (w / name).parent.mkdir(parents=True, exist_ok=True)
        (w / name).write_text(text)
    workspace.git(w, "init", "-q", "-b", "trunk")
    workspace.git(w, "add", "-A")
    workspace.git(w, "commit", "-q", "-m", "first")
    bare = tmp_path / "me-proj.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(w), str(bare)], check=True, capture_output=True)
    return bare


@pytest.fixture
def gh(tmp_path, fake_gh):
    fake_gh.answer(["auth", "status"], "")
    fake_gh.answer(["api", "user"], "me")
    return fake_gh


def settle(conn, project):
    """Run the project's setup run as the engine would, then the housekeeping step."""
    run_inline(conn, project["setup_run"])
    said = projectsetup.tick(conn)
    return projects.get(conn, project["id"]), said


def test_ensure_clones_into_the_home_and_records_the_branch(conn, tmp_path, gh):
    gh.repo("me/proj", make_bare(tmp_path, {"README.md": "# proj\n"}))
    p = projectsetup.ensure(conn, "me/proj")
    dest = paths.home() / "projects" / "me" / "proj"
    assert p["name"] == p["repo"] == "me/proj" and p["path"] == str(dest)
    assert p["setup"] == "cloning" and p["state"] == "on" and p["branch"] == ""
    assert projectsetup.ensure(conn, "me/proj")["id"] == p["id"]
    argv = json.loads(store.run(conn, p["setup_run"])["prompt"])
    assert argv[:2] == ["/bin/sh", "-c"] and "--filter=blob:none" in argv[2] and "--depth" not in argv[2]
    assert store.cwd_of(conn, store.run(conn, p["setup_run"])["thread_id"]) == str(paths.projects())

    p, said = settle(conn, p)
    assert p["setup"] == "ready" and p["branch"] == "trunk" and p["fault"] is None
    assert (dest / "README.md").exists() and not (tmp_path / "home/projects/me/proj.part").exists()
    assert workspace.git(dest, "symbolic-ref", "--short", "HEAD") == "trunk"
    assert github.repo_of(dest) == "me/proj"
    assert p["check_from"] == "none: no check found" and p["check_cmd"] is None
    assert any("ready" in s for s in said)


def test_a_restart_during_the_clone_runs_it_again_with_one_clone_and_one_row(conn, tmp_path, gh):
    gh.repo("me/proj", make_bare(tmp_path, {"README.md": "# proj\n"}))
    p = projectsetup.ensure(conn, "me/proj")
    argv = json.loads(store.run(conn, p["setup_run"])["prompt"])
    dest = projects.clone_path("me/proj")
    for _ in range(2):                      # the command, run whole again after a restart
        subprocess.run(argv, cwd=paths.projects(), check=True, capture_output=True)
    assert sorted(os.listdir(dest.parent)) == ["proj"] and not (dest / "proj.part").exists()
    (dest.parent / "proj.part").mkdir()     # a restart mid-clone leaves a half one
    p, _ = settle(conn, p)
    assert p["setup"] == "ready" and sorted(os.listdir(dest.parent)) == ["proj"]
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_the_guess_is_recorded_once_and_a_set_check_is_kept(conn, tmp_path, gh):
    gh.repo("me/proj", make_bare(tmp_path, {"Makefile": "check:\n\ttrue\n"}))
    p, said = settle(conn, projectsetup.ensure(conn, "me/proj"))
    assert p["check_cmd"] == "make check" and p["check_from"] == "guessed: Makefile target check"
    assert any("make check" in s for s in said)
    assert projectsetup.tick(conn) == [] and projects.get(conn, p["id"])["check_cmd"] == "make check"

    conn.execute("UPDATE projects SET check_cmd='make x', check_from='set', branch='dev' WHERE id=?", (p["id"],))
    p = projects.get(conn, p["id"])
    conn.execute("UPDATE projects SET setup='fault', fault='clone failed: x' WHERE id=?", (p["id"],))
    projectsetup.retry(conn, "me/proj")
    p, _ = settle(conn, projects.get(conn, p["id"]))
    assert p["check_cmd"] == "make x" and p["check_from"] == "set" and p["branch"] == "dev"


def test_the_install_runs_once_and_worktrees_link_from_the_clone(conn, tmp_path, gh, monkeypatch):
    gh.repo("me/proj", make_bare(tmp_path, {"package.json": json.dumps({"scripts": {"test": "true"}})}))
    monkeypatch.setattr(github, "_last", {"issues": db.now()})
    assert not github.due("issues")
    p, said = settle(conn, projectsetup.ensure(conn, "me/proj"))
    assert p["setup"] == "installing" and p["install_cmd"] == "npm install"
    assert p["check_cmd"] == "npm run test" and p["check_from"] == "guessed: package.json script test via npm"
    assert json.loads(store.run(conn, p["setup_run"])["prompt"]) == ["/bin/sh", "-c", "npm install"]
    run = store.run(conn, p["setup_run"])
    assert store.cwd_of(conn, run["thread_id"]) == p["path"]
    # what npm install would leave, without npm
    conn.execute("UPDATE runs SET prompt=? WHERE id=?",
                 (json.dumps(["/bin/sh", "-c", "mkdir -p node_modules && touch node_modules/x"]), run["id"]))
    p, said = settle(conn, p)
    assert p["setup"] == "ready" and p["fault"] is None and github.due("issues")
    installs = lambda: conn.execute("SELECT COUNT(*) FROM threads WHERE title LIKE 'install %'").fetchone()[0]
    assert installs() == 1
    for key in ("a1", "a2"):
        wt = workspace.path_for(key)
        workspace.git(p["path"], "worktree", "add", "-q", "-b", f"eki/{key}", str(wt))
        assert projects.link_deps(p, wt) == ["node_modules"]
        assert os.readlink(wt / "node_modules") == os.path.join(p["path"], "node_modules")
    projectsetup.tick(conn)
    assert installs() == 1


def test_a_failing_install_is_a_fault_until_retried(conn, tmp_path, gh):
    gh.repo("me/proj", make_bare(tmp_path, {"package.json": json.dumps({"scripts": {"test": "true"}}),
                                            "yarn.lock": ""}))
    p, _ = settle(conn, projectsetup.ensure(conn, "me/proj"))
    conn.execute("UPDATE runs SET prompt=? WHERE id=?",
                 (json.dumps(["/bin/sh", "-c", "echo 'lockfile broken' >&2; exit 1"]), p["setup_run"]))
    p, said = settle(conn, p)
    assert p["setup"] == "fault" and p["fault"] == "install failed: lockfile broken"
    assert any("--retry" in s for s in said)
    assert projectsetup.tick(conn) == [] and projects.get(conn, p["id"])["setup"] == "fault"   # no loop
    old = p["setup_run"]
    assert "install started again" in projectsetup.retry(conn, "me/proj")
    p = projects.get(conn, p["id"])
    assert p["setup"] == "installing" and p["fault"] is None and p["setup_run"] != old
    assert json.loads(store.run(conn, p["setup_run"])["prompt"])[2] == "yarn install --frozen-lockfile"
    with pytest.raises(ValueError):
        projectsetup.retry(conn, "me/proj")


def test_a_failing_clone_is_a_fault(conn, tmp_path, gh):
    p, _ = settle(conn, projectsetup.ensure(conn, "me/nothing"))
    assert p["setup"] == "fault" and p["fault"].startswith("clone failed: ")
    assert "clone started again" in projectsetup.retry(conn, "me/nothing")


def test_a_folder_row_on_github_retires_and_its_standing_goal_moves(conn, src, proj, gh_repo):
    old = projects.add(conn, proj)
    sid = standing.add(conn, str(proj), "keep it tidy")
    gh_repo.repo("me/proj", gh_repo.bare)
    said = projectsetup.tick(conn)
    row = projects.get(conn, old)
    assert row["state"] == "retired" and row["repo"] == "me/proj"
    assert any("retired" in s for s in said)
    new = projects.by_repo(conn, "me/proj")
    assert new["id"] != old and new["setup"] == "cloning"
    st = conn.execute("SELECT * FROM standing WHERE id=?", (sid,)).fetchone()
    assert standing._held(conn, st) == "moving to eki's own clone of me/proj"
    assert st["project"] == old

    settle(conn, new)
    st = conn.execute("SELECT * FROM standing WHERE id=?", (sid,)).fetchone()
    assert st["project"] == new["id"] and standing._held(conn, st) != "moving to eki's own clone of me/proj"
    projectsetup.tick(conn)
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2


def test_a_folder_row_off_github_stays(conn, src, proj):
    pid = projects.add(conn, proj)
    assert projectsetup.tick(conn) == []
    assert projects.get(conn, pid)["repo"] is None and projects.get(conn, pid)["state"] == "on"


def ready_with_work(conn, tmp_path, gh):
    gh.repo("me/proj", make_bare(tmp_path, {"README.md": "# proj\n"}))
    p, _ = settle(conn, projectsetup.ensure(conn, "me/proj"))
    gid = selfwork.submit(conn, "greet", plan=True, draft=False, owner="eki", source_kind="issue",
                          project=p["id"], issue=7)
    conn.execute("UPDATE goals SET state='planned' WHERE id=?", (gid,))
    iid = selfwork.new_item(conn, gid, "greet", "make greet.py", ["greet.py"], [], False)
    done = selfwork.new_item(conn, gid, "done", "was proposed", ["x.py"], [], False)
    conn.execute("UPDATE items SET state='proposed', pr_state='open' WHERE id=?", (done,))
    sid = standing.add(conn, "", "more", project=p["id"], issue=8)
    return p, iid, done, sid


def test_drop_removes_the_clone_stops_work_and_ignores_open_issues(conn, src, tmp_path, gh):
    p, iid, done, sid = ready_with_work(conn, tmp_path, gh)
    gh.answer(["search", "issues"], json.dumps([
        {"number": 7, "repository": {"nameWithOwner": "me/proj"}},
        {"number": 9, "repository": {"nameWithOwner": "me/proj"}},
        {"number": 3, "repository": {"nameWithOwner": "me/other"}}]))
    said = projectsetup.drop(conn, "me/proj")
    row = projects.get(conn, p["id"])
    assert row["state"] == "dropped" and json.loads(row["ignored"]) == [7, 9]
    assert row["check_from"] is None and row["check_cmd"] is None and row["fault"] is None
    assert not os.path.exists(p["path"]) and paths.projects().exists()
    assert selfwork.store_item(conn, iid)["state"] == "dropped"
    assert selfwork.store_item(conn, done)["state"] == "proposed"
    assert conn.execute("SELECT state FROM standing WHERE id=?", (sid,)).fetchone()[0] == "dropped"
    assert "#7, #9" in said and "open PRs are left to you" in said
    with pytest.raises(KeyError):
        projectsetup.drop(conn, "me/else")

    again = projectsetup.ensure(conn, "me/proj")        # a newer issue brings it back, same row
    assert again["id"] == p["id"] and again["state"] == "on" and again["setup"] == "cloning"


def test_drop_falls_back_to_its_own_issues_when_gh_fails(conn, src, tmp_path, gh):
    p, *_ = ready_with_work(conn, tmp_path, gh)
    gh.answer(["search", "issues"], "", code=1)
    said = projectsetup.drop(conn, "me/proj")
    assert json.loads(projects.get(conn, p["id"])["ignored"]) == [7, 8]
    assert "GitHub didn't answer" in said


def test_drop_never_removes_a_folder_outside_the_projects_home(conn, proj):
    assert projectsetup._remove(proj) is False and proj.exists()
