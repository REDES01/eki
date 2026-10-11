"""Every kind has a name and a plain explanation for people (TITLE, EXPLAIN in its module), and the cards
API carries them with each card's live state and overview."""
import json
import shutil
import types

import pytest

from eki import apiroutes, cardview, components
from eki.components import kindtext

NAMES = {"instructions": "Agent instructions", "knowledge": "Documents", "skills": "Skills", "service": "Tool",
         "mcp": "Tool connection", "agent": "Agent", "assets": "Media pool", "serve": "Preview links",
         "workflow": "Workflow"}
CODE_WORDS = ("static server", "worktree", "JSON", "spec")


@pytest.mark.parametrize("kind", sorted(components.KINDS))
def test_every_kind_has_a_title_and_an_explanation(kind):
    mod = components.kind_module(kind)
    title, explain = getattr(mod, "TITLE", ""), getattr(mod, "EXPLAIN", "")
    assert isinstance(title, str) and title.strip(), f"{kind} has no TITLE"
    assert isinstance(explain, str) and len(explain) >= 60, f"{kind} has no EXPLAIN of 60+ characters"
    assert not any(w in explain for w in CODE_WORDS), explain
    assert not any(w in getattr(mod, "ABOUT", "") for w in CODE_WORDS)
    assert kindtext.title(kind) == title and kindtext.explain(kind) == explain


def test_the_names():
    assert set(NAMES) == set(components.KINDS)
    assert {k: kindtext.title(k) for k in components.KINDS} == NAMES


def test_an_unknown_kind_never_raises():
    assert kindtext.title("nope") == "nope" and kindtext.explain("nope") == ""
    assert kindtext.title("") == "Card" and kindtext.explain("") == ""
    assert kindtext.title(None) == "Card" and kindtext.explain(None) == ""


def test_a_kind_without_words_fails(monkeypatch):
    bare = types.ModuleType("bare")
    monkeypatch.setattr(components, "kind_module", lambda kind: bare)
    assert kindtext.explain("serve") == "" and kindtext.title("serve") == "serve"


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder)))
    conn.commit()


def get(conn, path):
    fn, m = apiroutes.match("GET", path)
    return fn(conn, m, {}, {})


def test_cards_carry_title_explain_live_and_overview(conn, proj):
    project(conn, proj)
    base = proj / "components"
    shutil.copytree(components.templates_dir() / "voice", base / "voice")
    (base / "bad").mkdir(parents=True)
    (base / "bad" / "component.json").write_text('{"kind": "serve"')
    cards = {n: get(conn, f"/api/projects/p1/cards/{n}")["card"] for n in ("voice", "bad")}
    v = cards["voice"]
    assert v["title"] == "Tool" and v["explain"] == kindtext.explain("service")
    assert isinstance(v["live"], dict)
    assert v["overview"] == {"used_by": {"stages": [], "line": "not used yet", "runs": []}, "now": ["idle"]}
    b = cards["bad"]
    assert b["title"] == "Card" and b["explain"] == "" and b["live"] == {}
    assert b["overview"]["used_by"] == {"stages": [], "line": "", "runs": []}
    assert len(b["overview"]["now"]) == 1 and b["overview"]["now"][0].startswith("broken: ")
    json.dumps(cards)


def test_a_kinds_live_is_used_and_its_trouble_is_empty(conn, proj, monkeypatch):
    project(conn, proj)
    shutil.copytree(components.templates_dir() / "voice", proj / "components" / "voice")
    comp = components.own(proj)[0]["voice"]
    mod = components.kind_module("service")
    monkeypatch.setattr(mod, "live", lambda c, conn_, root: {"busy": c.name}, raising=False)
    assert cardview.live(conn, proj, comp) == {"busy": "voice"}
    assert get(conn, "/api/projects/p1/cards/voice")["card"]["live"] == {"busy": "voice"}

    def boom(c, conn_, root):
        raise RuntimeError("no")
    monkeypatch.setattr(mod, "live", boom)
    assert cardview.live(conn, proj, comp) == {}
    monkeypatch.setattr(mod, "live", lambda c, conn_, root: "not a dict")
    assert cardview.live(conn, proj, comp) == {}


def test_the_library_carries_titles(conn):
    got = get(conn, "/api/cards/library")
    by = {t["name"]: t for t in got["templates"]}
    assert by["serve"]["title"] == "Preview links" and by["playtester"]["title"] == "Agent"
    assert all("title" in t for t in got["templates"])


def test_template_about_lines_are_for_people():
    serve = json.loads((components.templates_dir() / "serve" / "component.json").read_text())
    assert "static server" not in serve["about"] and "worktree" not in serve["about"]
