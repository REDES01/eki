from pathlib import Path

import pytest

from eki import db, rebase, resolve, selfbrief, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "src"
    (r / "bin").mkdir(parents=True)
    (r / "a.txt").write_text("one\ntwo\nthree\n")
    (r / "bin" / "check").write_text("#!/bin/sh\nexit ${EKI_TEST_CHECK_EXIT:-0}\n")
    (r / "bin" / "check").chmod(0o755)
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


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
def conflicted(conn, repo):
    """An item built on main, stopped mid-rebase onto a head that changed the same line."""
    base = workspace.head(repo)
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     ("g1", "make a.txt better", "ask", "you", "planned", db.now()))
        iid = selfwork.new_item(conn, "g1", "Change line two", "put mine on line two", ["a.txt"], [], False)
    wt = workspace.add(repo, iid, base=base, branch=f"self/{iid}")
    sha = commit(wt, "a.txt", "one\nmine\nthree\n", "self: Change line two")
    with db.tx(conn):
        tid = store.create_thread(conn, "self: Change line two", str(wt))
        selfwork._set(conn, iid, worktree=str(wt), branch=f"self/{iid}", base=base, thread_id=tid,
                      commit_sha=sha, summary="line two says mine", state="queued")
    head = commit(repo, "a.txt", "one\ntheirs\nthree\n", "the head's line two")
    assert rebase.onto(wt, head) == ["a.txt"]
    return selfwork.store_item(conn, iid), head


def test_start_makes_a_resolve_run(conn, conflicted):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    run = store.run(conn, rid)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "resolving" and cur["run_id"] == rid and cur["head"] == head
    assert cur["gate2_run"] is None and cur["rebased"] is None
    assert run["thread_id"] == it["thread_id"] and run["row"] == "code" and run["priority"] == "now"
    assert "a.txt" in run["prompt"] and head[:12] in run["prompt"]
    assert "the head's line two" in run["prompt"] and "+theirs" in run["prompt"]
    assert "line two says mine" in run["prompt"] and "make a.txt better" in run["prompt"]
    assert resolve.resolve_runs(conn, cur) == 1
    assert resolve.tick(conn) == []                                  # the run hasn't ended


def test_a_clean_resolution_is_checked_and_requeued(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "SUMMARY: kept both.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "rechecking", (cur["error"], said)
    wt = cur["worktree"]
    assert not rebase.in_progress(wt) and cur["commit_sha"] == workspace.head(wt)
    assert git(wt, "rev-parse", "HEAD^") == head
    check = store.run(conn, cur["run_id"])
    assert check["provider"] == "command" and check["prompt"] == selfwork.CHECK
    assert resolve.tick(conn) == []
    run_inline(conn, check["id"])
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued" and cur["queued_at"] and cur["head"] is None and cur["rebased"] is None
    assert any("back in the queue" in s for s in said)
    assert resolve.tick(conn) == []                                  # moved on once


def test_markers_left_are_refused(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "SUMMARY: sure.\nITEM: done\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and "a.txt" in cur["error"] and "marker" in cur["error"]
    wt = Path(cur["worktree"])
    assert not rebase.in_progress(wt)
    assert workspace.head(wt) == it["commit_sha"] and (wt / "a.txt").read_text() == "one\nmine\nthree\n"


def test_a_person_is_asked_for(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: person the two intents contradict\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and "contradict" in cur["error"]
    assert not rebase.in_progress(cur["worktree"])


def test_a_red_gate_one_is_unfit(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "rechecking"
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    run_inline(conn, cur["run_id"])
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "unfit" and "checks" in cur["error"]


def test_a_second_stop_gets_another_run_and_three_are_the_most(conn, repo, conflicted, tmp_path,
                                                               monkeypatch):
    it, head = conflicted
    wt = Path(it["worktree"])
    rebase.abort(wt)
    commit(wt, "a.txt", "one\nmine again\nthree\n", "second")       # a second commit on the same line
    with db.tx(conn):
        selfwork._set(conn, it["id"], commit_sha=workspace.head(wt))
    it = selfwork.store_item(conn, it["id"])
    assert rebase.onto(wt, head) == ["a.txt"]
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "resolving" and cur["run_id"] != rid, said
    assert rebase.in_progress(wt) and resolve.resolve_runs(conn, cur) == 2
    with db.tx(conn):                                                 # pretend two ran already
        store.create_run(conn, cur["thread_id"], selfbrief.RESOLVE.splitlines()[0])
    run_inline(conn, cur["run_id"])
    monkeypatch.delenv("EKI_FAKE_TOUCH")
    rebase.abort(wt)
    assert rebase.onto(wt, head) == ["a.txt"]
    (wt / "a.txt").write_text("one\nboth\nthree\n")
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and "3 resolve runs" in cur["error"] and not rebase.in_progress(wt)


def test_the_brief():
    text = selfbrief.resolve(goal="g", title="Do the thing", spec="s", summary="", head="abcdef1234567890",
                             conflicted=["eki/x.py"], head_changes="$ git log\nabc fix")
    assert "Do the thing" in text and "abcdef123456" in text and "conflict" in text
    assert "eki/x.py" in text and "ITEM: done" in text and "--continue" in text
