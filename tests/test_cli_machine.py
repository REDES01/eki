"""`eki machine`: shows the caps, sets either or both, refuses a bad value."""
import json
import os

from eki import cli


def test_shows_the_values(home, capsys, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 18)
    assert cli.main(["machine"]) == 0
    assert capsys.readouterr().out.strip() == "agents at once 12 (1–64) · test runs at once 3 · browsers at once 3"


def test_sets_them(home, capsys):
    assert cli.main(["machine", "--agents", "20", "--checks", "4"]) == 0
    assert "agents at once 20" in capsys.readouterr().out
    assert json.loads((home / "routing.json").read_text())["machine"] == {"agents": 20, "checks": 4}
    assert cli.main(["machine", "--checks", "6"]) == 0
    assert capsys.readouterr().out.strip() == "agents at once 20 (1–64) · test runs at once 6 · browsers at once 3"


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
    assert capsys.readouterr().out.strip().endswith(" · browsers at once 5")
    assert json.loads((home / "routing.json").read_text())["machine"] == {"browsers": 5}
    assert cli.main(["machine", "--browsers", "17"]) == 1
    assert "machine.browsers must be a whole number from 1 to 16, not 17" in capsys.readouterr().err
    assert cli.main(["machine"]) == 0
    assert capsys.readouterr().out.strip().endswith(" · browsers at once 5")
