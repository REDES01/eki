import subprocess
from pathlib import Path

import pytest

from eki import db, engine, paths, store, workspace
from conftest import run_inline


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\n")
    (r / ".venv").mkdir()
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def test_a_worktree_on_its_own_branch_with_deps_linked(repo):
    wt = workspace.add(repo, "k1")
    assert wt == paths.work() / "k1"
    assert (wt / "a.txt").read_text() == "one\n"
    assert (wt / ".venv").is_symlink()
    assert workspace.git(wt, "branch", "--show-current") == "eki/k1"
    assert workspace.add(repo, "k1") == wt                   # already there: as it is


def test_two_worktrees_never_see_each_other(repo):
    a, b = workspace.add(repo, "a"), workspace.add(repo, "b")
    (a / "a.txt").write_text("from a\n")
    assert (b / "a.txt").read_text() == "one\n"
    assert workspace.changed(a, "HEAD") == ["a.txt"]                # the linked .venv never counts
    sha = workspace.commit_all(a, "a's change")
    assert sha and workspace.head(a) == sha
    assert workspace.commit_all(a, "again") is None
    assert (repo / "a.txt").read_text() == "one\n"            # the source repo is untouched


def test_remove_and_sweep(repo):
    workspace.add(repo, "old")
    assert workspace.remove(repo, "old", delete_branch=True)
    assert not (paths.work() / "old").exists()
    assert not workspace.git(repo, "branch", "--list", "eki/old")
    workspace.add(repo, "idle")
    assert workspace.sweep(repo, days=0) == ["idle"]


def test_a_command_is_a_run(conn, tmp_path):
    with db.tx(conn):
        tid = store.create_thread(conn, "cmd", str(tmp_path))
        rid = store.create_run(conn, tid, '["sh", "-c", "echo hi; pwd"]', provider="command")
    r = run_inline(conn, rid)
    assert r["state"] == "done"
    out = store.answer(conn, rid)
    assert "hi\n" in out and str(tmp_path.resolve()) in out


def test_a_failing_command_says_why(conn, tmp_path):
    with db.tx(conn):
        tid = store.create_thread(conn, "cmd", str(tmp_path))
        rid = store.create_run(conn, tid, "echo oops >&2; exit 3", provider="command")
    r = run_inline(conn, rid)
    assert r["state"] == "failed" and "oops" in r["error"]


def test_runs_in_one_folder_take_turns(conn, tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(engine, "spawn", lambda c, rid: spawned.append(rid))
    with db.tx(conn):
        a = store.create_run(conn, store.create_thread(conn, "t", str(tmp_path)), "a")
        store.create_run(conn, store.create_thread(conn, "u", str(tmp_path)), "b")
        c = store.create_run(conn, store.create_thread(conn, "v", None), "c")
    engine.spawn_ready(conn)
    assert spawned == [a, c]


def test_commit_all_with_an_ignored_dangling_link(repo, tmp_path):
    """A linked .venv that is ignored (or points nowhere) must not stop the commit."""
    wt = workspace.add(repo, "dl")
    (wt / ".venv").unlink()
    (wt / ".venv").symlink_to(tmp_path / "nowhere")
    with open(Path(repo) / ".git" / "info" / "exclude", "a") as f:
        f.write(".venv\n")
    (wt / "a.txt").write_text("changed\n")
    sha = workspace.commit_all(wt, "with a dangling ignored link")
    assert sha and ".venv" not in workspace.git(wt, "show", "--stat", "--format=", sha)
