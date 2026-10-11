"""What each change on the head was for (eki/landed.py)."""
import pytest

from eki import db, landed, selfwork, store, workspace
from eki.workspace import git


def commit(where, name, text, msg):
    (where / name).write_text(text)
    git(where, "add", "-A")
    git(where, "commit", "-q", "-m", msg)
    return workspace.head(where)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "a.txt").write_text("one\ntwo\nthree\n")
    (r / "b.txt").write_text("other\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


def goal(conn, gid, text):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     (gid, text, "ask", "you", "planned", db.now()))


def item(conn, gid, title, spec, summary=None, **fields):
    with db.tx(conn):
        iid = selfwork.new_item(conn, gid, title, spec, ["a.txt"], [], False)
        selfwork._set(conn, iid, summary=summary, **fields)
    return iid


def mine(conn, repo, base):
    """The item whose rebase stopped: its own worktree from `base`."""
    goal(conn, "gm", "my goal")
    iid = item(conn, "gm", "My change", "my spec")
    wt = workspace.add(repo, iid, base=base, branch=f"eki/{iid}")
    with db.tx(conn):
        selfwork._set(conn, iid, worktree=str(wt), base=base)
    return selfwork.store_item(conn, iid)


def landed_item(conn, repo, n, text, gid="g1"):
    """An eki item that landed on main, touching a.txt."""
    sha = commit(repo, "a.txt", text, f"commit of item {n}")
    return item(conn, gid, f"Item {n}", f"spec {n}", f"summary {n}", rebased=sha, state="applied")


def test_two_landed_items_are_named_newest_first(conn, repo):
    base = workspace.head(repo)
    it = mine(conn, repo, base)
    goal(conn, "g1", "line one of the goal\nline two\nline three\nline four")
    landed_item(conn, repo, 1, "one\nfirst\nthree\n")
    landed_item(conn, repo, 2, "one\nsecond\nthree\n")
    head = workspace.head(repo)
    out = landed.intents(conn, it, head, ["a.txt"])
    assert "### Item 1" in out and "### Item 2" in out
    assert out.index("### Item 2") < out.index("### Item 1")
    for s in ("spec 1", "summary 1", "spec 2", "summary 2", "line one of the goal", "line three"):
        assert s in out
    assert "line four" not in out
    assert "My change" not in out


def test_several_commits_of_one_item_count_once(conn, repo):
    base = workspace.head(repo)
    it = mine(conn, repo, base)
    goal(conn, "g1", "a goal")
    first = commit(repo, "a.txt", "x\n", "part one")
    second = commit(repo, "a.txt", "y\n", "part two")
    item(conn, "g1", "Two parts", "spec", None, commit_sha=first, rebased=second)
    out = landed.intents(conn, it, second, ["a.txt"])
    assert out.count("### Two parts") == 1 and "(nothing said)" in out


def test_a_commit_on_other_files_is_not_listed(conn, repo):
    base = workspace.head(repo)
    it = mine(conn, repo, base)
    goal(conn, "g1", "a goal")
    sha = commit(repo, "b.txt", "changed\n", "only b")
    item(conn, "g1", "Only b", "spec b", "summary b", rebased=sha)
    assert landed.intents(conn, it, sha, ["a.txt"]) == ""


def test_a_persons_commit_falls_back_to_subject_and_body(conn, repo):
    base = workspace.head(repo)
    it = mine(conn, repo, base)
    (repo / "a.txt").write_text("by hand\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Fix line two by hand", "-m", "Because the old one was wrong.")
    head = workspace.head(repo)
    out = landed.intents(conn, it, head, ["a.txt"])
    assert "### Fix line two by hand" in out
    assert "Because the old one was wrong." in out
    assert "a person's own commit, not an eki item" in out and head[:12] in out


def test_trimming_and_the_limit(conn, repo):
    base = workspace.head(repo)
    it = mine(conn, repo, base)
    goal(conn, "g1", "a goal")
    long_sha = commit(repo, "a.txt", "long\n", "long")
    item(conn, "g1", "Long", "s" * 100, "short", rebased=long_sha)
    out = landed.intents(conn, it, long_sha, ["a.txt"], trim=20)
    assert "Spec: " + "s" * 20 + "…" in out and "s" * 21 not in out
    for n in (1, 2, 3):
        landed_item(conn, repo, n, f"v{n}\n")
    head = workspace.head(repo)
    out = landed.intents(conn, it, head, ["a.txt"], limit=2)
    assert "### Item 3" in out and "### Item 2" in out
    assert "### Item 1" not in out and "### Long" not in out
    assert out.splitlines()[-1] == "(2 more not shown)"


def test_the_item_itself_is_never_listed(conn, repo):
    base = workspace.head(repo)
    it = mine(conn, repo, base)
    sha = commit(repo, "a.txt", "mine on main\n", "my own commit")
    with db.tx(conn):
        selfwork._set(conn, it["id"], rebased=sha)
    it = selfwork.store_item(conn, it["id"])
    assert landed.intents(conn, it, sha, ["a.txt"]) == ""


def test_nothing_landed_or_git_failing_gives_nothing(conn, repo, tmp_path):
    base = workspace.head(repo)
    it = mine(conn, repo, base)
    assert landed.intents(conn, it, base, ["a.txt"]) == ""
    assert landed.intents(conn, it, "nosuchref", ["a.txt"]) == ""
    with db.tx(conn):
        selfwork._set(conn, it["id"], worktree=str(tmp_path / "gone"))
    gone = selfwork.store_item(conn, it["id"])
    commit(repo, "a.txt", "z\n", "z")
    assert landed.intents(conn, gone, workspace.head(repo), ["a.txt"]) == ""
