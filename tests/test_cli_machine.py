"""`eki machine`: shows the caps, sets either or both, refuses a bad value."""
import json
import os

from eki import cli


def test_shows_the_values(home, capsys, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 18)
    assert cli.main(["machine"]) == 0
    assert capsys.readouterr().out.strip() == "agents at once 12 (1–32) · test runs at once 3"


def test_sets_them(home, capsys):
    assert cli.main(["machine", "--agents", "20", "--checks", "4"]) == 0
    assert "agents at once 20" in capsys.readouterr().out
    assert json.loads((home / "routing.json").read_text())["machine"] == {"agents": 20, "checks": 4}
    assert cli.main(["machine", "--checks", "6"]) == 0
    assert capsys.readouterr().out.strip() == "agents at once 20 (1–32) · test runs at once 6"


def test_a_bad_value_says_so(home, capsys):
    assert cli.main(["machine", "--agents", "0"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("eki: ") and "machine.agents" in err
    assert "machine" not in json.loads((home / "routing.json").read_text())
