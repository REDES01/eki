"""Gate 1 red and builds that ended early get one fixer run before unfit or left (eki/workstep.py, fed by eki/buildfeed.py)."""
import pytest

from eki import db, fixer, fixerbrief, selfwork, store, workspace
from conftest import run_inline
from test_self import says


@pytest.fixture
def src(tmp_path, monkeypatch):
    """Gate 1 is green only once tests/test_s.py exists — the stale test the fixer updates."""
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    (r / "eki" / "x.py").write_text("# x\n")
    (r / "bin" / "check").write_text("#!/bin/sh\n"
                                     "test -f tests/test_s.py || { echo 'FAILED test_s: expected the old order'; exit 1; }\n"
                                     "exit ${EKI_TEST_CHECK_EXIT:-0}\n")
    (r / "bin" / "check").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def step(conn, iid, expect):
    selfwork.tick(conn)
    fixer.tick(conn)
    cur = selfwork.store_item(conn, iid)
    assert cur["state"] == expect, (expect, cur["state"], cur["error"])
    return cur


def fixers(conn, it):
    return [r for r in store.thread_runs(conn, it["thread_id"]) if r["prompt"].startswith(fixerbrief.FIRST_LINE)]


def red_twice(conn, tmp_path, monkeypatch):
    """An item built twice, gate 1 red both times: it goes to the fixer."""
    selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    selfwork.tick(conn)
    iid = selfwork.items_in(conn, ("building",))[0]["id"]
    says(tmp_path, monkeypatch, "SUMMARY: changed x.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/x.py")
    for expect in ("judging", "building", "judging", "fixing"):
        run_inline(conn, selfwork.store_item(conn, iid)["run_id"])
        cur = step(conn, iid, expect)
    assert cur["tries"] == 2 and fixer.of(cur)["stage"] == "gate1"
    assert "expected the old order" in store.run(conn, cur["run_id"])["prompt"]
    assert len(fixers(conn, cur)) == 1
    return iid


def test_a_stale_test_is_fixed_and_proposed(conn, src, tmp_path, monkeypatch):
    iid = red_twice(conn, tmp_path, monkeypatch)
    says(tmp_path, monkeypatch, "SUMMARY: the test expected the old order; updated it.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "tests/test_s.py")
    run_inline(conn, selfwork.store_item(conn, iid)["run_id"])
    cur = step(conn, iid, "judging")
    run_inline(conn, cur["run_id"])
    cur = step(conn, iid, "proposed")
    assert "tests/test_s.py" in workspace.changed(cur["worktree"], cur["base"])
    assert fixer.of(cur)["summary"] == "the test expected the old order; updated it."


def test_a_real_bug_is_unfit_with_the_fixers_summary(conn, src, tmp_path, monkeypatch):
    iid = red_twice(conn, tmp_path, monkeypatch)
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")                         # red whatever the fixer does
    says(tmp_path, monkeypatch, "SUMMARY: 2 + 2 is not 5 here; the intent is wrong.\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "tests/test_s.py")
    run_inline(conn, selfwork.store_item(conn, iid)["run_id"])
    cur = step(conn, iid, "judging")
    run_inline(conn, cur["run_id"])
    cur = step(conn, iid, "unfit")
    assert len(fixers(conn, cur)) == 1                                      # no second fixer
    assert cur["error"] == "checks failed — the fixer tried (gate1): 2 + 2 is not 5 here; the intent is wrong."


def build_says(conn, tmp_path, monkeypatch, text):
    selfwork.submit(conn, "x", plan=False, files=["eki/x.py"])
    selfwork.tick(conn)
    it = selfwork.items_in(conn, ("building",))[0]
    says(tmp_path, monkeypatch, text)
    monkeypatch.delenv("EKI_FAKE_TOUCH", raising=False)
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    return selfwork.store_item(conn, it["id"])


def test_a_build_that_ended_early_with_nothing_changed_gets_a_fixer(conn, src, tmp_path, monkeypatch):
    cur = build_says(conn, tmp_path, monkeypatch, "Started the tests; waiting for them to finish.\n")
    assert cur["state"] == "fixing" and fixer.of(cur)["stage"] == "nothing"
    note = fixer.of(cur)["failure"]
    assert "the agent changed nothing" in note and "waiting for them" in note


def test_an_honest_person_gets_no_fixer(conn, src, tmp_path, monkeypatch):
    cur = build_says(conn, tmp_path, monkeypatch, "SUMMARY: can't.\nITEM: person needs a real trackpad\n")
    assert cur["state"] == "left" and cur["fixer"] is None and cur["error"] == "needs a real trackpad"


def test_retry_clears_the_fixer(conn, src, tmp_path, monkeypatch):
    cur = build_says(conn, tmp_path, monkeypatch, "Ran out of turn.\n")
    assert cur["state"] == "fixing"
    with pytest.raises(ValueError, match="is fixing"):                     # it holds its write-set
        selfwork.retry(conn, cur["id"])
    with db.tx(conn):
        selfwork._set(conn, cur["id"], state="left")
    selfwork.retry(conn, cur["id"])
    cur = selfwork.store_item(conn, cur["id"])
    assert cur["state"] == "waiting" and cur["fixer"] is None
