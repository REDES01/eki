import pytest

from eki import projectland, workspace


def commit(where, files, msg):
    for name, text in files.items():
        p = where / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            p.unlink()
        else:
            p.write_text(text)
    workspace.git(where, "add", "-A", "-f", "--", *files)
    workspace.git(where, "commit", "-q", "-m", msg)
    return workspace.git(where, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A person's folder on master, with a log, an ignore rule and two source files."""
    r = tmp_path / "chapter1"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "master")
    commit(r, {".gitignore": "*.log\nbuild/\n", "src/x.js": "x1\n", "src/y.js": "y1\n",
               "notes.md": "n\n"}, "first")
    return r


def side(repo, files, base="master", name="eki/item"):
    """A commit on top of `base` made in a worktree of its own, as the queue makes them."""
    wt = repo.parent / f"wt-{name.replace('/', '-')}"
    workspace.git(repo, "worktree", "add", "-q", "-b", name, str(wt), base)
    return commit(wt, files, "item")


def tip(repo, branch="master"):
    return workspace.git(repo, "rev-parse", f"refs/heads/{branch}")


def test_checked_out_finds_the_main_worktree_and_others(repo):
    assert projectland.checked_out(repo, "master") == repo.resolve()
    side(repo, {"a": "a\n"})
    assert projectland.checked_out(repo, "eki/item").name == "wt-eki-item"
    workspace.git(repo, "branch", "-q", "idle")
    assert projectland.checked_out(repo, "idle") is None


def test_landing_with_the_branch_checked_out_moves_the_folder(repo):
    new = side(repo, {"src/x.js": "x2\n", "src/z.js": "z\n"})
    assert projectland.land(repo, "master", new) is None
    assert tip(repo) == new
    assert (repo / "src" / "x.js").read_text() == "x2\n" and (repo / "src" / "z.js").read_text() == "z\n"
    assert workspace.git(repo, "status", "--porcelain") == ""


def test_landing_with_the_branch_not_checked_out_moves_only_the_ref(repo):
    workspace.git(repo, "checkout", "-q", "-b", "other")
    new = side(repo, {"src/x.js": "x2\n"})
    assert projectland.land(repo, "master", new) is None
    assert tip(repo) == new
    assert workspace.git(repo, "symbolic-ref", "--short", "HEAD") == "other"
    assert (repo / "src" / "x.js").read_text() == "x1\n"


def test_a_dirty_file_the_change_touches_waits_then_lands(repo):
    old = tip(repo)
    new = side(repo, {"src/x.js": "x2\n"})
    (repo / "src" / "x.js").write_text("mine\n")
    assert projectland.blockers(repo, old, new) == ["src/x.js"]
    assert projectland.land(repo, "master", new) == "has local changes to src/x.js"
    assert tip(repo) == old and (repo / "src" / "x.js").read_text() == "mine\n"
    workspace.git(repo, "checkout", "--", "src/x.js")       # the person puts it back
    assert projectland.land(repo, "master", new) is None
    assert tip(repo) == new and (repo / "src" / "x.js").read_text() == "x2\n"


def test_a_deleted_touched_file_blocks(repo):
    new = side(repo, {"src/x.js": "x2\n"})
    (repo / "src" / "x.js").unlink()
    assert projectland.land(repo, "master", new) == "has local changes to src/x.js"


def test_a_staged_change_to_a_touched_file_blocks(repo):
    old = tip(repo)
    new = side(repo, {"src/x.js": "x2\n"})
    (repo / "src" / "x.js").write_text("mine\n")
    workspace.git(repo, "add", "src/x.js")
    assert projectland.land(repo, "master", new) == "has local changes to src/x.js"
    assert tip(repo) == old
    assert workspace.git(repo, "diff", "--cached", "--name-only") == "src/x.js"


def test_a_dirty_file_the_change_does_not_touch_does_not_block(repo):
    new = side(repo, {"src/x.js": "x2\n"})
    (repo / "src" / "y.js").write_text("mine\n")
    (repo / "notes.md").write_text("staged\n")
    workspace.git(repo, "add", "notes.md")
    assert projectland.land(repo, "master", new) is None
    assert tip(repo) == new and (repo / "src" / "x.js").read_text() == "x2\n"
    assert (repo / "src" / "y.js").read_text() == "mine\n"
    assert workspace.git(repo, "diff", "--cached", "--name-only") == "notes.md"


def test_untracked_and_ignored_files_are_untouched(repo):
    new = side(repo, {"src/x.js": "x2\n"})
    (repo / "run.log").write_text("log\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.bin").write_text("b\n")
    (repo / "scratch.txt").write_text("s\n")
    assert projectland.land(repo, "master", new) is None
    assert tip(repo) == new
    assert (repo / "run.log").read_text() == "log\n"
    assert (repo / "build" / "out.bin").read_text() == "b\n"
    assert (repo / "scratch.txt").read_text() == "s\n"


def test_an_untracked_or_ignored_file_where_the_change_adds_one_blocks(repo):
    old = tip(repo)
    new = side(repo, {"scratch.txt": "theirs\n", "debug.log": "theirs\n", "src/x.js": "x2\n"})
    (repo / "scratch.txt").write_text("mine\n")
    (repo / "debug.log").write_text("mine\n")
    (repo / "other.txt").write_text("elsewhere\n")
    assert projectland.blockers(repo, old, new) == ["debug.log", "scratch.txt"]
    assert projectland.land(repo, "master", new) == "has local changes to debug.log, scratch.txt"
    assert tip(repo) == old and (repo / "scratch.txt").read_text() == "mine\n"


def test_a_non_fast_forward_raises(repo):
    new = side(repo, {"src/x.js": "x2\n"})
    commit(repo, {"src/y.js": "y2\n"}, "the person's own")
    before = tip(repo)
    with pytest.raises(projectland.NotFastForward):
        projectland.land(repo, "master", new)
    assert tip(repo) == before


def test_landing_twice_is_a_no_op(repo):
    new = side(repo, {"src/x.js": "x2\n"})
    assert projectland.land(repo, "master", new) is None
    assert projectland.land(repo, "master", new) is None
    assert tip(repo) == new
    old = workspace.git(repo, "rev-parse", "HEAD~1")
    assert projectland.land(repo, "master", old) is None      # an older commit is already contained
    assert tip(repo) == new
