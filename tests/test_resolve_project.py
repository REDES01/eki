"""A resolved item of a project goes back to its queue for gate 2, no gate 1 (eki/resolve.py)."""
import pytest

from eki import db, projects, rebase, resolve, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline


def says(tmp_path, monkeypatch, text):
    p = tmp_path / "says.txt"
    p.write_text(text)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))


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
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


def conflicted(conn, repo, project):
    """An item of goal g1 (on `project`, or eki itself) stopped mid-rebase on a.txt."""
    base = workspace.head(repo)
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project) "
                     "VALUES (?,?,?,?,?,?,?)",
                     ("g1", "make a.txt better", "ask", "you", "planned", db.now(), project))
        iid = selfwork.new_item(conn, "g1", "Change line two", "put mine on line two", ["a.txt"], [], False)
    wt = workspace.add(repo, iid, base=base, branch=f"eki/{iid}")
    sha = commit(wt, "a.txt", "one\nmine\nthree\n", "Change line two")
    with db.tx(conn):
        tid = store.create_thread(conn, "Change line two", str(wt))
        selfwork._set(conn, iid, worktree=str(wt), branch=f"eki/{iid}", base=base, thread_id=tid,
                      commit_sha=sha, summary="line two says mine", state="queued")
    head = commit(repo, "a.txt", "one\ntheirs\nthree\n", "the head's line two")
    assert rebase.onto(wt, head) == ["a.txt"]
    return selfwork.store_item(conn, iid), head


def resolved(conn, it, head, tmp_path, monkeypatch):
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "SUMMARY: kept both.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    return resolve.tick(conn)


def test_a_project_item_goes_back_without_a_gate_one(conn, repo, tmp_path, monkeypatch):
    pid = projects.add(conn, repo, check="test -f a.txt")
    it, head = conflicted(conn, repo, pid)
    runs = len(store.thread_runs(conn, it["thread_id"]))
    said = resolved(conn, it, head, tmp_path, monkeypatch)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued" and cur["queued_at"] and cur["head"] == head, (cur["error"], said)
    assert cur["resolved_on"] == head and cur["gate2"] is None and cur["gate2_run"] is None
    assert len(store.thread_runs(conn, it["thread_id"])) == runs + 1     # the resolve run only
    assert said == [f"item {it['id']}: resolved onto {head[:12]}; back in the queue — gate 2 judges it"]


def test_a_project_without_a_check_goes_back_the_same_way(conn, repo, tmp_path, monkeypatch):
    pid = projects.add(conn, repo)
    assert projects.check_argv(projects.get(conn, pid)) is None
    it, head = conflicted(conn, repo, pid)
    runs = len(store.thread_runs(conn, it["thread_id"]))
    said = resolved(conn, it, head, tmp_path, monkeypatch)
    cur = selfwork.store_item(conn, it["id"])
    wt = cur["worktree"]
    assert cur["state"] == "queued" and cur["queued_at"], (cur["error"], said)
    assert not rebase.in_progress(wt) and git(wt, "rev-parse", "HEAD^") == head
    sha = workspace.head(wt)
    assert cur["commit_sha"] == sha and cur["rebased"] == sha and cur["head"] == head
    assert cur["error"] is None and cur["gate2"] is None         # the project's queue decides (not judged)
    assert said == [f"item {it['id']}: resolved onto {head[:12]}; back in the queue — gate 2 judges it"]
    assert len(store.thread_runs(conn, it["thread_id"])) == runs + 1     # the resolve run only
    assert resolve.tick(conn) == []


def test_eki_items_go_back_the_same_way(conn, repo, tmp_path, monkeypatch):
    it, head = conflicted(conn, repo, None)
    said = resolved(conn, it, head, tmp_path, monkeypatch)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued", (cur["error"], said)
    assert all(r["provider"] != "command" for r in store.thread_runs(conn, it["thread_id"]))
