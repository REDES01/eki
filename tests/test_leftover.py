"""On judge, background tasks a build left in its worktree are killed and said (eki/leftover.py)."""
import os
import signal
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

def test_eki_s_own_servers_and_processes_are_spared(home, tmp_path):
    import json
    import sys
    from eki.components import servers
    assert str(servers.state_dir()).startswith(str(home))     # the fixture home, never the real one
    wt = tmp_path / "wt"
    wt.mkdir()
    nap = "import time; time.sleep(60)"
    server = subprocess.Popen(["/bin/sh", "-c", "sleep 60 & wait"], cwd=wt, start_new_session=True)
    (servers.state_dir() / "serve-test.json").write_text(json.dumps({"pid": server.pid}))
    (servers.state_dir() / "broken.json").write_text("{not json")
    ours = subprocess.Popen([sys.executable, "-c", nap, "eki.components.static"], cwd=wt)
    job = subprocess.Popen(["sleep", "60"], cwd=wt)
    try:
        import time
        time.sleep(0.3)         # the server's own sleep has started, in its session
        found = leftover.find(str(wt))
        assert [f["pid"] for f in found] == [job.pid]      # not the server, its session, or eki
        leftover.kill(found)
        assert job.wait(timeout=5) is not None
        assert server.poll() is None and ours.poll() is None
        assert leftover.find(str(wt)) == []
    finally:
        try:
            os.killpg(server.pid, signal.SIGKILL)
        except OSError:
            pass
        for p in (server, ours, job):
            p.kill()
            p.wait()

def test_is_eki_reads_command_lines():
    yes = ["/u/.venv/bin/eki mcp-hold x", "eki use claude", "python3 -m eki slot -- bin/check",
           "/usr/bin/python3 /u/.venv/bin/eki serve", "python -m eki.components.static 8080",
           "python3 -c x eki.components.static"]
    no = ["sleep 60", "npm run dev", "python3 -m http.server", "node /u/eki/web/x.js", ""]
    assert all(leftover._is_eki(c) for c in yes)
    assert not any(leftover._is_eki(c) for c in no)

def test_a_browser_slot_holder_is_spared_but_not_its_session(home, tmp_path, monkeypatch):
    from eki import browserslots
    wt = tmp_path / "wt"
    wt.mkdir()
    holder = subprocess.Popen(["sleep", "60"], cwd=wt)      # same session as the job: the agent's
    job = subprocess.Popen(["sleep", "61"], cwd=wt)
    monkeypatch.setattr(browserslots, "pids", lambda: {holder.pid})
    try:
        assert [f["pid"] for f in leftover.find(str(wt))] == [job.pid]
    finally:
        for p in (holder, job):
            p.kill()
            p.wait()
