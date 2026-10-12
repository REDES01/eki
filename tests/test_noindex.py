"""eki's churn folders stay out of Spotlight: worktrees under work.noindex, markers, the migration."""
import os

import pytest

from eki import builds, db, noindex, paths, rebase, workspace
from eki.workspace import git


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.txt").write_text("one\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


def test_new_worktrees_and_gate_trees_land_under_work_noindex(home, repo):
    wt = workspace.add(repo, "it1")
    assert wt == home / "work.noindex" / "it1" and (wt / "a.txt").exists()
    t = rebase.gate_tree(repo, "gate2-it1", workspace.head(repo))
    assert t == home / "work.noindex" / "gate2-it1" and workspace.head(t) == workspace.head(repo)
    assert not (home / "work").exists()


def test_scratch_and_builds_are_marked_never_to_index():
    assert (paths.scratch() / ".metadata_never_index").is_file()
    assert (builds.root() / ".metadata_never_index").is_file()
    assert builds.root().name == "builds"


def old_home(home, conn, repo):
    """A home as an older eki left it: a real work/ with a worktree, marks, an item and a thread."""
    old = home / "work"
    old.mkdir()
    git(repo, "worktree", "add", "-q", "-b", "eki/it1", str(old / "it1"))
    (old / ".marks").mkdir()
    (old / ".marks" / "it1").write_text("")
    (old / ".disk.json").write_text("{}")
    conn.execute("INSERT INTO goals(id, text, state, created_at) VALUES ('g1','t','planned',?)", (db.now(),))
    conn.execute("INSERT INTO items(id, goal_id, title, state, worktree, branch, created_at, updated_at) "
                 "VALUES ('it1','g1','t','building',?,'eki/it1',?,?)", (str(old / "it1"), db.now(), db.now()))
    conn.execute("INSERT INTO threads(id, title, cwd, created_at) VALUES ('t1','',?,?)",
                 (str(old / "it1" / "a.txt"), db.now()))
    return old


def test_migrate_moves_the_worktrees_and_their_stored_paths(home, conn, repo):
    old = old_home(home, conn, repo)
    (home / "self").mkdir()
    lines = noindex.migrate(conn)
    assert lines
    new = home / "work.noindex"
    assert (new / "it1" / "a.txt").exists() and (new / ".marks" / "it1").exists() and (new / ".disk.json").exists()
    assert old.is_symlink() and os.path.realpath(old) == os.path.realpath(new)
    assert conn.execute("SELECT worktree FROM items WHERE id='it1'").fetchone()[0] == str(new / "it1")
    assert conn.execute("SELECT cwd FROM threads WHERE id='t1'").fetchone()[0] == str(new / "it1" / "a.txt")
    git(new / "it1", "status")
    listed = git(repo, "worktree", "list", "--porcelain")
    assert f"worktree {os.path.realpath(new / 'it1')}" in listed
    assert (home / "self" / ".metadata_never_index").is_file()
    assert noindex.migrate(conn) == []


def test_nothing_to_do_says_nothing(home, conn):
    assert noindex.migrate(conn) == []
    assert not (home / "work").exists()


def test_a_run_stopped_halfway_finishes_next_time(home, conn, repo):
    old = old_home(home, conn, repo)
    new = paths.work()
    os.rename(old / ".marks", new / ".marks")           # a killed pass: one moved, no symlink left yet
    (old / "plan-g1").mkdir()
    os.rename(old / "plan-g1", new / "plan-g1")
    os.symlink(new / "plan-g1", old / "plan-g1")        # one moved with its symlink
    assert not old.is_symlink()
    noindex.migrate(conn)
    assert old.is_symlink()
    assert {p.name for p in new.iterdir()} >= {"it1", ".marks", "plan-g1", ".disk.json"}
    assert conn.execute("SELECT worktree FROM items WHERE id='it1'").fetchone()[0] == str(new / "it1")
    git(new / "it1", "status")
    assert noindex.migrate(conn) == []


def test_a_name_taken_in_work_noindex_stays_and_is_said(home, conn, repo):
    old = old_home(home, conn, repo)
    (paths.work() / ".disk.json").write_text("{}")
    lines = noindex.migrate(conn)
    assert any(".disk.json" in line and "stays" in line for line in lines)
    assert (old / ".disk.json").is_file() and not old.is_symlink()
    assert (old / "it1").is_symlink() and (old / "it1" / "a.txt").exists()   # the old path still works
    assert conn.execute("SELECT worktree FROM items WHERE id='it1'").fetchone()[0] == str(paths.work() / "it1")


def snapshot(root):
    return sorted((str(p.relative_to(root)), p.lstat().st_mtime) for p in root.rglob("*"))


def test_the_persons_project_folders_are_left_as_they_are(home, conn, repo, tmp_path):
    proj = tmp_path / "user" / "myapp"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "main.py").write_text("print(1)\n")
    os.utime(proj / "src" / "main.py", (1_000_000, 1_000_000))
    before = snapshot(tmp_path / "user")
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at) VALUES ('p1','myapp',?,'main',?)",
                 (str(proj), db.now()))
    conn.execute("INSERT INTO threads(id, title, cwd, created_at) VALUES ('t2','',?,?)", (str(proj / "src"), db.now()))
    old_home(home, conn, repo)
    noindex.migrate(conn)
    assert snapshot(tmp_path / "user") == before
    assert not list((tmp_path / "user").rglob(".metadata_never_index"))
    assert conn.execute("SELECT cwd FROM threads WHERE id='t2'").fetchone()[0] == str(proj / "src")
    assert conn.execute("SELECT path FROM projects WHERE id='p1'").fetchone()[0] == str(proj)


def test_a_pass_killed_before_its_repair_is_repaired_next_time(home, conn, repo):
    old = old_home(home, conn, repo)
    new = paths.work()
    os.rename(old / "it1", new / "it1")                 # moved, then killed: no symlink, no repair
    os.symlink(new / "it1", old / "it1")
    assert noindex._stale(new / "it1", new)
    noindex.migrate(conn)
    assert not noindex._stale(new / "it1", new)
    assert old.is_symlink()
    assert f"worktree {os.path.realpath(new / 'it1')}" in git(repo, "worktree", "list", "--porcelain")
