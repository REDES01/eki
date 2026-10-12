"""Settings → Machine's live line: what runs now, said in one sentence."""
import subprocess
import sys
import time

import pytest

from eki import browserslots, machinenow, providers, quota

HOLD = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
os.write(fd, str(os.getpid()).encode())
print("held", flush=True)
sys.stdin.read()
"""


def _item(conn, iid, state):
    conn.execute("INSERT INTO items(id, goal_id, title, state, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                 (iid, "g1", iid, state, time.time(), time.time()))


def _now(**kw):
    return {"agents": 3, "checks": 1, "browsers": 2, "load": 9.2, "cores": 18, "memory": 64, **kw}


def test_the_line_reads_as_a_sentence():
    assert machinenow.line(_now(quota=[{"provider": "claude", "left": 70}])) == \
        "now: 3 agents working · 1 test run · 2 browsers · load 9.2 on 18 cores · memory 64% · quota left claude 70%"


@pytest.mark.parametrize("now, words", [
    (_now(agents=1, checks=2, browsers=1, cores=1),
     "now: 1 agent working · 2 test runs · 1 browser · load 9.2 on 1 core · memory 64%"),
    (_now(agents=0, checks=0, browsers=0, load=0),
     "now: 0 agents working · 0 test runs · 0 browsers · load 0.0 on 18 cores · memory 64%"),
    (_now(quota=[]), "now: 3 agents working · 1 test run · 2 browsers · load 9.2 on 18 cores · memory 64%"),
    (_now(quota=[{"provider": "claude", "left": 70}, {"provider": "codex", "left": 5}]),
     "now: 3 agents working · 1 test run · 2 browsers · load 9.2 on 18 cores · memory 64% · "
     "quota left claude 70%, codex 5%"),
])
def test_singulars_plurals_and_no_quota(now, words):
    assert machinenow.line(now) == words


def test_now_counts_building_items_across_projects_and_reads_memory(conn, monkeypatch):
    monkeypatch.setenv("EKI_MEMORY_TOTAL_GB", "32")
    monkeypatch.setenv("EKI_FREE_GB", "8")
    for i, state in enumerate(["building", "building", "judging", "waiting", "building"]):
        _item(conn, f"i{i}", state)
    got = machinenow.now(conn)
    assert got["agents"] == 3 and got["checks"] == 0 and got["browsers"] == 0
    assert "· 0 browsers ·" in machinenow.line(got)
    assert got["memory"] == 75
    assert isinstance(got["load"], float) and got["cores"] >= 1
    assert "quota" not in got


def test_now_says_what_is_left_of_each_subscription(conn):
    assert "fake" in providers.config()
    quota.record(conn, "fake", {"five_hour": {"used": 0.3, "resets_at": time.time() + 3600},
                                "seven_day": {"used": 0.55, "resets_at": time.time() + 86400}})
    assert machinenow.now(conn)["quota"] == [{"provider": "fake", "left": 45}]


def test_now_counts_a_browser_slot_held_by_another_process(conn):
    child = subprocess.Popen([sys.executable, "-c", HOLD, str(browserslots.folder() / "0.lock")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        got = machinenow.now(conn)
        assert got["browsers"] == 1
        assert "· 1 browser ·" in machinenow.line(got)
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    assert machinenow.now(conn)["browsers"] == 0
