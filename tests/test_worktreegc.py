import json
import os
import time

import pytest

from eki import db, paths, store, workspace, worktreegc

DAY = 86400.0


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\n")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def goal(conn, gid="g1", state="planned"):
    conn.execute("INSERT INTO goals(id, text, state, created_at) VALUES (?,?,?,?)", (gid, "t", state, db.now()))


def item(conn, repo, iid, state, *, updated=None, **cols):
    wt = workspace.add(repo, iid)
    fields = dict(id=iid, goal_id="g1", title=iid, state=state, worktree=str(wt), branch=f"eki/{iid}",
                  created_at=db.now(), updated_at=updated or db.now(), **cols)
    conn.execute(f"INSERT INTO items({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
                 list(fields.values()))
    return wt


def branches(repo):
    return workspace.git(repo, "branch", "--list", "eki/*")


def worktrees(repo):
    return workspace.git(repo, "worktree", "list", "--porcelain")


def test_finished_items_go_with_their_branch(conn, repo):
    goal(conn)
    for iid, state in (("i-live", "live"), ("i-applied", "applied"), ("i-dropped", "dropped")):
        item(conn, repo, iid, state)
    said = worktreegc.tick(conn)
    assert sorted(said) == ["worktree i-applied: removed (item applied)",
                            "worktree i-dropped: removed (item dropped)",
                            "worktree i-live: removed (item live)"]
    for iid in ("i-live", "i-applied", "i-dropped"):
        assert not (paths.work() / iid).exists()
        assert iid not in worktrees(repo)
        assert conn.execute("SELECT worktree FROM items WHERE id=?", (iid,)).fetchone()[0]   # row kept as is
    assert branches(repo) == ""


def test_an_open_pr_keeps_its_branch(conn, repo):
    goal(conn)
    item(conn, repo, "i-pr", "live", pr="https://github.com/o/r/pull/1", pr_state="open")
    assert worktreegc.tick(conn) == ["worktree i-pr: removed (item live)"]
    assert "eki/i-pr" in branches(repo)


def test_landed_goes_once_its_commit_is_in_main(conn, repo):
    goal(conn)
    wt = item(conn, repo, "i-land", "landed")
    (wt / "b.txt").write_text("b\n")
    sha = workspace.commit_all(wt, "change")
    conn.execute("UPDATE items SET commit_sha=? WHERE id='i-land'", (sha,))
    assert worktreegc.tick(conn) == []                              # not in main yet
    workspace.git(repo, "merge", "-q", "--ff-only", sha)
    assert worktreegc.tick(conn) == ["worktree i-land: removed (item landed)"]


def test_busy_items_and_active_runs_are_kept(conn, repo):
    goal(conn)
    item(conn, repo, "i-build", "building")
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "p")                          # queued: active
    item(conn, repo, "i-run", "dropped", review_run=rid)
    wt = item(conn, repo, "i-cwd", "live")
    store.create_run(conn, store.create_thread(conn, "t2", str(wt)), "p")
    assert worktreegc.tick(conn) == []
    assert all((paths.work() / k).exists() for k in ("i-build", "i-run", "i-cwd"))


def test_a_failed_item_is_kept_two_days_with_its_branch(conn, repo):
    goal(conn)
    item(conn, repo, "i-unfit", "unfit", updated=db.now() - 1 * DAY)
    assert worktreegc.tick(conn) == []
    conn.execute("UPDATE items SET updated_at=? WHERE id='i-unfit'", (db.now() - 3 * DAY,))
    assert worktreegc.tick(conn) == ["worktree i-unfit: removed (item unfit 2+ days ago)"]
    assert "eki/i-unfit" in branches(repo)


def test_a_planned_goals_plan_tree_goes(conn, repo):
    goal(conn, "g1", "planned")
    goal(conn, "g2", "planning")
    workspace.add(repo, "plan-g1")
    workspace.add(repo, "plan-g2")
    assert worktreegc.tick(conn) == ["worktree plan-g1: removed (goal planned)"]
    assert (paths.work() / "plan-g2").exists()
    assert "eki/plan-g1" not in branches(repo) and "eki/plan-g2" in branches(repo)


def test_an_unknown_folder_goes_after_two_days(conn):
    old = paths.work() / "stray"
    old.mkdir()
    (paths.work() / "fresh").mkdir()
    assert worktreegc.tick(conn) == []
    os.utime(old, (time.time() - 3 * DAY,) * 2)
    assert worktreegc.tick(conn) == ["worktree stray: removed (no row knows it, untouched 3 days)"]
    assert (paths.work() / "fresh").exists()


def test_a_broken_git_folder_is_rmtreed(conn, repo):
    goal(conn)
    wt = item(conn, repo, "i-broken", "dropped")
    (wt / ".git").write_text("gitdir: /nowhere\n")
    assert worktreegc.tick(conn) == ["worktree i-broken: removed (item dropped)"]
    assert not wt.exists()


def test_at_most_five_a_pass_oldest_first(conn, repo):
    goal(conn)
    for n in range(7):
        wt = item(conn, repo, f"i{n}", "dropped")
        os.utime(wt, (time.time() - (10 - n) * 60,) * 2)
    said = worktreegc.tick(conn)
    assert [s.split()[1] for s in said] == ["i0:", "i1:", "i2:", "i3:", "i4:"]
    assert len(worktreegc.tick(conn)) == 2
    assert worktreegc.tick(conn) == []


def test_a_restart_mid_cleanup_is_finished_by_the_next_pass(conn, repo, monkeypatch):
    goal(conn)
    wt = item(conn, repo, "i-half", "dropped")
    real = workspace.remove

    def killed(where, key, **kw):
        import shutil
        shutil.rmtree(paths.work() / key)                           # gone from disk, not pruned
        raise KeyboardInterrupt("engine killed")

    monkeypatch.setattr(workspace, "remove", killed)
    with pytest.raises(KeyboardInterrupt):
        worktreegc.tick(conn)
    assert not wt.exists() and "i-half" in worktrees(repo) and "eki/i-half" in branches(repo)
    monkeypatch.setattr(workspace, "remove", real)
    assert worktreegc.tick(conn) == ["worktree i-half: removed (item dropped)"]
    assert "i-half" not in worktrees(repo) and branches(repo) == ""
    assert not list((paths.work() / ".gc").glob("*.json"))
    assert worktreegc.tick(conn) == []


def test_disk_line(conn, repo):
    assert worktreegc.disk_line() == "worktrees: not measured yet"
    goal(conn)
    item(conn, repo, "i-keep", "building")
    worktreegc.tick(conn)
    worktreegc._thread[0].join(10)
    rec = json.loads((paths.work() / ".disk.json").read_text())
    assert rec["count"] == 1 and rec["bytes"] > 0
    line = worktreegc.disk_line()
    assert line.startswith("worktrees: 1 folders, ") and line.endswith("on disk (measured just now)")
    worktreegc.tick(conn)                                           # measured once per 10 minutes
    assert worktreegc._thread[0] is None or not worktreegc._thread[0].is_alive()
