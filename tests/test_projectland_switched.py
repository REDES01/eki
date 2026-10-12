"""Landing waits, with the reason, while the person's folder is on another branch."""
import pytest

from eki import projectland, workspace


def commit(where, files, msg):
    for name, text in files.items():
        (where / name).write_text(text)
    workspace.git(where, "add", "-A", "--", *files)
    workspace.git(where, "commit", "-q", "-m", msg)
    return workspace.git(where, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A person's folder on main, and an eki/x commit one step ahead of it."""
    r = tmp_path / "folder"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    commit(r, {"a.txt": "one\n"}, "first")
    wt = tmp_path / "wt"
    workspace.git(r, "worktree", "add", "-q", "-b", "eki/x", str(wt), "main")
    return r, commit(wt, {"a.txt": "two\n"}, "item")


def main(repo):
    return workspace.git(repo, "rev-parse", "refs/heads/main")


def test_another_branch_waits_then_lands_once_switched_back(repo):
    r, new = repo
    old = main(r)
    workspace.git(r, "switch", "-q", "-c", "other")
    why = "is on other, not main: eki lands on main once you switch back"
    assert projectland.land(r, "main", new) == why
    assert main(r) == old
    assert projectland.land(r, "main", new) == why          # a later tick: still waits
    workspace.git(r, "switch", "-q", "main")
    assert projectland.land(r, "main", new) is None
    assert main(r) == new
    assert (r / "a.txt").read_text() == "two\n"
    assert workspace.git(r, "status", "--porcelain") == ""


def test_a_detached_head_waits_with_its_own_wording(repo):
    r, new = repo
    old = main(r)
    workspace.git(r, "switch", "-q", "--detach")
    assert projectland.land(r, "main", new) == (
        "is on a detached HEAD, not main: eki lands on main once you switch back")
    assert main(r) == old
    assert (r / "a.txt").read_text() == "one\n"


def test_already_landed_is_none_even_while_switched(repo):
    r, new = repo
    assert projectland.land(r, "main", new) is None
    workspace.git(r, "switch", "-q", "-c", "other")
    assert projectland.land(r, "main", new) is None
