"""integration.sync when main and origin/main have both moved: main is rebased
onto origin (a conflict leaves it as it was), and what eki landed follows."""
import json

import pytest

from eki import db, integration, paths, workspace
from tests.test_integration import commit, on_branch, origin, src  # noqa: F401  (fixtures)


@pytest.fixture
def someone(tmp_path, origin):  # noqa: F811
    other = tmp_path / "someone"
    workspace.git(tmp_path, "clone", "-q", str(origin), str(other))
    return other


def land(fname, text="eki\n"):
    """eki lands a commit on integration main (a fast-forward, as the queue does)."""
    r = integration.repo()
    wt = paths.home() / "wt"
    workspace.git(r, "worktree", "add", "-q", "-b", f"land-{fname}", str(wt), "main")
    sha = commit(wt, fname, text)
    workspace.git(r, "worktree", "remove", "--force", str(wt))
    integration.fast_forward(sha)
    return sha


def push_from(where, fname, text="theirs\n"):
    sha = commit(where, fname, text)
    workspace.git(where, "push", "-q", "origin", "main")
    return sha


def add_item(conn, iid, sha, state="landed"):
    now = db.now()
    conn.execute("INSERT INTO goals(id, text, created_at) VALUES (?,?,?)", (f"g-{iid}", "g", now))
    conn.execute("INSERT INTO items(id, goal_id, title, state, commit_sha, rebased, created_at, updated_at)"
                 " VALUES (?,?,?,?,?,?,?,?)", (iid, f"g-{iid}", "t", state, sha, sha, now, now))


def test_diverged_main_is_rebased_onto_origin_and_pushes(src, origin, someone):  # noqa: F811
    mine = land("mine.py")
    theirs = push_from(someone, "theirs.py")
    new = integration.sync()
    r = integration.repo()
    assert new != mine and workspace.git(r, "rev-parse", "main^") == theirs
    assert workspace.git(r, "log", "-1", "--format=%s", "main") == "add mine.py"
    assert (r / "mine.py").exists() and (r / "theirs.py").exists()
    assert workspace.git(r, "status", "--porcelain") == ""
    assert not (paths.home() / "self" / "sync-rebase").exists()
    assert integration.push() is True
    assert workspace.git(origin, "rev-parse", "main") == new
    assert integration.sync() == new                  # nothing more to do


def test_a_conflict_leaves_main_as_it_was_and_is_a_fault(src, origin, someone):  # noqa: F811
    mine = land("a.py", "eki's a\n")
    push_from(someone, "a.py", "their a\n")
    assert integration.sync() == mine
    r = integration.repo()
    assert workspace.git(r, "status", "--porcelain") == ""
    assert not (r / ".git" / "rebase-merge").exists() and not (r / ".git" / "rebase-apply").exists()
    assert not (paths.home() / "self" / "sync-rebase").exists()
    assert "sync-rebase" not in workspace.git(r, "worktree", "list")
    conn = db.connect()
    rows = conn.execute("SELECT data FROM journal WHERE kind='fault'").fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0]["data"])
    assert data["exc"] == "RebaseConflict" and data["ours"] is False and data["where"] == "integration.sync"
    assert data["traceback"] and len(data["traceback"]) <= 4000


def test_carries_a_rewritten_commit_but_not_a_foreign_one(src, origin, someone, tmp_path):  # noqa: F811
    mine = land("mine.py")
    push_from(someone, "theirs.py")
    integration.sync()
    assert not integration.contains(mine) and integration.carries(mine)
    assert integration.carries(integration.main())
    foreign = on_branch("foreign", "foreign.py")
    assert not integration.carries(foreign)
    assert not integration.carries("f" * 40) and not integration.carries("")


def test_a_landed_items_rebased_follows_the_rewrite(src, origin, someone):  # noqa: F811
    mine = land("mine.py")
    conn = db.connect()
    add_item(conn, "i1", mine, "landed")
    add_item(conn, "i2", mine, "live")
    add_item(conn, "i3", mine, "queued")
    push_from(someone, "theirs.py")
    new = integration.sync()
    got = {r["id"]: r["rebased"] for r in conn.execute("SELECT id, rebased FROM items")}
    assert got == {"i1": new, "i2": new, "i3": mine}
