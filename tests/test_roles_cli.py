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
    assert planner.startswith("planner   fake → fake · its default") and planner.endswith("[default]")


def test_set_and_clear_a_role(home, capsys):
    _providers(home, fake={"kind": "fake", "models": {"fast": "fake-small"}})
    before = json.loads((home / "routing.json").read_text())
    assert cli.main(["roles", "planner", "fake:fast"]) == 0
    assert "planner   fake:fast → fake · fake-small" in capsys.readouterr().out
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


# -- provider, model and effort ----------------------------------------------

def _claude(home, monkeypatch):
    from eki import capacity
    _providers(home, claude={"kind": "claude_code"})
    monkeypatch.setattr(capacity, "status", lambda conn, background=False: {"claude": (True, "")})


def _read(conn):
    """A read list for claude: opus offers efforts, haiku takes none."""
    rows = [("opus", "Opus 5.5", '["low", "medium", "high"]', "medium", 1),
            ("haiku", "Haiku 4.5", None, None, 0)]
    for model, label, efforts, default, is_default in rows:
        conn.execute("INSERT INTO provider_models(provider, model, label, efforts, default_effort,"
                     " is_default, read_at) VALUES ('claude',?,?,?,?,?,1)",
                     (model, label, efforts, default, is_default))
    conn.commit()


def _written(home):
    return json.loads((home / "routing.json").read_text()).get("roles")


def test_provider_model_and_effort_write_the_shortest_form(home, monkeypatch, capsys):
    _claude(home, monkeypatch)
    assert cli.main(["roles", "router", "claude", "haiku"]) == 0
    assert _written(home)["router"] == "claude:haiku"
    assert cli.main(["roles", "planner", "claude", "opus", "--effort", "high"]) == 0
    assert _written(home)["planner"] == {"provider": "claude", "model": "opus", "effort": "high"}
    out = capsys.readouterr().out
    assert "router    claude:haiku → claude · haiku" in out
    assert "planner   claude:opus@high → claude · opus · high" in out
    assert cli.main(["roles", "router", "claude", "fast"]) == 0          # a tier as the model word
    assert _written(home)["router"] == "claude:fast"
    assert cli.main(["roles", "router", "claude:opus", "haiku"]) == 1
    assert "not both" in capsys.readouterr().err


def test_with_the_list_read_it_shows_labels_and_refuses_what_isnt_offered(home, conn, monkeypatch,
                                                                          capsys):
    _claude(home, monkeypatch)
    _read(conn)
    assert cli.main(["roles", "planner", "claude", "opus", "--effort", "high"]) == 0
    assert "planner   claude:opus@high → claude · Opus 5.5 · high" in capsys.readouterr().out
    before = (home / "routing.json").read_text()
    assert cli.main(["roles", "planner", "claude", "opus", "--effort", "max"]) == 1
    assert "claude opus doesn't offer effort max; it offers low, medium (default), high" \
        in capsys.readouterr().err
    assert cli.main(["roles", "router", "claude", "haiku", "--effort", "low"]) == 1
    assert "claude haiku takes no effort" in capsys.readouterr().err
    assert cli.main(["roles", "router", "claude", "sonnet"]) == 1
    assert "claude doesn't list sonnet; it offers opus, haiku" in capsys.readouterr().err
    assert cli.main(["roles", "router", "claude", "--effort", "max"]) == 1   # the default model's
    assert "claude opus doesn't offer effort max" in capsys.readouterr().err
    assert (home / "routing.json").read_text() == before
    cli.main(["roles"])
    assert "router    none → nobody" in capsys.readouterr().out
