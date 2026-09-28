"""`eki goal`: add, pause, resume, drop, now and the listing, GitHub projects by OWNER/REPO."""
import subprocess

import pytest

from conftest import run_inline
from eki import budget, db, paths, projects, projectsetup, selfwork, standing, store, workspace
from eki.cli import main


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "garden"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r.resolve()


def row(conn, sid):
    return conn.execute("SELECT * FROM standing WHERE id=?", (sid,)).fetchone()


def added(capsys, proj, *extra):
    assert main(["goal", "add", str(proj), "keep the tests fast\nand small", *extra]) == 0
    return capsys.readouterr().out.strip()


def test_add_records_a_project_and_a_standing_goal(conn, capsys, proj):
    sid = added(capsys, proj, "--check", "true", "--branch", "trunk")
    st = row(conn, sid)
    assert st["state"] == "on" and st["text"].startswith("keep the tests fast")
    p = conn.execute("SELECT * FROM projects WHERE id=?", (st["project"],)).fetchone()
    assert p["path"] == str(proj) and p["branch"] == "trunk" and p["check_cmd"] == "true"


def test_add_refuses_a_folder_that_isnt_a_repo(conn, capsys, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["goal", "add", str(plain), "anything"]) == 1
    assert "not a git repo" in capsys.readouterr().err
    assert conn.execute("SELECT COUNT(*) FROM standing").fetchone()[0] == 0


def test_pause_resume_now_drop(conn, capsys, proj):
    sid = added(capsys, proj)
    assert main(["goal", "pause", sid[:6]]) == 0
    assert row(conn, sid)["state"] == "paused"
    assert main(["goal", "resume", sid]) == 0
    assert row(conn, sid)["state"] == "on"
    with db.tx(conn):
        conn.execute("UPDATE standing SET rest_until=? WHERE id=?", (db.now() + 3600, sid))
    assert main(["goal", "now", sid]) == 0
    assert row(conn, sid)["rest_until"] <= db.now()
    assert main(["goal", "drop", sid]) == 0
    assert row(conn, sid)["state"] == "dropped"
    capsys.readouterr()
    assert main(["goal"]) == 0
    assert sid not in capsys.readouterr().out


def test_an_unknown_id_fails(conn, capsys):
    assert main(["goal", "pause", "nope"]) == 1
    assert "no standing goal" in capsys.readouterr().err


def test_the_listing(conn, capsys, proj):
    sid = added(capsys, proj)
    st = row(conn, sid)
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("UPDATE standing SET rounds=2, why=? WHERE id=?", ("waiting for the Mac: busy", sid))
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project, standing_id)"
                     " VALUES (?,?,?,?,?,?,?,?)", (gid, "round", "standing", "eki", "planned", db.now(),
                                                   st["project"], sid))
    proposed = selfwork.new_item(conn, gid, "say hello", "do it", ["app.py"], [], False)
    selfwork._set(conn, proposed, state="proposed", branch=f"eki/{proposed}")
    waiting = selfwork.new_item(conn, gid, "say bye", "do it", ["bye.py"], [], False)
    done = selfwork.new_item(conn, gid, "old one", "done", ["old.py"], [], False)
    selfwork._set(conn, done, state="applied")
    assert main(["goal"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"{sid}  garden  on  keep the tests fast"
    assert "  why: waiting for the Mac: busy" in lines
    assert "  rounds: 2" in lines
    assert f"git -C {proj} merge eki/{proposed}" in out
    assert any(waiting in ln and "waiting" in ln and f"eki/{waiting}" in ln for ln in lines)
    assert done not in out
    assert lines[-1] == budget.describe()


def test_a_resting_goal_says_until_when(conn, capsys, proj):
    sid = added(capsys, proj)
    with db.tx(conn):
        conn.execute("UPDATE standing SET rest_until=?, why=? WHERE id=?",
                     (db.now() + 24 * 3600, "nothing worth doing now: all good", sid))
    assert main(["goal"]) == 0
    out = capsys.readouterr().out
    assert "why: resting until" in out and "all good" in out


def test_no_goals_still_shows_the_budget(conn, capsys):
    assert main(["goal"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "no standing goals" in lines[0] and lines[-1].startswith("budget:")


def project(conn, proj):
    return conn.execute("SELECT * FROM projects WHERE path=?", (str(proj),)).fetchone()


def test_issues_is_a_no_op_note(conn, capsys, proj, fake_gh):
    assert main(["goal", "add", str(proj), "--issues"]) == 0
    assert capsys.readouterr().out.strip() == "not needed: issues labelled eki in any repo you own are taken"
    assert conn.execute("SELECT COUNT(*) FROM standing").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0 and fake_gh.calls() == []
    out = added(capsys, proj, "--issues").splitlines()
    assert out[0].startswith("not needed:") and row(conn, out[-1])["state"] == "on"


def test_add_needs_text(conn, capsys, proj):
    assert main(["goal", "add", str(proj)]) == 1
    assert "say the goal in words" in capsys.readouterr().err


def test_there_is_no_unwatch(conn, capsys, proj):
    with pytest.raises(SystemExit):
        main(["goal", "unwatch", str(proj)])


def looks(folder):
    return [workspace.git(folder, *a) for a in (["worktree", "list"], ["branch", "-a"], ["status", "--short"],
                                                 ["config", "--list", "--local"])]


def test_add_on_a_github_folder_goes_to_ekis_own_clone(conn, capsys, proj, gh_repo):
    before = looks(proj)
    out = added(capsys, proj, "--check", "make x").splitlines()
    clone = paths.projects() / "me" / "proj"
    assert out[0] == f"me/proj: working in eki's own clone at {clone}, not {proj}"
    p = projects.by_repo(conn, "me/proj")
    assert row(conn, out[-1])["project"] == p["id"] and p["path"] == str(clone)
    assert p["setup"] == "cloning" and p["check_cmd"] == "make x" and p["check_from"] == "set"
    assert project(conn, proj) is None and looks(proj) == before
    assert not any(c[:2] == ["repo", "clone"] for c in gh_repo.calls())     # the clone is a run, not now


def ready_project(conn, gh, tmp_path, files):
    w = tmp_path / "upstream"
    w.mkdir()
    for name, text in files.items():
        (w / name).write_text(text)
    workspace.git(w, "init", "-q", "-b", "trunk")
    workspace.git(w, "add", "-A")
    workspace.git(w, "commit", "-q", "-m", "first")
    bare = tmp_path / "me-proj.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(w), str(bare)], check=True, capture_output=True)
    gh.repo("me/proj", bare)
    p = projectsetup.ensure(conn, "me/proj")
    run_inline(conn, p["setup_run"])
    projectsetup.tick(conn)
    return projects.get(conn, p["id"])


def test_a_repo_project_lists_check_clone_setup_and_open_prs(conn, capsys, tmp_path, gh_repo):
    p = ready_project(conn, gh_repo, tmp_path, {"Makefile": "check:\n\ttrue\n"})
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project, issue)"
                     " VALUES (?,?,?,?,?,?,?,?)", (gid, "Make it greet\n\nplease", "issue", "eki", "planned",
                                                   db.now(), p["id"], 7))
    it = selfwork.new_item(conn, gid, "say hello", "do it", ["app.py"], [], False)
    url = "https://github.com/me/proj/pull/3"
    selfwork._set(conn, it, state="proposed", branch=f"eki/{it}", pr=url, pr_state="open")
    assert main(["goal"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:5] == ["me/proj", "  check: make check (guessed: Makefile target check)",
                         f"  clone: {p['path']}", "  setup: ready",
                         "  path: GitHub me/proj — issues labelled eki in, PRs out"]
    assert f"  #7 Make it greet — goal {gid} planned" in lines
    assert f"  PR {url}  say hello" in lines and "no standing goals" not in lines[0]
    with db.tx(conn):
        conn.execute("UPDATE projects SET setup='fault', fault='install failed: lockfile broken' WHERE id=?",
                     (p["id"],))
    assert main(["goal"]) == 0
    assert "  setup: fault: install failed: lockfile broken" in capsys.readouterr().out


def test_a_retired_row_says_where_it_finishes(conn, capsys, proj, gh_repo):
    pid = projects.add(conn, proj)
    with db.tx(conn):
        conn.execute("UPDATE projects SET repo='me/proj', state='retired' WHERE id=?", (pid,))
    assert main(["goal"]) == 0
    assert f"  setup: retired — finishing in {proj}" in capsys.readouterr().out


def test_drop_owner_repo_removes_the_clone(conn, capsys, tmp_path, gh_repo):
    p = ready_project(conn, gh_repo, tmp_path, {"README.md": "# proj\n"})
    gh_repo.answer(["search", "issues"], "[]")
    assert (paths.projects() / "me" / "proj" / "README.md").exists()
    assert main(["goal", "drop", "me/proj"]) == 0
    assert capsys.readouterr().out.startswith("me/proj: dropped")
    assert not (paths.projects() / "me" / "proj").exists()
    assert projects.get(conn, p["id"])["state"] == "dropped"
    assert main(["goal"]) == 0 and "me/proj" not in capsys.readouterr().out
    assert main(["goal", "drop", "me/nothing"]) == 1
    assert "no project me/nothing" in capsys.readouterr().err


def test_the_local_path_line(conn, capsys, proj, fake_gh):
    added(capsys, proj)
    assert main(["goal"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "  path: local — merge by hand (origin isn't on GitHub)"
    assert fake_gh.calls() == []


def test_a_standing_goal_from_an_issue_shows_its_number(conn, capsys, proj, gh_repo):
    sid = standing.add(conn, str(proj), "keep it tidy", issue=12)
    assert main(["goal"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"{sid}  garden  on  #12 keep it tidy"
    assert lines[1] == "  path: GitHub me/proj — issues labelled eki in, PRs out"
