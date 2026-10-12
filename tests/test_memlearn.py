"""Memory learning: peaks of process trees, measured from a fake ps table, folded into estimates."""
import fcntl
import os
import subprocess

import pytest

from eki import browserslots, memadmit, memlearn, selfwork, store, testslots

GB = 1024 * 1024        # ps reports RSS in KB


@pytest.fixture
def ps(monkeypatch):
    """table[pid] = (ppid, GB); tick reads it as ps would print it."""
    table = {1: (0, 0.01)}
    monkeypatch.setattr(memlearn, "_ps", lambda: "".join(
        f"{pid} {pp} {round(gb * GB)}\n" for pid, (pp, gb) in table.items()))
    return table


@pytest.fixture
def slots():
    held = []

    def take(folder, i, pid):
        path = folder / f"{i}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        path.write_text(str(pid))
        held.append(fd)
        return fd
    yield take
    for fd in held:
        try:
            os.close(fd)
        except OSError:
            pass


def peaks(conn):
    return {r[0]: pytest.approx(r[1]) for r in conn.execute("SELECT key, peak_gb FROM mem_peaks")}


def test_a_browser_tree_is_learned_when_its_pid_goes(conn, ps, slots):
    ps.update({100: (1, 0.4), 101: (100, 0.7)})
    slots(browserslots.folder(), 0, 100)
    assert memlearn.tick(conn) == []
    assert peaks(conn) == {"browser:100": 1.1}
    del ps[100], ps[101]
    assert memlearn.tick(conn) == ["memory: browser 1.1 GB (1 samples)"]
    assert memadmit.estimate(conn, "browser") == pytest.approx(1.1)
    assert peaks(conn) == {}


def test_the_peak_only_rises(conn, ps, slots):
    ps.update({100: (1, 0.5)})
    slots(browserslots.folder(), 0, 100)
    memlearn.tick(conn)
    ps[100] = (1, 0.9)
    memlearn.tick(conn)
    assert peaks(conn) == {"browser:100": 0.9}
    ps[100] = (1, 0.2)
    memlearn.tick(conn)
    assert peaks(conn) == {"browser:100": 0.9}


def test_an_agent_run_is_learned_without_its_browser(conn, ps, slots):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", provider="claude")
    store.update_run(conn, rid, state="running", pid=200, child_pid=201)
    ps.update({200: (1, 0.05), 201: (200, 0.25), 202: (201, 0.4), 203: (202, 0.6)})
    slots(browserslots.folder(), 0, 202)
    memlearn.tick(conn)
    assert peaks(conn) == {f"run:{rid}": 0.3, "browser:202": 1.0}
    store.update_run(conn, rid, state="done")
    assert memlearn.tick(conn) == ["memory: agent:claude 0.3 GB (1 samples)"]
    assert memadmit.estimate(conn, "agent:claude") == pytest.approx(0.3)
    assert peaks(conn) == {"browser:202": 1.0}


def test_a_judge_run_learns_under_check(conn, ps):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, selfwork.CHECK, provider="command")
    other = store.create_run(conn, tid, "ls", provider="command")
    store.update_run(conn, rid, state="running", pid=300)
    store.update_run(conn, other, state="running", pid=301)
    ps.update({300: (1, 1.0), 310: (300, 1.0), 301: (1, 5.0)})
    memlearn.tick(conn)
    assert peaks(conn) == {f"run:{rid}": 2.0}
    store.update_run(conn, rid, state="failed")
    memlearn.tick(conn)
    assert memadmit.estimate(conn, "check") == pytest.approx(2.0)


def test_a_test_slot_holder_learns_under_check(conn, ps, slots):
    ps.update({400: (1, 0.1), 401: (400, 1.4)})
    slots(testslots.folder(), 0, 400)
    memlearn.tick(conn)
    assert peaks(conn) == {"hold:400": 1.5}
    del ps[400], ps[401]
    memlearn.tick(conn)
    assert memadmit.estimate(conn, "check") == pytest.approx(1.5)


def test_estimates_fold_from_measured_peaks(conn, ps, slots):
    ps.update({100: (1, 1.0), 101: (1, 2.0)})
    slots(browserslots.folder(), 0, 100)
    slots(browserslots.folder(), 1, 101)
    memlearn.tick(conn)
    del ps[100]
    memlearn.tick(conn)
    del ps[101]
    assert memlearn.tick(conn) == ["memory: browser 1.3 GB (2 samples)"]
    assert memadmit.estimate(conn, "browser") == pytest.approx(0.7 * 1.0 + 0.3 * 2.0)


def test_a_ps_failure_learns_nothing(conn, ps, slots, monkeypatch):
    ps.update({100: (1, 1.0)})
    slots(browserslots.folder(), 0, 100)
    memlearn.tick(conn)

    def broken():
        raise subprocess.CalledProcessError(1, "ps")
    monkeypatch.setattr(memlearn, "_ps", broken)
    assert memlearn.tick(conn) == []
    monkeypatch.setattr(memlearn, "_ps", lambda: "")
    assert memlearn.tick(conn) == []
    assert peaks(conn) == {"browser:100": 1.0}
    assert conn.execute("SELECT COUNT(*) FROM mem_estimates").fetchone()[0] == 0


def test_housekeeping_runs_it():
    from eki import housekeep
    assert ("memory", memlearn.tick) in housekeep.STEPS
