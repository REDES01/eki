import json

from eki import checkslots, db, engine, queue, selfwork, store


def slots(home, n):
    routing = json.loads((home / "routing.json").read_text())
    routing["machine"] = {"checks": n}
    (home / "routing.json").write_text(json.dumps(routing))


def judge(conn, title):
    return store.create_run(conn, store.create_thread(conn, title, None), selfwork.CHECK,
                            provider="command")


def test_two_slots_hold_the_third_judge_until_one_ends(home, conn, monkeypatch):
    slots(home, 2)
    spawned = []

    def spawn(c, rid):
        spawned.append(rid)
        store.update_run(c, rid, state="starting")
    monkeypatch.setattr(engine, "spawn", spawn)
    with db.tx(conn):
        a, b, c = judge(conn, "a"), judge(conn, "b"), judge(conn, "c")
        plain = store.create_run(conn, store.create_thread(conn, "p", None), "hello")
    engine.spawn_ready(conn)
    assert spawned == [a, b, plain]
    held = store.run(conn, c)
    assert checkslots.waiting(conn, held)
    engine.spawn_ready(conn)
    assert spawned == [a, b, plain]
    store.update_run(conn, a, state="done")
    assert not checkslots.waiting(conn, held)
    engine.spawn_ready(conn)
    assert spawned == [a, b, plain, c]


def test_one_slot_runs_one_judge_and_keeps_its_threads_order(home, conn, monkeypatch):
    slots(home, 1)
    spawned = []
    monkeypatch.setattr(engine, "spawn", lambda c, rid: spawned.append(rid))
    with db.tx(conn):
        a, b = judge(conn, "a"), judge(conn, "b")
        later = store.create_run(conn, store.run(conn, b)["thread_id"], "after the check")
    engine.spawn_ready(conn)
    assert spawned == [a]
    assert later not in spawned


def test_limit_follows_machine_checks_never_below_one(home):
    slots(home, 0)
    assert checkslots.limit() == 1
    slots(home, 3)
    assert checkslots.limit() == 3


def test_what_counts_as_a_judge(conn):
    with db.tx(conn):
        t = store.create_thread(conn, "t", None)
        gate1 = store.create_run(conn, t, selfwork.CHECK, provider="command")
        gate2 = store.create_run(conn, t, queue.GATE2, provider="command")
        other = store.create_run(conn, t, '["ls"]', provider="command")
        fake = store.create_run(conn, t, "run bin/check please", provider="fake")
    assert checkslots.is_judge(store.run(conn, gate1))
    assert checkslots.is_judge(store.run(conn, gate2))
    assert not checkslots.is_judge(store.run(conn, other))
    assert not checkslots.is_judge(store.run(conn, fake))
    assert not checkslots.waiting(conn, store.run(conn, gate1))


def test_an_agents_test_run_takes_a_slot_from_the_judges(home, conn):
    from eki import testslots
    slots(home, 1)
    with db.tx(conn):
        a = judge(conn, "a")
    with testslots.hold(conn, wait=0.01):
        assert checkslots.busy(conn) == 1
        assert checkslots.waiting(conn, store.run(conn, a))
    assert not checkslots.waiting(conn, store.run(conn, a))
