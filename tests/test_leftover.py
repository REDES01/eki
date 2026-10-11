"""On judge, background tasks a build left in its worktree are killed and said (eki/leftover.py)."""
import subprocess

import pytest

from eki import leftover, selfwork, store
from conftest import run_inline
from test_self import PLAN, src, says  # noqa: F401 — the source fixture and what the fake says


def test_a_sleep_left_in_the_worktree_is_found_and_killed(home, tmp_path):
    wt = tmp_path / "wt" / "sub"
    wt.mkdir(parents=True)
    p = subprocess.Popen(["sleep", "60"], cwd=wt)
    try:
        found = leftover.find(str(tmp_path / "wt"))
        assert [f["pid"] for f in found] == [p.pid] and "sleep" in found[0]["cmd"]
        assert leftover.kill(found) == found
        assert p.wait(timeout=5) is not None
        assert leftover.find(str(tmp_path / "wt")) == []
    finally:
        p.kill()
        p.wait()

def test_nothing_running_and_errors_find_nothing(home, tmp_path, monkeypatch):
    (tmp_path / "quiet").mkdir()
    assert leftover.find(str(tmp_path / "quiet")) == []
    assert leftover.kill([{"pid": 0x7FFFFFF0, "cmd": "gone"}]) == []
    monkeypatch.setattr(leftover.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no lsof")))
    assert leftover.find(str(tmp_path / "quiet")) == []

def test_concluding_a_build_kills_leftovers_says_so_and_judges_on(conn, src, tmp_path, monkeypatch):
    gid = selfwork.submit(conn, "add two modules")
    says(tmp_path, monkeypatch, PLAN)
    run_inline(conn, conn.execute("SELECT plan_run FROM goals WHERE id=?", (gid,)).fetchone()[0])
    selfwork.tick(conn)
    a = selfwork.items_in(conn, ("building",))[0]
    seen = []
    monkeypatch.setattr(leftover, "find", lambda wt: seen.append(wt) or [{"pid": 4242, "cmd": "npm run dev"}])
    monkeypatch.setattr(leftover, "kill", lambda found: found)
    says(tmp_path, monkeypatch, "SUMMARY: made a.\n\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", "eki/a.py")
    build = a["run_id"]
    run_inline(conn, build)
    said = selfwork.tick(conn)
    line = f"item {a['id']}: the build left background tasks running — killed: 4242 npm run dev"
    assert line in said and a["worktree"] in seen
    assert any(s.startswith(f"item {a['id']}: built") for s in said)
    notes = [e["data"] for e in store.events_after(conn, build) if e["kind"] == "note"]
    assert any(line in n for n in notes)
    assert selfwork.store_item(conn, a["id"])["state"] == "judging"
