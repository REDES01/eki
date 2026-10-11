"""Settings → Machine's live line: what runs now, said in one sentence."""
import time

import pytest

from eki import machinenow, providers, quota


def _item(conn, iid, state):
    conn.execute("INSERT INTO items(id, goal_id, title, state, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                 (iid, "g1", iid, state, time.time(), time.time()))


def _now(**kw):
    return {"agents": 3, "checks": 1, "load": 9.2, "cores": 18, "memory": 64, **kw}


def test_the_line_reads_as_a_sentence():
    assert machinenow.line(_now(quota=[{"provider": "claude", "left": 70}])) == \
        "now: 3 agents working · 1 test run · load 9.2 on 18 cores · memory 64% · quota left claude 70%"


@pytest.mark.parametrize("now, words", [
    (_now(agents=1, checks=2, cores=1), "now: 1 agent working · 2 test runs · load 9.2 on 1 core · memory 64%"),
    (_now(agents=0, checks=0, load=0), "now: 0 agents working · 0 test runs · load 0.0 on 18 cores · memory 64%"),
    (_now(quota=[]), "now: 3 agents working · 1 test run · load 9.2 on 18 cores · memory 64%"),
    (_now(quota=[{"provider": "claude", "left": 70}, {"provider": "codex", "left": 5}]),
     "now: 3 agents working · 1 test run · load 9.2 on 18 cores · memory 64% · quota left claude 70%, codex 5%"),
])
def test_singulars_plurals_and_no_quota(now, words):
    assert machinenow.line(now) == words


def test_now_counts_building_items_across_projects_and_reads_memory(conn, monkeypatch):
    monkeypatch.setenv("EKI_MEMORY_TOTAL_GB", "32")
    monkeypatch.setenv("EKI_FREE_GB", "8")
    for i, state in enumerate(["building", "building", "judging", "waiting", "building"]):
        _item(conn, f"i{i}", state)
    got = machinenow.now(conn)
    assert got["agents"] == 3 and got["checks"] == 0
    assert got["memory"] == 75
    assert isinstance(got["load"], float) and got["cores"] >= 1
    assert "quota" not in got


def test_now_says_what_is_left_of_each_subscription(conn):
    assert "fake" in providers.config()
    quota.record(conn, "fake", {"five_hour": {"used": 0.3, "resets_at": time.time() + 3600},
                                "seven_day": {"used": 0.55, "resets_at": time.time() + 86400}})
    assert machinenow.now(conn)["quota"] == [{"provider": "fake", "left": 45}]
