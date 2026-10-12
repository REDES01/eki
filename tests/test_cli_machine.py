"""`eki machine`: shows the caps and the memory floor, sets them, refuses a bad value."""
import json
import os

from eki import cli, db, memadmit

TAIL = " · memory floor 6 GB · swap at most 60%"


def _first(out):
    return out.strip().splitlines()[0]


def test_shows_the_values(home, capsys, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 18)
    assert cli.main(["machine"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["agents at once 12 (1–64) · test runs at once 3 · browsers at once 3" + TAIL,
                   "estimates: agent 0.3 GB · browser 1.1 GB · check 1.5 GB"]


def test_sets_them(home, capsys):
    assert cli.main(["machine", "--agents", "20", "--checks", "4"]) == 0
    assert "agents at once 20" in capsys.readouterr().out
    assert json.loads((home / "routing.json").read_text())["machine"] == {"agents": 20, "checks": 4}
    assert cli.main(["machine", "--checks", "6"]) == 0
    assert _first(capsys.readouterr().out) == "agents at once 20 (1–64) · test runs at once 6 · browsers at once 3" + TAIL


def test_a_bad_value_says_so(home, capsys):
    assert cli.main(["machine", "--agents", "0"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("eki: ") and "machine.agents" in err
    assert "machine" not in json.loads((home / "routing.json").read_text())


def test_agents_go_to_64_checks_stay_at_32(home, capsys):
    assert cli.main(["machine", "--agents", "64"]) == 0
    assert capsys.readouterr().out.startswith("agents at once 64 (1–64)")
    assert cli.main(["machine", "--agents", "65"]) == 1
    assert "machine.agents must be a whole number from 1 to 64, not 65" in capsys.readouterr().err
    assert cli.main(["machine", "--checks", "33"]) == 1
    assert "machine.checks must be a whole number from 1 to 32, not 33" in capsys.readouterr().err
    assert json.loads((home / "routing.json").read_text())["machine"] == {"agents": 64}


def test_browsers(home, capsys):
    assert cli.main(["machine", "--browsers", "5"]) == 0
    assert _first(capsys.readouterr().out).endswith(" · browsers at once 5" + TAIL)
    assert json.loads((home / "routing.json").read_text())["machine"] == {"browsers": 5}
    assert cli.main(["machine", "--browsers", "17"]) == 1
    assert "machine.browsers must be a whole number from 1 to 16, not 17" in capsys.readouterr().err
    assert cli.main(["machine"]) == 0
    assert _first(capsys.readouterr().out).endswith(" · browsers at once 5" + TAIL)


def test_the_memory_floor(home, capsys):
    assert cli.main(["machine", "--floor-gb", "8", "--swap-max", "50"]) == 0
    assert _first(capsys.readouterr().out).endswith(" · memory floor 8 GB · swap at most 50%")
    assert json.loads((home / "routing.json").read_text())["machine"] == {"floor_gb": 8, "swap_max": 50}
    for flags, why in ((["--floor-gb", "0"], "machine.floor_gb must be a whole number from 1 to 64, not 0"),
                       (["--floor-gb", "65"], "machine.floor_gb"),
                       (["--swap-max", "101"], "machine.swap_max must be a whole number from 1 to 100, not 101")):
        assert cli.main(["machine", *flags]) == 1
        assert why in capsys.readouterr().err
    assert json.loads((home / "routing.json").read_text())["machine"] == {"floor_gb": 8, "swap_max": 50}


def test_the_estimates_line_shows_learned_and_default_kinds(home, capsys):
    conn = db.connect()
    with conn:
        memadmit.learn(conn, "agent:claude", 0.4)
        memadmit.learn(conn, "browser", 1.24)
    conn.close()
    assert cli.main(["machine"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[1] == \
        "estimates: agent 0.3 GB · agent:claude 0.4 GB · browser 1.2 GB · check 1.5 GB"
