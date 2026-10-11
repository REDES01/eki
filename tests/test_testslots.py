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
