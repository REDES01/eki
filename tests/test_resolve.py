from pathlib import Path

import pytest

from eki import db, rebase, resolve, resolvebrief, selfwork, store, workspace
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


def test_a_clean_resolution_goes_back_to_the_queue_for_gate_two(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    assert selfwork.store_item(conn, it["id"])["resolved_on"] == head
    says(tmp_path, monkeypatch, "SUMMARY: kept both.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued" and cur["queued_at"], (cur["error"], said)
    wt = cur["worktree"]
    assert not rebase.in_progress(wt) and cur["commit_sha"] == workspace.head(wt)
    assert git(wt, "rev-parse", "HEAD^") == head
    assert cur["head"] == head and cur["rebased"] == cur["commit_sha"]   # its base: the head it sits on
    assert cur["resolved_on"] == head and cur["gate2_run"] is None and cur["error"] is None
    assert said == [f"item {it['id']}: resolved onto {head[:12]}; back in the queue — gate 2 judges it"]
    assert [r["id"] for r in store.thread_runs(conn, it["thread_id"])] == [rid]   # no gate 1 run
    assert resolve.tick(conn) == []                                  # moved on once


def test_a_resolved_item_does_not_carry_its_old_head_along(conn, repo, conflicted, tmp_path, monkeypatch):
    """The head an item was resolved onto may leave the queue (gate 2 red). The next
    rebase moves only the item's own commits, not that head's."""
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued"
    # main moved on without `head` (its item failed gate 2): a new commit on the old base
    git(repo, "reset", "-q", "--hard", "HEAD^")
    other = commit(repo, "b.txt", "other\n", "another item")
    from eki import queue
    asked = []
    monkeypatch.setattr(rebase, "onto", lambda wt, h, since=None: asked.append((h, since)) or [])
    sha, said = queue._rebase(conn, cur, other)
    assert sha and any("rebased on" in s for s in said), said
    assert asked == [(other, head)]                    # `rebase --onto other head`: only its own commits move


def test_markers_left_get_a_second_try_then_are_refused(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "Both sides change line two; keep both.\nSUMMARY: sure.\nITEM: done\n")
    run_inline(conn, rid)
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "resolving" and cur["run_id"] != rid, (cur["error"], said)
    assert any("second try" in s and "a.txt" in s for s in said), said
    wt = Path(cur["worktree"])
    assert rebase.in_progress(wt) and resolve.resolve_runs(conn, cur) == 2
    prompt = store.run(conn, cur["run_id"])["prompt"]
    assert prompt.splitlines()[:2] == [resolvebrief.FIRST_LINE, resolvebrief.RETRY_LINE]
    assert "markers at lines 2–6 of a.txt" in prompt and "edit the files" in prompt
    assert "lines 2–6:" in prompt and "<<<<<<<" in prompt and "\nmine\n" in prompt and "\ntheirs\n" in prompt
    assert resolve.tick(conn) == []                                  # the retry hasn't ended
    run_inline(conn, cur["run_id"])                                  # it leaves the markers too
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and "a.txt" in cur["error"] and "marker" in cur["error"]
    assert "second resolve run" in cur["error"]
    assert not rebase.in_progress(wt)
    assert workspace.head(wt) == it["commit_sha"] and (wt / "a.txt").read_text() == "one\nmine\nthree\n"


def test_no_item_line_gets_a_second_try(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "Kept both.\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "resolving" and cur["run_id"] != rid, (cur["error"], said)
    prompt = store.run(conn, cur["run_id"])["prompt"]
    assert resolvebrief.RETRY_LINE in prompt and "no ITEM: line" in prompt
    assert "a.txt" in prompt and resolvebrief.NO_HUNKS in prompt
    run_inline(conn, cur["run_id"])                                  # no ITEM line again
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and cur["error"] == "no ITEM line after a second resolve run"


def test_a_good_second_try_goes_back_to_the_queue(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "SUMMARY: sure.\nITEM: done\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, cur["run_id"])
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued" and cur["head"] == head and cur["resolved_on"] == head
    assert all(r["provider"] != "command" for r in store.thread_runs(conn, it["thread_id"]))


def test_a_person_is_asked_for(conn, conflicted, tmp_path, monkeypatch):
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: person the two intents contradict\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and "contradict" in cur["error"]
    assert not rebase.in_progress(cur["worktree"])


def test_a_leftover_recheck_drains_back_to_the_queue_whatever_it_said(conn, conflicted, tmp_path, monkeypatch):
    """An item an older build left in `rechecking` (gate 1 after a resolve): a red check
    is not counted against it — gate 2 judges it."""
    it, head = conflicted
    rid = resolve.start(conn, it, head, ["a.txt"])
    says(tmp_path, monkeypatch, "ITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "a.txt")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    with db.tx(conn):                                                 # as the old build left it
        check = store.create_run(conn, cur["thread_id"], selfwork.CHECK, provider="command")
        selfwork._set(conn, it["id"], state="rechecking", run_id=check, rebased=None)
    assert resolve.tick(conn) == []                                  # its run hasn't ended
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    run_inline(conn, check)
    assert store.run(conn, check)["state"] == "failed"
    said = resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued" and cur["queued_at"] and cur["error"] is None, said
    assert cur["head"] == head and cur["rebased"] == cur["commit_sha"] and cur["gate2_run"] is None
    assert "not counted" in cur["verdict"] and "failed" in cur["verdict"]
    assert said == [f"item {it['id']}: resolved; back in the queue — gate 2 judges it"]
    assert resolve.tick(conn) == []


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
        store.create_run(conn, cur["thread_id"], resolvebrief.FIRST_LINE)
    run_inline(conn, cur["run_id"])
    monkeypatch.delenv("EKI_FAKE_TOUCH")
    rebase.abort(wt)
    assert rebase.onto(wt, head) == ["a.txt"]
    (wt / "a.txt").write_text("one\nboth\nthree\n")
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and "3 resolve runs" in cur["error"] and not rebase.in_progress(wt)


def test_the_brief():
    text = resolvebrief.resolve(goal="g", title="Do the thing", spec="s", summary="", head="abcdef1234567890",
                                conflicted=["eki/x.py"], head_changes="$ git log\nabc fix", landed="")
    assert "Do the thing" in text and "abcdef123456" in text and "conflict" in text
    assert "eki/x.py" in text and "ITEM: done" in text and "--continue" in text


def test_retry_brief():
    kw = dict(goal="g", title="Do the thing", spec="s", summary="", head="abcdef1234567890",
              conflicted=["log.md"], head_changes="", landed="")
    text = resolvebrief.retry(previous_failure="the file still has markers at lines 12–30 of log.md",
                              hunks="log.md:\n  lines 12–30:\n<<<<<<< HEAD", **kw)
    lines = text.splitlines()
    assert lines[0] == resolvebrief.FIRST_LINE and lines[1] == resolvebrief.RETRY_LINE
    assert "lines 12–30 of log.md" in text and "<<<<<<< HEAD" in text and "Do the thing" in text
    assert resolvebrief.retry(previous_failure="your answer had no ITEM: line", hunks="", **kw).count(
        resolvebrief.NO_HUNKS) == 1
