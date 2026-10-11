"""Free-text settings a kind explains: components.field_help(kind) is the kind module's FIELD_HELP (or {}),
the card JSON carries it as field_help (eki/api_cards.py), and the open card (projectcardpanel.js) shows
each such key as a text field with its line under it, even when the spec lacks it."""
import json
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from eki import apiroutes, components
from test_web_cardworkflow import PANEL_STUB as WORKFLOW_STUB

# a serve card has no part of its own: no .pp-kind to read
PANEL_STUB = WORKFLOW_STUB.replace('kind: box.querySelector(".pp-kind").innerHTML', 'kind: ""')
assert PANEL_STUB != WORKFLOW_STUB
WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def _fake(monkeypatch, kind, module):
    real = components.kind_module
    monkeypatch.setattr(components, "kind_module", lambda k: module if k == kind else real(k))


def test_field_help_is_empty_for_a_kind_without_it(monkeypatch):
    _fake(monkeypatch, "knowledge", types.SimpleNamespace())
    assert components.field_help("knowledge") == {}
    assert components.field_help("no-such-kind") == {}


def test_field_help_is_the_kinds_dict(monkeypatch):
    _fake(monkeypatch, "serve", types.SimpleNamespace(FIELD_HELP={"build": "a command run before serving"}))
    assert components.field_help("serve") == {"build": "a command run before serving"}


def test_field_help_never_raises(monkeypatch):
    def boom(kind):
        raise ImportError("broken")
    monkeypatch.setattr(components, "kind_module", boom)
    assert components.field_help("serve") == {}
    _fake(monkeypatch, "serve", types.SimpleNamespace(FIELD_HELP=["not", "a", "dict"]))
    assert components.field_help("serve") == {}


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def test_the_card_payload_carries_field_help(conn, proj, monkeypatch):
    _fake(monkeypatch, "knowledge", types.SimpleNamespace(**{k: getattr(components.kind_module("knowledge"), k)
                                                             for k in dir(components.kind_module("knowledge"))
                                                             if not k.startswith("__")},
                                                          FIELD_HELP={"extra": "one line"}))
    shutil.copytree(components.templates_dir() / "knowledge", proj / "components" / "notes")
    (proj / "components" / "broken").mkdir()
    (proj / "components" / "broken" / "component.json").write_text('{"kind": "knowledge"}')
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 ("game", "game", str(proj), "trunk", 0, str(proj)))
    conn.commit()
    cards = {c["name"]: c for c in call(conn, "GET", "/api/projects/game/cards")["cards"]}
    assert cards["notes"]["field_help"] == {"extra": "one line"}
    assert cards["broken"]["error"] and cards["broken"]["field_help"] == {"extra": "one line"}


def test_the_panel_shows_a_field_help_key_as_a_text_field_with_its_line(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "panel.js"
    stub.write_text(PANEL_STUB)
    c = {"name": "play", "kind": "serve", "editable": True, "locked": [],
         "spec": {"kind": "serve", "about": "serves the game"},
         "choices": components.choices("serve"), "choice_defaults": components.choice_defaults("serve"),
         "choice_help": components.choice_help("serve"),
         "field_help": {"build": "a shell command run in the folder before serving; empty: none"}}
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "cardworkflow.js"), str(WEB / "projectcardpanel.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    settings = json.loads(got.stdout)["settings"]
    assert 'data-key="build" data-type="text" value=""' in settings
    assert 'class="dim pp-help">a shell command run in the folder before serving; empty: none' in settings
    c["spec"]["build"] = "npm run build"
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "cardworkflow.js"), str(WEB / "projectcardpanel.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    settings = json.loads(got.stdout)["settings"]
    assert settings.count('data-key="build"') == 1 and 'value="npm run build"' in settings
