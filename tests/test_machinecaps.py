"""Machine caps in routing.json: defaults, clamping, the one-time move from "self", saving."""
import json
import os

import pytest

from eki import api_settings, checkslots, machinecaps


def routing(home):
    return json.loads((home / "routing.json").read_text())


def write(home, **blocks):
    data = routing(home)
    data.update(blocks)
    (home / "routing.json").write_text(json.dumps(data))


def test_defaults_follow_the_cores(home, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 18)
    assert machinecaps.settings() == {"agents": 12, "checks": 3, "browsers": 3}
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert machinecaps.checks_default() == 2
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert machinecaps.checks_default() == 2


def test_values_are_clamped(home):
    write(home, machine={"agents": 99, "checks": 0})
    assert machinecaps.settings() == {"agents": 64, "checks": 1, "browsers": 3}
    write(home, machine={"agents": 50, "checks": 50})
    assert machinecaps.settings() == {"agents": 50, "checks": 32, "browsers": 3}


def test_a_bad_file_falls_back_to_the_defaults(home, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    (home / "routing.json").write_text("{not json")
    assert machinecaps.settings() == {"agents": 12, "checks": 2, "browsers": 3}
    (home / "routing.json").write_text(json.dumps({"machine": "lots"}))
    assert machinecaps.settings() == {"agents": 12, "checks": 2, "browsers": 3}
    (home / "routing.json").write_text(json.dumps({"machine": {"agents": "x", "checks": None}}))
    assert machinecaps.settings() == {"agents": 12, "checks": 2, "browsers": 3}


def test_self_parallel_and_check_slots_move_once(home):
    write(home, self={"parallel": 6, "check_slots": 3, "autonomy": "apply"})
    assert machinecaps.settings() == {"agents": 6, "checks": 3, "browsers": 3}
    data = routing(home)
    assert data["machine"] == {"agents": 6, "checks": 3}
    assert data["self"] == {"autonomy": "apply"}
    assert data["rows"]                                   # every other key kept
    path = home / "routing.json"
    before = (path.stat().st_mtime_ns, path.read_text())
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    machinecaps.settings()
    assert path.stat().st_mtime_ns == 1_000_000_000 and path.read_text() == before[1]


def test_old_values_move_within_each_bound(home):
    write(home, self={"parallel": 50, "check_slots": 50})
    assert machinecaps.settings() == {"agents": 50, "checks": 32, "browsers": 3}
    assert routing(home)["machine"] == {"agents": 50, "checks": 32}


def test_no_old_keys_means_no_write(home):
    path = home / "routing.json"
    before = path.read_text()
    machinecaps.settings()
    assert path.read_text() == before


def test_save_sets_fields_and_refuses_out_of_range(home):
    assert machinecaps.save(agents=8)["agents"] == 8
    assert machinecaps.save(checks=4) == {"agents": 8, "checks": 4, "browsers": 3}
    assert routing(home)["machine"] == {"agents": 8, "checks": 4}
    assert machinecaps.save(agents=64)["agents"] == 64
    for bad in (0, 65):
        with pytest.raises(ValueError) as e:
            machinecaps.save(agents=bad)
        assert str(e.value) == f"machine.agents must be a whole number from 1 to 64, not {bad}"
    with pytest.raises(ValueError) as e:
        machinecaps.save(checks=33)
    assert str(e.value) == "machine.checks must be a whole number from 1 to 32, not 33"
    assert routing(home)["machine"] == {"agents": 64, "checks": 4}


def test_save_keeps_migrated_values(home):
    write(home, self={"parallel": 6, "check_slots": 3})
    assert machinecaps.save(agents=10) == {"agents": 10, "checks": 3, "browsers": 3}


def test_problems_in_words():
    assert machinecaps.problems({}) == []
    assert machinecaps.problems({"agents": 12, "checks": 3}) == []
    assert machinecaps.problems([]) == ["machine must be an object"]
    got = machinecaps.problems({"agents": 0, "checks": True})
    assert len(got) == 2 and "machine.agents" in got[0] and "machine.checks" in got[1]
    assert machinecaps.problems({"agents": 64, "checks": 32}) == []
    assert machinecaps.problems({"agents": 65, "checks": 33}) == [
        "machine.agents must be a whole number from 1 to 64, not 65",
        "machine.checks must be a whole number from 1 to 32, not 33"]


def test_check_slots_follow_machine_checks(home):
    write(home, machine={"checks": 5})
    assert checkslots.limit() == 5


def test_the_settings_post_refuses_zero_agents(home, conn):
    got = api_settings.read(conn, "routing")
    data = {**got["data"], "machine": {"agents": 0}}
    with pytest.raises(api_settings.Invalid) as e:
        api_settings.write(conn, "routing", {"data": data, "mtime": got["mtime"]})
    assert e.value.problems == ["machine.agents must be a whole number from 1 to 64, not 0"]
    data["machine"] = {"agents": 16, "checks": 4}
    api_settings.write(conn, "routing", {"data": data, "mtime": got["mtime"]})
    assert machinecaps.settings() == {"agents": 16, "checks": 4, "browsers": 3}


def test_browsers_default_to_3_and_are_clamped(home):
    assert machinecaps.BROWSERS == 3 and machinecaps.MOST["browsers"] == 16
    assert machinecaps.settings()["browsers"] == 3
    write(home, machine={"browsers": 40})
    assert machinecaps.settings()["browsers"] == 16
    write(home, machine={"browsers": 0})
    assert machinecaps.settings()["browsers"] == 1
    write(home, machine={"browsers": "x"})
    assert machinecaps.settings()["browsers"] == 3


def test_save_and_problems_for_browsers(home):
    assert machinecaps.save(browsers=5)["browsers"] == 5
    assert machinecaps.save(agents=9)["browsers"] == 5
    assert routing(home)["machine"] == {"browsers": 5, "agents": 9}
    assert machinecaps.save(browsers=16)["browsers"] == 16
    for bad in (0, 17, True):
        with pytest.raises(ValueError) as e:
            machinecaps.save(browsers=bad)
        assert str(e.value) == f"machine.browsers must be a whole number from 1 to 16, not {bad!r}"
    assert routing(home)["machine"]["browsers"] == 16
    assert machinecaps.problems({"browsers": 1}) == machinecaps.problems({"browsers": 16}) == []
    assert machinecaps.problems({"agents": 12, "browsers": 17}) == [
        "machine.browsers must be a whole number from 1 to 16, not 17"]
