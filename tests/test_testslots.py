"""Agents' own test runs (`eki slot --`) share the machine's check slots with judge runs."""
import json
import os
import subprocess
import sys
import threading
import time

from eki import checkslots, db, engine, projectbrief, projects, selfbrief, selfwork, store, testslots


def slots(home, n):
    routing = json.loads((home / "routing.json").read_text())
    routing["machine"] = {"checks": n}
    (home / "routing.json").write_text(json.dumps(routing))


def judge(conn, title):
    return store.create_run(conn, store.create_thread(conn, title, None), selfwork.CHECK,
                            provider="command")


def test_a_hold_waits_for_the_running_judge_then_takes_its_slot(home, conn):
    slots(home, 1)
    with db.tx(conn):
        rid = judge(conn, "j")
    store.update_run(conn, rid, state="running")
    got = threading.Event()

    def holder():
        c = db.connect()
        with testslots.hold(c, wait=0.02):
            got.set()
            assert testslots.held() == 1
        c.close()
    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.2)
    assert not got.is_set()
    store.update_run(conn, rid, state="done")
    t.join(5)
    assert got.is_set()
    assert testslots.held() == 0


def test_busy_counts_holds(home, conn):
    slots(home, 3)
    assert checkslots.busy(conn) == 0
    with testslots.hold(conn, wait=0.01):
        assert checkslots.busy(conn) == 1
        with testslots.hold(conn, wait=0.01):
            assert checkslots.busy(conn) == 2
    assert checkslots.busy(conn) == 0


def test_a_hold_in_another_process_keeps_a_judge_queued_until_it_exits(home, conn, monkeypatch):
    slots(home, 1)
    spawned = []

    def spawn(c, rid):
        spawned.append(rid)
        store.update_run(c, rid, state="starting")
    monkeypatch.setattr(engine, "spawn", spawn)
    code = ("import sys, time\nfrom eki import db, testslots\n"
            "with testslots.hold(db.connect(), wait=0.01):\n"
            "    print('held', flush=True)\n    sys.stdin.read()\n")
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        assert child.stdout.readline().strip() == "held"
        with db.tx(conn):
            rid = judge(conn, "j")
        engine.spawn_ready(conn)
        assert spawned == []
        assert checkslots.waiting(conn, store.run(conn, rid))
    finally:
        child.stdin.close()
        child.wait(10)
    assert testslots.held() == 0
    engine.spawn_ready(conn)
    assert spawned == [rid]


def test_both_build_briefs_name_eki_slot(conn, proj):
    kw = dict(goal="g", title="t", spec="s", files=["x.py"], branch="b", base="0" * 16,
              source="/src", others=[])
    assert "eki slot -- " in selfbrief.build(**kw)
    p = projects.get(conn, projects.add(conn, proj))
    b = projectbrief.build(**kw, project=p)
    assert "eki slot -- " in b
    assert "eki slot -- " in projectbrief._check(p) or not projects.check_argv(p)


def test_a_hold_waits_while_a_wide_judge_is_reserved(home, conn, monkeypatch):
    slots(home, 3)
    monkeypatch.setattr(checkslots.os, "cpu_count", lambda: 18)
    with db.tx(conn):
        w = store.create_run(conn, store.create_thread(conn, "gate 2: w", None),
                             '["/bin/sh", "-c", "EKI_CHECK_WIDE=1 exec bin/check -q"]',
                             provider="command")
    assert checkslots.reserved(conn) == 3
    assert checkslots.ahead(conn) == 3              # ahead weighs it; nothing added on top
    got = threading.Event()

    def holder():
        c = db.connect()
        with testslots.hold(c, wait=0.02):
            got.set()
        c.close()
    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.2)
    assert not got.is_set()                         # queued: its slots stay free
    store.update_run(conn, w, state="running")
    time.sleep(0.2)
    assert not got.is_set()                         # running: it fills them
    store.update_run(conn, w, state="done")
    t.join(5)
    assert got.is_set()


def until(cond, secs=5.0):
    end = time.time() + secs
    while not cond() and time.time() < end:
        time.sleep(0.01)
    return cond()


def fake_spawn(monkeypatch):
    spawned = []

    def spawn(c, rid):
        spawned.append(rid)
        store.update_run(c, rid, state="starting")
    monkeypatch.setattr(engine, "spawn", spawn)
    return spawned


def test_a_queued_judge_goes_ahead_of_an_earlier_hold(home, conn, monkeypatch):
    slots(home, 1)
    spawned = fake_spawn(monkeypatch)
    release, a_has, b_has = threading.Event(), threading.Event(), threading.Event()

    def a():
        c = db.connect()
        with testslots.hold(c, wait=0.02):
            a_has.set()
            release.wait(5)
        c.close()

    def b():
        c = db.connect()
        with testslots.hold(c, wait=0.02):
            b_has.set()
        c.close()
    ta, tb = threading.Thread(target=a), threading.Thread(target=b)
    ta.start()
    assert a_has.wait(5)
    tb.start()
    assert until(lambda: testslots.waiting() == 1)
    with db.tx(conn):
        rid = judge(conn, "j")
    assert testslots.behind(conn) == 1
    release.set()
    ta.join(5)
    time.sleep(0.2)
    assert not b_has.is_set()
    assert testslots.held() == 0 and testslots.waiting() == 1
    engine.spawn_ready(conn)
    assert spawned == [rid]
    store.update_run(conn, rid, state="running")
    time.sleep(0.2)
    assert not b_has.is_set()
    store.update_run(conn, rid, state="done")
    tb.join(5)
    assert b_has.is_set()
    assert testslots.waiting() == 0 and testslots.behind(conn) == 0


def test_a_running_hold_is_never_interrupted(home, conn, monkeypatch):
    slots(home, 1)
    spawned = fake_spawn(monkeypatch)
    finished = False
    with testslots.hold(conn, wait=0.01):
        with db.tx(conn):
            rid = judge(conn, "j")
        engine.spawn_ready(conn)
        assert spawned == []
        assert testslots.held() == 1
        finished = True
    assert finished
    engine.spawn_ready(conn)
    assert spawned == [rid]


def test_a_slow_hold_says_why_once_and_keeps_waiting(home, conn, capsys):
    slots(home, 1)
    with db.tx(conn):
        rid = judge(conn, "j")
    got = threading.Event()

    def holder():
        c = db.connect()
        with testslots.hold(c, wait=0.02, notice=0.05):
            got.set()
        c.close()
    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.5)
    assert not got.is_set()
    assert testslots.waiting() == 1
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("eki slot:")]
    assert lines == ["eki slot: waiting 1 min for a test slot — checks (the train, gate 2, "
                     "gate 1) go first; still waiting"]
    store.update_run(conn, rid, state="done")
    t.join(5)
    assert got.is_set()
    assert "eki slot:" not in capsys.readouterr().err


def test_a_killed_waiting_hold_leaves_no_waiter(home, conn):
    slots(home, 1)
    with db.tx(conn):
        judge(conn, "j")
    code = ("from eki import db, testslots\n"
            "with testslots.hold(db.connect(), wait=0.01):\n    pass\n")
    child = subprocess.Popen([sys.executable, "-c", code], env=os.environ.copy())
    try:
        assert until(lambda: testslots.waiting() == 1, 10)
        assert testslots.held() == 0
    finally:
        child.kill()
        child.wait(10)
    assert testslots.waiting() == 0
