"""Each kind of run is held only by its own limit: checks by check slots, builds by
self.parallel, everything else not at all — unless EKI_MAX_PARALLEL sets a ceiling."""
import json

import pytest

from eki import checkslots, db, engine, scheduler, selfwork, store, workspace


@pytest.fixture
def spawned(monkeypatch):
    got = []

    def spawn(c, rid):
        got.append(rid)
        store.update_run(c, rid, state="starting")
    monkeypatch.setattr(engine, "spawn", spawn)
    return got


def settings(home, **kw):
    routing = json.loads((home / "routing.json").read_text())
    routing["self"] = kw
    (home / "routing.json").write_text(json.dumps(routing))


def judge(conn, title):
    return store.create_run(conn, store.create_thread(conn, title, None), selfwork.CHECK,
                            provider="command")


def plain(conn, title, **kw):
    return store.create_run(conn, store.create_thread(conn, title, None), f"say {title}", **kw)


def running(conn, rid):
    store.update_run(conn, rid, state="running")
    return rid


def test_a_plan_and_a_chat_start_while_check_and_build_slots_are_full(home, conn, spawned):
    settings(home, check_slots=2, parallel=4)
    with db.tx(conn):
        for i in range(2):
            running(conn, judge(conn, f"check {i}"))
        for i in range(4):                       # builds at self.parallel
            running(conn, store.create_run(conn, store.create_thread(conn, f"build {i}", None),
                                           f"build {i}", provider="claude"))
        for i in range(4):                       # other Claude turns
            running(conn, plain(conn, f"turn {i}", provider="claude"))
        plan = plain(conn, "plan", row="plan")
        chat = plain(conn, "chat")
    assert len(store.runs_in(conn, ("starting", "running"))) == 10
    assert engine.spawn_ready(conn) == 2
    assert spawned == [plan, chat]


def test_a_fourth_check_waits_for_a_slot(home, conn, spawned):
    settings(home, check_slots=3)
    with db.tx(conn):
        first = [running(conn, judge(conn, f"check {i}")) for i in range(3)]
        fourth = judge(conn, "check 4")
        chat = plain(conn, "chat")
    engine.spawn_ready(conn)
    assert spawned == [chat]
    assert checkslots.waiting(conn, store.run(conn, fourth))
    store.update_run(conn, first[0], state="done")
    engine.spawn_ready(conn)
    assert spawned == [chat, fourth]


def _repo(r):
    (r / "eki").mkdir(parents=True)
    (r / "bin").mkdir()
    (r / "eki" / "x.py").write_text("# x\n")
    (r / "bin" / "check").write_text("#!/bin/sh\nexit 0\n")
    (r / "bin" / "check").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def test_a_fifth_build_waits_for_parallel_while_a_chat_starts(home, conn, spawned, tmp_path,
                                                              monkeypatch):
    monkeypatch.setenv("EKI_SOURCE", str(_repo(tmp_path / "src")))
    settings(home, parallel=4)
    items = []
    for i in range(5):
        gid = selfwork.submit(conn, f"goal {i}", plan=False, files=[f"eki/f{i}.py"])
        items.append(conn.execute("SELECT id FROM items WHERE goal_id=?", (gid,)).fetchone()[0])
    scheduler.start_ready(conn)
    state = {i: tuple(conn.execute("SELECT state, why FROM items WHERE id=?", (i,)).fetchone())
             for i in items}
    assert sum(1 for s in state.values() if s[0] == "building") == 4
    assert ("waiting", "4 agents working (machine)") in state.values()
    with db.tx(conn):
        chat = plain(conn, "chat")
    engine.spawn_ready(conn)
    assert chat in spawned


def test_no_ceiling_unless_set(home, conn, spawned, monkeypatch):
    monkeypatch.delenv("EKI_MAX_PARALLEL", raising=False)
    assert engine.ceiling() is None
    with db.tx(conn):
        runs = [plain(conn, f"t{i}") for i in range(12)]
    assert engine.spawn_ready(conn) == 12
    assert spawned == runs
    for bad in ("0", "", "x", "-2"):
        monkeypatch.setenv("EKI_MAX_PARALLEL", bad)
        assert engine.ceiling() is None


def test_a_set_ceiling_counts_every_kind(home, conn, spawned, monkeypatch):
    settings(home, check_slots=4)
    monkeypatch.setenv("EKI_MAX_PARALLEL", "3")
    assert engine.ceiling() == 3
    with db.tx(conn):
        running(conn, judge(conn, "busy check"))
        queued = [judge(conn, "check a"), plain(conn, "a"), judge(conn, "check b"), plain(conn, "b")]
    assert engine.spawn_ready(conn) == 2
    assert spawned == queued[:2]
    assert all(store.run(conn, r)["state"] == "queued" for r in queued[2:])
    store.update_run(conn, queued[0], state="done")
    engine.spawn_ready(conn)
    assert spawned == queued[:3]
