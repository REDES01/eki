"""A wide judge run (gate 2, the train's check) takes several check slots and goes first."""
import json

from eki import checkslots, db, engine, selfwork, store

WIDE = '["/bin/sh", "-c", "EKI_CHECK_WIDE=1 EKI_CHECK_FAST=1 exec bin/check -q"]'


def setup(home, monkeypatch, checks):
    routing = json.loads((home / "routing.json").read_text())
    routing["machine"] = {"checks": checks}
    (home / "routing.json").write_text(json.dumps(routing))
    monkeypatch.setattr(checkslots.os, "cpu_count", lambda: 18)
    spawned = []

    def spawn(c, rid):
        spawned.append(rid)
        store.update_run(c, rid, state="starting")
    monkeypatch.setattr(engine, "spawn", spawn)
    return spawned


def run(conn, title, prompt):
    return store.create_run(conn, store.create_thread(conn, title, None), prompt, provider="command")


def test_the_wide_run_starts_first_and_alone_when_it_fills_the_limit(home, conn, monkeypatch):
    spawned = setup(home, monkeypatch, 3)
    with db.tx(conn):
        a = run(conn, "gate 1: a", selfwork.CHECK)
        w = run(conn, "gate 2: w", WIDE)
        b = run(conn, "gate 1: b", selfwork.CHECK)
    engine.spawn_ready(conn)
    assert spawned == [w]
    store.update_run(conn, w, state="done")
    engine.spawn_ready(conn)
    assert spawned == [w, a, b]


def test_no_new_narrow_run_starts_while_the_wide_one_waits(home, conn, monkeypatch):
    spawned = setup(home, monkeypatch, 3)
    with db.tx(conn):
        first = run(conn, "gate 1: first", selfwork.CHECK)
    engine.spawn_ready(conn)
    assert spawned == [first]
    with db.tx(conn):
        w = run(conn, "gate 2: w", WIDE)
        n = run(conn, "gate 1: n", selfwork.CHECK)
    engine.spawn_ready(conn)
    assert spawned == [first]                       # 1 + 3 > 3, and n would take w's slots
    assert checkslots.waiting(conn, store.run(conn, w))
    assert checkslots.waiting(conn, store.run(conn, n))
    store.update_run(conn, first, state="done")
    engine.spawn_ready(conn)
    assert spawned == [first, w]
