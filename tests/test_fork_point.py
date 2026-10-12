"""An item's touched files are its own, even when the agent rebased its branch
onto a newer main during the build turn (the commits that landed meanwhile are
not the item's, and must not lock it on a file it never changed)."""
from eki import selfwork, workspace
from eki.workspace import git


def _commit(where, name, text, msg):
    (where / name).write_text(text)
    git(where, "add", "-A")
    git(where, "commit", "-q", "-m", msg)
    return workspace.head(where)


def test_a_rebased_branch_counts_only_its_own_files(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    base = _commit(r, "a.txt", "one\n", "first")
    git(r, "checkout", "-q", "-b", "self/x")
    _commit(r, "mine.txt", "mine\n", "the item's change")
    git(r, "checkout", "-q", "main")
    (r / "bin").mkdir()
    _commit(r, "bin/check", "#!/bin/sh\n", "someone else's change, landed meanwhile")
    git(r, "checkout", "-q", "self/x")
    git(r, "rebase", "-q", "main")                             # what the agent did in its turn
    it = {"base": base, "worktree": str(r)}
    assert workspace.changed(r, base) == ["bin/check", "mine.txt"]    # the old way: not its own
    assert workspace.changed(r, selfwork.fork_point(it, None)) == ["mine.txt"]


def test_a_branch_not_rebased_keeps_its_base(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    base = _commit(r, "a.txt", "one\n", "first")
    git(r, "checkout", "-q", "-b", "self/x")
    _commit(r, "mine.txt", "mine\n", "the item's change")
    assert selfwork.fork_point({"base": base, "worktree": str(r)}, None) == base
    assert selfwork.fork_point({"base": base, "worktree": str(r)}, {"branch": "no-such"}) == base
