"""The browser shim's wrapper admits a browser by memory too: after its slot,
before the real browser, with eki/memadmit.py's floor, swap limit and estimate."""
import json
import os
import subprocess
import time

import pytest

from eki import browsershim, browserslots, db, memadmit, paths

FAKE = """#!/bin/sh
echo "$$" > "$MARKER"
exec sleep "${HOLD:-30}"
"""

WAITING = "eki: waiting for memory"


@pytest.fixture
def exe(home, tmp_path, monkeypatch):
    """A wrapped fake browser that writes its pid to $MARKER and sleeps."""
    real = tmp_path / "pw-cache"
    exe_dir = real / "chromium_headless_shell-1187" / "chrome-headless-shell-mac-arm64"
    exe_dir.mkdir(parents=True)
    (exe_dir / "chrome-headless-shell").write_text(FAKE)
    (exe_dir / "chrome-headless-shell").chmod(0o755)
    monkeypatch.setenv("EKI_PLAYWRIGHT_CACHE", str(real))
    browsershim.ensure()
    return (browsershim.mirror() / "chromium_headless_shell-1187"
            / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")


def launch(exe, marker, **env):
    full = {**os.environ, "MARKER": str(marker), "HOLD": "30", **env}
    return subprocess.Popen([str(exe), "--headless"], env=full,
                            stderr=subprocess.PIPE, text=True)


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def stop(*children):
    for c in children:
        if c.poll() is None:
            c.kill()
        c.wait(timeout=10)


def held_for(seconds):
    """Every slot count seen over the next seconds."""
    seen = set()
    end = time.time() + seconds
    while time.time() < end:
        seen.add(browserslots.held())
        time.sleep(0.05)
    return seen


def test_db_path_is_baked_in(exe):
    assert repr(str(paths.db())) in exe.read_text()
    assert str(paths.db()) in browsershim._bake()


def test_below_the_floor_nothing_starts_and_no_slot_is_held(exe, tmp_path):
    marker = tmp_path / "m"
    child = launch(exe, marker, EKI_FREE_GB="3")
    try:
        assert held_for(1.5) == {0}
        assert not marker.exists() and child.poll() is None
    finally:
        stop(child)
    err = child.stderr.read()
    lines = [ln for ln in err.splitlines() if ln.strip()]
    assert lines == ["eki: waiting for memory (3.0 GB free, swap 0%) — Settings → Machine "
                     "sets the floor; Playwright's launch timeout still counts"]


def test_room_for_one_browser_starts_only_one(exe, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    first = launch(exe, a, EKI_FREE_GB="7.5")
    second = launch(exe, b, EKI_FREE_GB="7.5")
    try:
        assert wait_for(lambda: a.exists() or b.exists())
        time.sleep(1.5)
        assert a.exists() != b.exists()               # the second sees the first's fresh slot
        assert browserslots.held() == 1
        running, waiting = (first, second) if a.exists() else (second, first)
        later = b if a.exists() else a
        assert waiting.poll() is None
        stop(running)
        assert wait_for(later.exists)                 # starts once the first is gone
        assert browserslots.pids() == {waiting.pid}
    finally:
        stop(first, second)
    assert WAITING in (first.stderr.read() + second.stderr.read())


def test_raised_memory_lets_the_second_start(exe, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    first = launch(exe, a, EKI_FREE_GB="7.5")
    try:
        assert wait_for(a.exists)
        second = launch(exe, b, EKI_FREE_GB="7.5")
        time.sleep(1.5)
        assert not b.exists()
        stop(second)
        third = launch(exe, b, EKI_FREE_GB="9")      # 9 − 1.1 reserved − 1.1 ≥ 6
        try:
            assert wait_for(b.exists)
            assert browserslots.held() == 2
        finally:
            stop(third)
    finally:
        stop(first)


def test_a_learned_browser_estimate_is_honoured(exe, tmp_path):
    conn = db.connect()
    memadmit.learn(conn, "browser", 3.0)
    conn.commit()
    conn.close()
    marker = tmp_path / "m"
    child = launch(exe, marker, EKI_FREE_GB="8.5")    # 8.5 − 3.0 < 6, but 8.5 − 1.1 ≥ 6
    try:
        assert held_for(1.5) == {0}
        assert not marker.exists()
    finally:
        stop(child)
    assert WAITING in child.stderr.read()
    child = launch(exe, marker, EKI_FREE_GB="9")
    try:
        assert wait_for(marker.exists)
    finally:
        stop(child)


def test_swap_above_its_limit_holds_it(exe, tmp_path):
    marker = tmp_path / "m"
    child = launch(exe, marker, EKI_SWAP_PCT="70")
    try:
        time.sleep(1.5)
        assert not marker.exists() and child.poll() is None
    finally:
        stop(child)
    assert "swap 70%" in child.stderr.read()


def test_the_floor_is_read_from_settings(exe, tmp_path, home):
    data = json.loads((home / "routing.json").read_text())
    data["machine"] = {"floor_gb": 2, "swap_max": 80}
    (home / "routing.json").write_text(json.dumps(data))
    marker = tmp_path / "m"
    child = launch(exe, marker, EKI_FREE_GB="3.5", EKI_SWAP_PCT="70")
    try:
        assert wait_for(marker.exists)
    finally:
        stop(child)
