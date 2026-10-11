"""Every card the API returns carries its kind's fixed choices, for the settings form's dropdowns."""
import json
import shutil

import pytest

from eki import api_cards, apiroutes, components

ACCESS = ["queue", "exclusive", "shared"]


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


@pytest.fixture
def cards(conn, proj):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 ("p1", "game", str(proj), "trunk", 0, str(proj)))
    conn.commit()
    base = proj / "components"
    for t in ("knowledge", "agent", "assets"):
        shutil.copytree(components.templates_dir() / t, base / t)
    (base / "odd").mkdir()
    (base / "odd" / "component.json").write_text(json.dumps({"kind": "nope", "about": "x"}))
    return base


def listed(conn):
    return {c["name"]: c for c in call(conn, "GET", "/api/projects/p1/cards")["cards"]}


def test_project_cards_carry_choices(conn, cards):
    got = listed(conn)
    assert got["knowledge"]["choices"] == {"access": ACCESS, "read": ["always", "when-relevant"]}
    assert got["knowledge"]["choice_defaults"] == {"access": "shared"}
    assert got["agent"]["choices"] == {"access": ACCESS, "bare": [False, True]}
    assert got["agent"]["choice_defaults"] == {"access": "shared", "bare": False}
    assert got["assets"]["choices"] == {"access": ACCESS}
    assert got["odd"]["error"] and got["odd"]["choices"] == {"access": ACCESS}
    assert got["odd"]["choice_defaults"] == {"access": "shared"}
    assert json.loads(json.dumps(got["agent"]))["choices"]["bare"] == [False, True]


def test_a_single_card_and_an_edited_one_carry_choices(conn, cards):
    one = api_cards.card_of(conn, "p1", "knowledge")
    assert one["choices"]["read"] == ["always", "when-relevant"]
    edited = call(conn, "POST", "/api/projects/p1/cards/knowledge", {"settings": {"read": "when-relevant"}})["card"]
    assert edited["spec"]["read"] == "when-relevant"
    assert edited["choices"] == {"access": ACCESS, "read": ["always", "when-relevant"]}
    assert edited["choice_defaults"] == {"access": "shared"}


def test_every_card_carries_choice_help(conn, cards):
    got = listed(conn)
    for name, kind in (("knowledge", "knowledge"), ("agent", "agent"), ("assets", "assets"), ("odd", "nope")):
        assert got[name]["choice_help"] == components.choice_help(kind)
    assert got["odd"]["choice_help"] == {"access": components.choice_help("")["access"]}
    assert set(got["knowledge"]["choice_help"]["read"]) == {"always", "when-relevant"}
    back = json.loads(json.dumps(got["agent"]))
    assert set(back["choice_help"]["bare"]) == {"true", "false"}
    one = api_cards.card_of(conn, "p1", "knowledge")
    assert one["choice_help"] == components.choice_help("knowledge")
    edited = call(conn, "POST", "/api/projects/p1/cards/knowledge", {"settings": {"read": "always"}})["card"]
    assert edited["choice_help"] == components.choice_help("knowledge")
