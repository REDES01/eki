"""`eki roles`: list eki's own jobs, set one to a target, clear it back to its default."""
import json

from eki import cli, roles


def _providers(home, **extra):
    got = json.loads((home / "providers.json").read_text())
    got.update(extra)
    (home / "providers.json").write_text(json.dumps(got))


def test_lists_every_role(home, capsys):
    assert cli.main(["roles"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [l.split()[0] for l in lines] == list(roles.ROLES)
    planner = next(l for l in lines if l.startswith("planner"))
    assert planner.startswith("planner   fake → its default") and planner.endswith("[default]")


def test_set_and_clear_a_role(home, capsys):
    _providers(home, fake={"kind": "fake", "models": {"fast": "fake-small"}})
    before = json.loads((home / "routing.json").read_text())
    assert cli.main(["roles", "planner", "fake:fast"]) == 0
    assert "planner   fake:fast → fake-small" in capsys.readouterr().out
    data = json.loads((home / "routing.json").read_text())
    assert data["roles"] == {"planner": "fake:fast"}
    assert {k: v for k, v in data.items() if k != "roles"} == before
    assert roles.get("planner") == "fake:fast"
    cli.main(["roles"])
    assert not next(l for l in capsys.readouterr().out.splitlines()
                    if l.startswith("planner")).endswith("[default]")

    assert cli.main(["roles", "planner", "default"]) == 0
    assert json.loads((home / "routing.json").read_text()) == before
    assert roles.get("planner") == "fake"


def test_a_bad_target_or_role_exits_1_with_the_problem(home, capsys):
    before = (home / "routing.json").read_text()
    assert cli.main(["roles", "router", "nope:fast"]) == 1
    assert "unknown provider 'nope'" in capsys.readouterr().err
    assert cli.main(["roles", "router", "fake:"]) == 1
    assert "nothing after the colon" in capsys.readouterr().err
    assert cli.main(["roles", "planner", "off"]) == 1
    assert "planner can't be off" in capsys.readouterr().err
    assert cli.main(["roles", "boss", "fake"]) == 1
    assert "unknown role 'boss'" in capsys.readouterr().err
    assert (home / "routing.json").read_text() == before


def test_off_is_allowed_for_chores(home, capsys):
    assert cli.main(["roles", "chores", "off"]) == 0
    assert capsys.readouterr().out.startswith("chores    off")
    assert roles.pick("chores").provider is None
