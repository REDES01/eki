"""Editing a card: settings merged and validated before writing, an agent's prompt, read-only cases."""
import json
import shutil

import pytest

from eki import apiroutes, components
from eki.api_settings import Invalid


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder) if folder else None))
    conn.commit()


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def card(conn, name):
    return {c["name"]: c for c in call(conn, "GET", "/api/projects/p1/cards")["cards"]}[name]


@pytest.fixture
def cards(conn, proj):
    project(conn, proj)
    base = proj / "components"
    shutil.copytree(components.templates_dir() / "voice", base / "voice")
    shutil.copytree(components.templates_dir() / "agent", base / "helper")
    return base


def test_valid_settings_written_and_shown(conn, cards):
    got = call(conn, "POST", "/api/projects/p1/cards/voice", {"settings": {"slots": 2, "about": "speaks"}})
    assert got["card"]["spec"]["slots"] == 2 and got["card"]["about"] == "speaks"
    text = (cards / "voice" / "component.json").read_text()
    assert text.endswith("}\n") and json.loads(text)["slots"] == 2 and '\n  "' in text
    assert card(conn, "voice")["spec"]["slots"] == 2 and card(conn, "voice")["error"] == ""
    call(conn, "POST", "/api/projects/p1/cards/voice", {"settings": {"slots": None}})
    assert "slots" not in json.loads((cards / "voice" / "component.json").read_text())


@pytest.mark.parametrize("settings,field", [({"slots": 0}, "slots"), ({"access": "sometimes"}, "access"),
                                            ({"kind": "agent"}, "kind"), ({"about": None}, "about")])
def test_invalid_settings_write_nothing(conn, cards, settings, field):
    f = cards / "voice" / "component.json"
    before = f.read_bytes()
    with pytest.raises(Invalid) as e:
        call(conn, "POST", "/api/projects/p1/cards/voice", {"settings": settings})
    assert field in e.value.problems and e.value.problems[field]
    assert f.read_bytes() == before


def test_agent_without_prompt_is_a_problem_beside_prompt(conn, cards):
    (cards / "helper" / "prompt.md").unlink()
    f = cards / "helper" / "component.json"
    before = f.read_bytes()
    assert card(conn, "helper")["error"] and card(conn, "helper")["editable"]
    with pytest.raises(Invalid) as e:
        call(conn, "POST", "/api/projects/p1/cards/helper", {"settings": {"bare": True}})
    assert "prompt.md" in e.value.problems["prompt"]
    assert f.read_bytes() == before


def test_agent_bare_switch_saves(conn, cards):
    for v in (True, False):
        got = call(conn, "POST", "/api/projects/p1/cards/helper", {"settings": {"bare": v}})
        assert got["card"]["spec"]["bare"] is v
        assert json.loads((cards / "helper" / "component.json").read_text())["bare"] is v
    with pytest.raises(Invalid) as e:
        call(conn, "POST", "/api/projects/p1/cards/helper", {"settings": {"bare": "yes"}})
    assert "bare" in e.value.problems


def test_prompt_edits_the_agents_prompt_file(conn, cards):
    got = call(conn, "POST", "/api/projects/p1/cards/helper", {"prompt": "You are terse.\n"})
    assert (cards / "helper" / "prompt.md").read_text() == "You are terse.\n"
    assert got["card"]["prompt"] == "You are terse.\n"
    with pytest.raises(ValueError, match="only an agent"):
        call(conn, "POST", "/api/projects/p1/cards/voice", {"prompt": "hi"})


def test_ref_card_and_unknown_card_refuse(conn, cards):
    pool = components.pool_dir() / "images"
    pool.mkdir(parents=True)
    (pool / "component.json").write_text(json.dumps({"kind": "service", "about": "x", "run": "echo"}))
    (cards / "images").mkdir()
    (cards / "images" / "component.json").write_text('{"pool": "images"}')
    with pytest.raises(ValueError, match="edit it in the pool"):
        call(conn, "POST", "/api/projects/p1/cards/images", {"settings": {"slots": 2}})
    assert (cards / "images" / "component.json").read_text() == '{"pool": "images"}'
    with pytest.raises(KeyError):
        call(conn, "POST", "/api/projects/p1/cards/nope", {"settings": {"slots": 2}})


def test_project_without_folder_refuses(conn):
    project(conn, None)
    with pytest.raises(ValueError, match="pick or clone a folder first"):
        call(conn, "POST", "/api/projects/p1/cards/voice", {"settings": {"slots": 2}})
