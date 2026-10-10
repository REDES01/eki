"""Component access rules: queue, exclusive, memory, conflicts — all read from the database."""
import json

import pytest

from eki import components, db, engine, store
from eki.components import access, service


@pytest.fixture
def up(monkeypatch):
    """Which components are up; the service kind's is_up/stop stubbed to read and change it."""
    class Up(set):
        stopped: list
    state = Up()
    stopped = []
    monkeypatch.setattr(service, "is_up", lambda c: c.name in state)

    def stop(c):
        stopped.append(c.name)
        state.discard(c.name)
        return True
    monkeypatch.setattr(service, "stop", stop)
    state.stopped = stopped
    return state


@pytest.fixture
def spawned(monkeypatch):
    out = []

    def spawn(conn, rid):
        out.append(rid)
        store.update_run(conn, rid, state="starting", spawned_at=db.now())
    monkeypatch.setattr(engine, "spawn", spawn)
    return out


def comp(name, **spec):
    d = components.pool_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps({"kind": "service", "about": name, "serve": "x",
                                                  "health": "y", "call": "z", **spec}))


def caller(conn):
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "caller", None), "do it", provider="fake")
    store.update_run(conn, rid, state="running", started_at=db.now())
    return rid


def use(conn, name, parent=None, folder="/tmp"):
    prompt = json.dumps({"component": name, "args": [], "caller_dir": folder, "caller_run": parent})
    with db.tx(conn):
        return store.create_run(conn, store.create_thread(conn, f"use {name}", None), prompt,
                                provider="component", parent=parent)


def start(conn, rid):
    store.update_run(conn, rid, state="running", started_at=db.now())


def end(conn, rid):
    store.update_run(conn, rid, state="done", ended_at=db.now())


def test_queue_serves_in_order_with_n_slots(conn, spawned, up):
    comp("images", access="queue", slots=2)
    calls = [use(conn, "images") for _ in range(4)]
    engine.spawn_ready(conn)
    assert spawned == calls[:2]
    assert "2 of 2 slots busy" in store.run(conn, calls[2])["why"]
    end(conn, calls[0])
    engine.spawn_ready(conn)
    assert spawned == calls[:3]
    assert store.run(conn, calls[2])["why"] is None


def test_queue_shares_slots_with_its_provider(conn, spawned, up):
    comp("images", access="queue", provider="comfyui")
    with db.tx(conn):
        pic = store.create_run(conn, store.create_thread(conn, "a picture", None), "draw", provider="comfyui")
    store.update_run(conn, pic, state="running")
    c = use(conn, "images")
    engine.spawn_ready(conn)
    assert c not in spawned
    end(conn, pic)
    engine.spawn_ready(conn)
    assert c in spawned


def test_exclusive_held_for_the_caller_run_and_freed_at_its_end(conn, spawned, up):
    comp("voice", access="exclusive")
    a, b = caller(conn), caller(conn)
    a1 = use(conn, "voice", a)
    engine.spawn_ready(conn)
    start(conn, a1)
    end(conn, a1)                                   # the call is done, the hold is not
    b1 = use(conn, "voice", b)
    a2 = use(conn, "voice", a)
    engine.spawn_ready(conn)
    assert spawned == [a1, a2]
    assert store.run(conn, b1)["why"] == f"waits for voice: held by {a}"
    end(conn, a2)
    end(conn, a)                                    # the caller's run ends: freed
    engine.spawn_ready(conn)
    assert spawned == [a1, a2, b1]
    assert access.holders(conn, "voice") == [b]


def test_a_call_with_no_caller_holds_only_while_it_runs(conn, spawned, up):
    comp("voice", access="exclusive")
    x, y = use(conn, "voice"), use(conn, "voice")
    engine.spawn_ready(conn)
    assert spawned == [x]
    end(conn, x)
    engine.spawn_ready(conn)
    assert spawned == [x, y]


def test_memory_short_start_waits_and_says_so(conn, spawned, up, monkeypatch):
    comp("voice", memory_gb=12)
    monkeypatch.setenv("EKI_FREE_GB", "5")
    c = use(conn, "voice")
    engine.spawn_ready(conn)
    assert spawned == [] and store.run(conn, c)["why"] == "waits for voice: needs 12 GB, 5 free"
    up.add("voice")                                 # already up: no new memory needed
    engine.spawn_ready(conn)
    assert spawned == [c]


def test_conflicting_components_never_up_or_in_use_together(conn, spawned, up):
    comp("voice", conflicts=["images"])
    comp("images", access="queue")
    up.add("images")
    img = use(conn, "images")
    engine.spawn_ready(conn)
    start(conn, img)
    v = use(conn, "voice")
    engine.spawn_ready(conn)
    assert v not in spawned and "conflicts with images" in store.run(conn, v)["why"]
    end(conn, img)                                  # idle but still up: stopped first
    engine.spawn_ready(conn)
    assert v in spawned and up.stopped == ["images"] and "images" not in up
    up.add("voice")
    img2 = use(conn, "images")                      # the other direction: voice is in use
    engine.spawn_ready(conn)
    assert img2 not in spawned and "conflicts with voice" in store.run(conn, img2)["why"]


def test_holds_and_queues_survive_a_restart(conn, spawned, up):
    comp("voice", access="exclusive")
    comp("images", access="queue")
    a, b = caller(conn), caller(conn)
    a1 = use(conn, "voice", a)
    i1, i2 = use(conn, "images"), use(conn, "images")
    engine.spawn_ready(conn)
    start(conn, a1)
    end(conn, a1)
    b1 = use(conn, "voice", b)
    conn.close()
    fresh = db.connect()                            # a restart: nothing but the database
    engine.spawn_ready(fresh)
    assert spawned == [a1, i1]
    assert access.holders(fresh, "voice") == [a]
    assert access.gate(fresh, store.run(fresh, b1)) == (False, f"waits for voice: held by {a}")
    assert access.gate(fresh, store.run(fresh, i2))[0] is False


def test_the_wait_is_noted_on_the_callers_run_once(conn, spawned, up, monkeypatch):
    comp("voice", access="exclusive", memory_gb=12)
    a, b = caller(conn), caller(conn)
    a1 = use(conn, "voice", a)
    start(conn, a1)
    b1 = use(conn, "voice", b)
    for _ in range(3):
        engine.spawn_ready(conn)
    monkeypatch.setenv("EKI_FREE_GB", "1")
    end(conn, a1)
    end(conn, a)                                    # now it waits for memory instead: why changes
    engine.spawn_ready(conn)
    assert store.run(conn, b1)["why"] == "waits for voice: needs 12 GB, 1 free"
    notes = [json.loads(e["data"]) for e in store.events_after(conn, b) if e["kind"] == "note"]
    assert notes == [{"text": f"waits for voice: held by {a}", "call": b1}]
    assert not [e for e in store.events_after(conn, b1) if e["kind"] == "note"]


def test_a_missing_component_lets_the_run_start(conn, spawned, up):
    c = use(conn, "nothing")
    engine.spawn_ready(conn)
    assert spawned == [c]
