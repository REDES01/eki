"""Memory admission: the floor, swap, the per-pass tally, estimates and what was just started."""
import fcntl
import os
import time

import pytest

from eki import browserslots, db, memadmit, selfwork, store, testslots

WAIT = "waiting for memory"


def test_below_the_floor_nothing_new_starts(conn, monkeypatch):
    monkeypatch.setenv("EKI_FREE_GB", "4")
    assert memadmit.low() == "waiting for memory (4.0 GB free, swap 0%)"
    assert memadmit.Tick(conn).fits("browser") == "waiting for memory (4.0 GB free, swap 0%)"
    assert memadmit.fits(conn, "agent:claude").startswith(WAIT)


def test_too_much_swap_with_memory_short_and_nothing_new_starts(conn, monkeypatch):
    monkeypatch.setenv("EKI_SWAP_PCT", "70")
    monkeypatch.setenv("EKI_FREE_GB", "10")          # above the floor (6), under two floors
    assert memadmit.low() == "waiting for memory (10.0 GB free, swap 70%)"
    assert memadmit.Tick(conn).fits("browser") == "waiting for memory (10.0 GB free, swap 70%)"


def test_swap_left_over_with_plenty_free_holds_nothing(conn, monkeypatch):
    """macOS keeps swap allocated after the pressure is gone (2026-10-12: swap 84%,
    30 GB free, every check held for 15 minutes): alone it is no reason to wait."""
    monkeypatch.setenv("EKI_SWAP_PCT", "84")
    monkeypatch.setenv("EKI_FREE_GB", "30.7")
    assert memadmit.low() is None
    assert memadmit.Tick(conn).fits("check") is None


def test_plenty_of_room(conn):
    assert memadmit.low() is None
    assert memadmit.fits(conn, "check") is None


def test_room_for_one_browser_not_two(conn, monkeypatch):
    monkeypatch.setenv("EKI_FREE_GB", "7.5")
    tick = memadmit.Tick(conn)
    assert memadmit.estimate(conn, "browser") == 1.1
    assert tick.fits("browser") is None
    tick.took("browser")
    assert tick.fits("browser") == "waiting for memory (7.5 GB free, swap 0%)"


def test_the_floor_follows_the_setting(conn, monkeypatch):
    from eki import machinecaps
    monkeypatch.setenv("EKI_FREE_GB", "4")
    machinecaps.save(floor_gb=2, swap_max=80)
    assert memadmit.low() is None and memadmit.fits(conn, "browser") is None
    monkeypatch.setenv("EKI_SWAP_PCT", "81")
    assert memadmit.low() is None                       # 4 GB is two floors: swap alone isn't a reason
    monkeypatch.setenv("EKI_FREE_GB", "3")
    assert memadmit.low().startswith(WAIT)              # under two floors, the swap limit holds


def test_estimate_defaults_and_the_agent_fallback(conn):
    assert memadmit.estimate(conn, "check") == 1.5
    assert memadmit.estimate(conn, "agent:claude") == 0.3
    assert memadmit.estimate(conn, "agent") == 0.3
    memadmit.learn(conn, "agent:claude", 0.5)
    memadmit.learn(conn, "agent:codex", 0.8)
    assert memadmit.estimate(conn, "agent:claude") == 0.5
    assert memadmit.estimate(conn, "agent:local") == 0.3
    assert memadmit.estimate(conn, "agent") == 0.8


def test_learn_first_sets_then_folds(conn):
    assert memadmit.learn(conn, "browser", 1.0) == 1.0
    assert memadmit.learn(conn, "browser", 2.0) == pytest.approx(0.7 * 1.0 + 0.3 * 2.0)
    assert memadmit.learn(conn, "browser", 0.001) == pytest.approx(1.3)       # no measurement
    rows = {e["kind"]: e for e in memadmit.estimates(conn)}
    assert rows["browser"] == {"kind": "browser", "gb": pytest.approx(1.3), "samples": 2, "learned": True}
    assert rows["check"] == {"kind": "check", "gb": 1.5, "samples": 0, "learned": False}
    assert [e["kind"] for e in memadmit.estimates(conn)] == ["agent", "browser", "check"]


def test_run_kind(conn):
    tid = store.create_thread(conn, "t", None)
    judge = store.create_run(conn, tid, selfwork.CHECK, provider="command")
    other = store.create_run(conn, tid, "ls", provider="command")
    comp = store.create_run(conn, tid, "x", provider="component")
    agent = store.create_run(conn, tid, "hi", provider="claude")
    kinds = [memadmit.run_kind(store.run(conn, r)) for r in (judge, other, comp, agent)]
    assert kinds == ["check", None, None, "agent:claude"]


def test_reserved_counts_only_just_started_runs(conn):
    tid = store.create_thread(conn, "t", None)
    fresh = store.create_run(conn, tid, "hi", provider="claude")
    old = store.create_run(conn, tid, "hi", provider="claude")
    store.update_run(conn, fresh, state="running", spawned_at=db.now() - 10)
    store.update_run(conn, old, state="running", spawned_at=db.now() - 300)
    assert memadmit.reserved(conn) == pytest.approx(0.3)


def test_reserved_counts_fresh_held_slot_files(conn):
    held = []
    for path in (browserslots.folder() / "0.lock", testslots.folder() / "0.lock",
                 browserslots.folder() / "1.lock"):
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.append(fd)
    stale = time.time() - 300
    os.utime(browserslots.folder() / "1.lock", (stale, stale))      # held, but long since started
    (browserslots.folder() / "2.lock").write_text("")                # fresh, but no one holds it
    try:
        assert memadmit.reserved(conn) == pytest.approx(1.1 + 1.5)
        tick = memadmit.Tick(conn)
        assert tick.reserved == pytest.approx(2.6)
    finally:
        for fd in held:
            os.close(fd)
