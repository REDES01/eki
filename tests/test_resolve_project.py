"""A resolved item of a project is rechecked with its project's check (eki/resolve.py)."""
import json

import pytest

from eki import db, projects, projectwork, rebase, resolve, selfwork, store, workspace
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


def test_a_project_check_is_the_recheck(conn, repo, tmp_path, monkeypatch):
    pid = projects.add(conn, repo, check="test -f a.txt")
    it, head = conflicted(conn, repo, pid)
    said = resolved(conn, it, head, tmp_path, monkeypatch)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "rechecking", (cur["error"], said)
    check = store.run(conn, cur["run_id"])
    assert check["provider"] == "command" and check["prompt"] != selfwork.CHECK
    assert check["prompt"] == projects.check_argv(projects.get(conn, pid))
    assert json.loads(check["prompt"])[2] == "test -f a.txt"
    run_inline(conn, check["id"])
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued" and cur["head"] == head, (cur["error"], said)
    assert any("back in the queue" in s for s in said)


def test_a_project_without_a_check_goes_straight_back(conn, repo, tmp_path, monkeypatch):
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
    assert cur["verdict"] == projectwork.not_judged(projects.get(conn, pid)) and cur["error"] is None
    assert said == [f"item {it['id']}: resolved; not judged; back in the queue"]
    assert len(store.thread_runs(conn, it["thread_id"])) == runs + 1     # the resolve run only
    assert resolve.tick(conn) == []


def test_eki_items_still_use_its_own_check(conn, repo, tmp_path, monkeypatch):
    it, head = conflicted(conn, repo, None)
    said = resolved(conn, it, head, tmp_path, monkeypatch)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "rechecking", (cur["error"], said)
    assert store.run(conn, cur["run_id"])["prompt"] == selfwork.CHECK
