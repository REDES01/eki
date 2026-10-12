"""The scheduler starts an item only when the Mac's memory has room for one more agent."""
from eki import memadmit, scheduler
from tests.test_scheduler import add_item, agents, one, src, state  # noqa: F401 — src is a fixture

WAIT = "waiting for memory ("


def test_below_the_floor_nothing_new_starts_and_building_is_left_alone(conn, src, monkeypatch):
    agents(4)
    first = one(conn, "goal a", ["eki/a1.py"])
    assert len(scheduler.start_ready(conn)) == 1
    _, second = add_item(conn, "a two", ["eki/a2.py"])
    third = one(conn, "goal b", ["eki/b1.py"])
    monkeypatch.setenv("EKI_FREE_GB", "4")
    assert scheduler.start_ready(conn) == []
    assert state(conn, first)["state"] == "building"
    for iid in (second, third):
        assert tuple(state(conn, iid)) == ("waiting", "waiting for memory (4.0 GB free, swap 0%)")
    monkeypatch.setenv("EKI_FREE_GB", "64")
    assert len(scheduler.start_ready(conn)) == 2
    assert state(conn, second)["why"] is None and state(conn, third)["why"] is None


def test_room_for_one_agent_starts_one(conn, src, monkeypatch):
    agents(4)
    memadmit.learn(conn, "agent:claude", 0.3)
    ids = [one(conn, f"goal {c}", [f"eki/{c}.py"]) for c in "abc"]
    monkeypatch.setenv("EKI_FREE_GB", "6.5")          # 6.5 - 0.3 fits the floor of 6; 6.5 - 0.6 doesn't
    assert len(scheduler.start_ready(conn)) == 1
    states = [tuple(state(conn, i)) for i in ids]
    assert states.count(("building", None)) == 1
    left = [s for s in states if s[0] == "waiting"]
    assert len(left) == 2 and all(s[1].startswith(WAIT) for s in left)


def test_the_counts_are_checked_first(conn, src, monkeypatch):
    agents(1)
    one(conn, "goal a", ["eki/a.py"])
    b = one(conn, "goal b", ["eki/b.py"])
    assert len(scheduler.start_ready(conn)) == 1
    monkeypatch.setenv("EKI_FREE_GB", "4")
    scheduler.start_ready(conn)
    assert tuple(state(conn, b)) == ("waiting", "1 agents working (machine)")
