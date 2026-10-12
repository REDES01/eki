"""The engine holds judges and background agents for memory; a 'now' chat never waits."""
from eki import db, engine, selfwork, store

WAIT = "waiting for memory"


def fake_spawn(monkeypatch):
    spawned = []

    def spawn(c, rid):
        spawned.append(rid)
        store.update_run(c, rid, state="starting")
    monkeypatch.setattr(engine, "spawn", spawn)
    return spawned


def run(conn, title, prompt, **kw):
    with db.tx(conn):
        return store.create_run(conn, store.create_thread(conn, title, None), prompt, **kw)


def test_a_queued_judge_waits_below_the_floor(conn, monkeypatch):
    spawned = fake_spawn(monkeypatch)
    monkeypatch.setenv("EKI_FREE_GB", "4")
    rid = run(conn, "j", selfwork.CHECK, provider="command")
    engine.spawn_ready(conn)
    assert spawned == [] and store.run(conn, rid)["state"] == "queued"
    assert store.run(conn, rid)["why"].startswith(WAIT)     # a held run says why, not nothing
    monkeypatch.setenv("EKI_FREE_GB", "64")
    engine.spawn_ready(conn)
    assert spawned == [rid]


def test_a_now_chat_starts_regardless(conn, monkeypatch):
    spawned = fake_spawn(monkeypatch)
    monkeypatch.setenv("EKI_FREE_GB", "1")
    monkeypatch.setenv("EKI_SWAP_PCT", "95")
    rid = run(conn, "chat", "hi", provider="claude", priority="now")
    engine.spawn_ready(conn)
    assert spawned == [rid]


def test_a_background_agent_waits_and_its_thread_keeps_order(conn, monkeypatch):
    spawned = fake_spawn(monkeypatch)
    monkeypatch.setattr(engine.machine, "room", lambda: (True, ""))
    monkeypatch.setenv("EKI_FREE_GB", "4")
    bg = run(conn, "bg", "work", provider="claude", priority="background")
    tid = store.run(conn, bg)["thread_id"]
    with db.tx(conn):
        later = store.create_run(conn, tid, "ls", provider="command", priority="background")
    engine.spawn_ready(conn)
    assert spawned == []                            # the command behind it waits its turn
    monkeypatch.setenv("EKI_FREE_GB", "64")
    engine.spawn_ready(conn)
    assert spawned == [bg] and store.run(conn, later)["state"] == "queued"


def test_room_for_one_judge_not_two(conn, home, monkeypatch):
    import json
    routing = json.loads((home / "routing.json").read_text())
    routing["machine"] = {"checks": 4}
    (home / "routing.json").write_text(json.dumps(routing))
    spawned = fake_spawn(monkeypatch)
    monkeypatch.setenv("EKI_FREE_GB", "8")           # a check is 1.5: 6.5 fits, 5.0 doesn't
    a = run(conn, "a", selfwork.CHECK, provider="command")
    run(conn, "b", selfwork.CHECK, provider="command")
    engine.spawn_ready(conn)
    assert spawned == [a]
