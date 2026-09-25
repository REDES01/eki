import pytest

from eki import paths, rebase, workspace
from eki.workspace import git


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.txt").write_text("one\ntwo\nthree\n")
    (r / "b.txt").write_text("b\n")
    (r / ".gitignore").write_text(".venv/\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


def commit(where, name, text, msg="change"):
    (where / name).write_text(text)
    git(where, "add", "-A")
    git(where, "commit", "-q", "-m", msg)
    return workspace.head(where)


def item(repo, key="it"):
    return workspace.add(repo, key, base="main")


def ancestors(where):
    return git(where, "rev-list", "HEAD").splitlines()


def test_a_branch_on_another_file_rebases_cleanly(repo):
    wt = item(repo)
    commit(wt, "b.txt", "b changed\n")
    head = commit(repo, "a.txt", "one\nTWO\nthree\n")
    assert rebase.onto(wt, head) == []
    assert not rebase.in_progress(wt)
    assert git(wt, "rev-parse", "HEAD^") == head
    assert (wt / "a.txt").read_text() == "one\nTWO\nthree\n"
    assert rebase.onto(wt, head) == []                       # already on head: a no-op
    assert git(wt, "rev-parse", "HEAD^") == head


def test_a_conflict_stops_resolves_and_finishes(repo):
    wt = item(repo)
    commit(wt, "a.txt", "one\nmine\nthree\n")
    head = commit(repo, "a.txt", "one\ntheirs\nthree\n")
    assert rebase.onto(wt, head) == ["a.txt"]
    assert rebase.in_progress(wt)
    assert rebase.conflicted(wt) == ["a.txt"]
    assert rebase.markers(wt, head) == ["a.txt"]
    (wt / "a.txt").write_text("one\nmine and theirs\nthree\n")
    assert rebase.markers(wt, head) == []
    assert rebase.proceed(wt) == []
    assert not rebase.in_progress(wt)
    assert head in ancestors(wt)
    assert (wt / "a.txt").read_text() == "one\nmine and theirs\nthree\n"


def test_onto_after_a_kill_mid_rebase_aborts_and_retries(repo):
    wt = item(repo)
    commit(wt, "a.txt", "one\nmine\nthree\n")
    head = commit(repo, "a.txt", "one\ntheirs\nthree\n")
    assert rebase.onto(wt, head) == ["a.txt"]
    assert rebase.onto(wt, head) == ["a.txt"]                # left in progress: aborted, tried again
    assert rebase.in_progress(wt)
    rebase.abort(wt)
    assert not rebase.in_progress(wt)
    rebase.abort(wt)                                          # nothing in progress: nothing
    assert (wt / "a.txt").read_text() == "one\nmine\nthree\n"


def test_two_commits_that_conflict_stop_twice(repo):
    wt = item(repo)
    commit(wt, "a.txt", "one\nmine\nthree\n", "first mine")
    commit(wt, "b.txt", "b mine\n", "second mine")
    commit(repo, "a.txt", "one\ntheirs\nthree\n")
    head = commit(repo, "b.txt", "b theirs\n")
    assert rebase.onto(wt, head) == ["a.txt"]
    (wt / "a.txt").write_text("one\nboth\nthree\n")
    assert rebase.proceed(wt) == ["b.txt"]
    assert rebase.markers(wt, head) == ["b.txt"]
    (wt / "b.txt").write_text("b both\n")
    assert rebase.proceed(wt) == []
    assert not rebase.in_progress(wt)
    assert head in ancestors(wt)
    assert git(wt, "log", "--format=%s", f"{head}..HEAD").splitlines() == ["second mine", "first mine"]


def test_a_step_that_became_empty_is_skipped(repo):
    wt = item(repo)
    commit(wt, "a.txt", "one\nmine\nthree\n", "mine")
    head = commit(repo, "a.txt", "one\ntheirs\nthree\n")
    assert rebase.onto(wt, head) == ["a.txt"]
    (wt / "a.txt").write_text("one\ntheirs\nthree\n")        # resolved to exactly the head
    assert rebase.proceed(wt) == []
    assert not rebase.in_progress(wt)
    assert workspace.head(wt) == head


def test_markers_need_a_real_marker_line(repo):
    wt = item(repo)
    base = workspace.head(wt)
    (wt / "ok.md").write_text("Title\n=======\n")            # a heading underline, no opening marker
    (wt / "bad.txt").write_text("x\n<<<<<<< HEAD\na\n=======\nb\n>>>>>>> other\n")
    assert rebase.markers(wt, base) == ["bad.txt"]


def test_gate_tree_is_detached_fresh_and_links_the_venv(repo):
    (repo / ".venv").mkdir()
    (repo / ".venv" / "marker").write_text("v\n")
    sha = workspace.head(repo)
    t = rebase.gate_tree(repo, "gate-x", sha)
    assert t == paths.work() / "gate-x"
    assert workspace.head(t) == sha
    assert git(t, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"   # detached
    assert (t / ".venv").is_symlink() and (t / ".venv" / "marker").exists()
    (t / "junk.txt").write_text("left over\n")
    later = commit(repo, "b.txt", "later\n")
    t2 = rebase.gate_tree(repo, "gate-x", later)
    assert t2 == t and workspace.head(t2) == later
    assert not (t2 / "junk.txt").exists()
    assert not git(t2, "status", "--porcelain", "--", ".", *workspace.NOT_LINKED)
    assert workspace.remove(repo, "gate-x")
    assert not t.exists()
