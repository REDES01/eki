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
    assert not checkslots.waiting(conn, store.run(conn, gate1))     # alone, a judge takes a free slot
    with db.tx(conn):
        gate2 = store.create_run(conn, t, queue.GATE2, provider="command")
        other = store.create_run(conn, t, '["ls"]', provider="command")
        fake = store.create_run(conn, t, "run bin/check please", provider="fake")
    assert checkslots.is_judge(store.run(conn, gate1))
    assert checkslots.is_judge(store.run(conn, gate2))
    assert not checkslots.is_judge(store.run(conn, other))
    assert not checkslots.is_judge(store.run(conn, fake))
    # (a wide gate 2 queued after it reserves its slots first: tests/test_check.py, docs/self-build.md)


def test_an_agents_test_run_takes_a_slot_from_the_judges(home, conn):
    from eki import testslots
    slots(home, 1)
    with testslots.hold(conn, wait=0.01):       # a queued check would go first: hold, then queue
        with db.tx(conn):
            a = judge(conn, "a")
        assert checkslots.busy(conn) == 1
        assert checkslots.waiting(conn, store.run(conn, a))
    assert not checkslots.waiting(conn, store.run(conn, a))


WIDE = '["/bin/sh", "-c", "EKI_CHECK_WIDE=1 EKI_CHECK_FAST=1 exec bin/check -q"]'


def wide(conn, title):
    return store.create_run(conn, store.create_thread(conn, title, None), WIDE, provider="command")


def cores(monkeypatch, n):
    monkeypatch.setattr(checkslots.os, "cpu_count", lambda: n)


def test_a_wide_run_weighs_its_workers_over_a_narrow_runs(home, conn, monkeypatch):
    slots(home, 6)
    with db.tx(conn):
        w, n = wide(conn, "w"), judge(conn, "n")
    assert checkslots.is_wide(store.run(conn, w))
    assert not checkslots.is_wide(store.run(conn, n))
    for count, expected in ((18, 3), (4, 1), (None, 2)):
        cores(monkeypatch, count)
        assert checkslots.weight(store.run(conn, w)) == expected
        assert checkslots.weight(store.run(conn, n)) == 1
    cores(monkeypatch, 18)
    slots(home, 2)
    assert checkslots.weight(store.run(conn, w)) == 2      # never more than the limit


def test_busy_sums_weights(home, conn, monkeypatch):
    slots(home, 6)
    cores(monkeypatch, 18)
    with db.tx(conn):
        w, n = wide(conn, "w"), judge(conn, "n")
    store.update_run(conn, w, state="running")
    store.update_run(conn, n, state="running")
    assert checkslots.busy(conn) == 4


def test_reserved_is_the_first_ready_wide_run(home, conn, monkeypatch):
    slots(home, 6)
    cores(monkeypatch, 18)
    with db.tx(conn):
        cancelled, later, w = wide(conn, "c"), wide(conn, "l"), wide(conn, "w")
    store.update_run(conn, cancelled, cancel=1)
    store.update_run(conn, later, retry_at=db.now() + 3600)
    store.update_run(conn, w, state="running")
    assert checkslots.reserved(conn) == 0
    store.update_run(conn, later, retry_at=db.now() - 1)
    assert checkslots.reserved(conn) == 3


def test_a_narrow_judge_waits_while_a_wide_one_is_reserved(home, conn, monkeypatch):
    slots(home, 6)
    cores(monkeypatch, 18)
    with db.tx(conn):
        running = [judge(conn, f"r{i}") for i in range(3)]
        w, n = wide(conn, "w"), judge(conn, "n")
    for rid in running:
        store.update_run(conn, rid, state="running")
    assert checkslots.reserved(conn) == 3
    assert not checkslots.waiting(conn, store.run(conn, w))     # 3 + 3 fits in 6
    assert checkslots.waiting(conn, store.run(conn, n))         # 3 + 1 + 3 doesn't
    store.update_run(conn, w, cancel=1)
    assert not checkslots.waiting(conn, store.run(conn, n))


TRAIN = '["/bin/sh", "-c", "bin/check -q && python -m eki.candidate"]'


def test_the_train_goes_before_an_earlier_gate_1(home, conn, monkeypatch):
    slots(home, 1)
    spawned = []
    monkeypatch.setattr(engine, "spawn", lambda c, rid: spawned.append(rid))
    with db.tx(conn):
        judge(conn, "gate 1")
        train = store.create_run(conn, store.create_thread(conn, "train", None), TRAIN,
                                 provider="command", priority="background")
    engine.spawn_ready(conn)
    assert spawned == [train]


def test_rank_puts_the_train_then_gate_2_then_gate_1(home, conn, monkeypatch):
    slots(home, 1)
    spawned = []
    monkeypatch.setattr(engine, "spawn", lambda c, rid: spawned.append(rid))
    with db.tx(conn):
        g1 = judge(conn, "gate 1")
        g2 = store.create_run(conn, store.create_thread(conn, "gate 2", None), queue.GATE2,
                              provider="command")
        conn.execute("INSERT INTO items(id, goal_id, title, gate2_run, created_at, updated_at) "
                     "VALUES ('i1', 'g1', 'x', ?, 1, 1)", (g2,))
        tr = store.create_run(conn, store.create_thread(conn, "train", None), TRAIN,
                              provider="command")
    assert [checkslots.rank(conn, store.run(conn, r)) for r in (tr, g2, g1)] == [0, 1, 2]
    queued = [store.run(conn, r) for r in (g1, g2, tr)]
    assert [r["id"] for r in checkslots.first(conn, queued)] == [tr, g2, g1]
    engine.spawn_ready(conn)
    assert spawned == [tr]


def test_first_keeps_other_runs_after_the_judges_in_order(conn):
    with db.tx(conn):
        p1 = store.create_run(conn, store.create_thread(conn, "p1", None), "hello")
        j = judge(conn, "j")
        p2 = store.create_run(conn, store.create_thread(conn, "p2", None), "again")
    queued = [store.run(conn, r) for r in (p1, j, p2)]
    assert [r["id"] for r in checkslots.first(conn, queued)] == [j, p1, p2]


def test_ahead_counts_only_checks_that_could_start(home, conn, monkeypatch):
    with db.tx(conn):
        ready = judge(conn, "ready")
    assert checkslots.ahead(conn) == 1
    store.update_run(conn, ready, state="done")
    with db.tx(conn):
        later = judge(conn, "later")
        t = store.create_thread(conn, "busy thread", None)
        turn = store.create_run(conn, t, "build", provider="fake")
        store.create_run(conn, t, selfwork.CHECK, provider="command")
        bg = store.create_run(conn, store.create_thread(conn, "bg", None), selfwork.CHECK,
                              provider="command", priority="background")
        store.create_run(conn, store.create_thread(conn, "plain", None), '["ls"]',
                         provider="command")
    store.update_run(conn, later, retry_at=db.now() + 600)
    store.update_run(conn, turn, state="running")
    assert checkslots.ahead(conn) == 1                  # only the background one, with room
    monkeypatch.setenv("EKI_MACHINE", "busy")
    assert checkslots.ahead(conn) == 0
    monkeypatch.setenv("EKI_MACHINE", "ok")
    store.update_run(conn, bg, cancel=1)
    assert checkslots.ahead(conn) == 0
