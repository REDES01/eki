"""The planner picks each item's knowledge cards; the brief of a planned item follows them."""
import json

from eki import selfbrief, selfwork
from eki.components import knowledge
from tests.test_plangraph import _plan, _src

CARDS = {"decisions": "always", "architecture": "when-relevant", "voices": "when-relevant",
         "story-core": "when-relevant"}


def _cards(proj):
    for name, read in CARDS.items():
        (proj / "k").mkdir(exist_ok=True)
        (proj / "k" / f"{name}.md").write_text("x\n")
        d = proj / "components" / name
        d.mkdir(parents=True)
        (d / "component.json").write_text(json.dumps({"kind": "knowledge", "about": f"about {name}",
                                                      "files": [f"k/{name}.md"], "read": read}))
    d = proj / "components" / "agents"
    d.mkdir()
    (d / "component.json").write_text(json.dumps({"kind": "instructions", "about": "how to work here", "file": "AGENTS.md"}))
    (proj / "AGENTS.md").write_text("# rules\n")


def _first(out):
    return [ln for ln in out.splitlines() if ln.startswith("- read first:")]


def test_items_in_parses_context_in_both_forms():
    got = selfbrief.items_in('ITEMS:\n[{"title": "a", "context": [{"name": " architecture ", "why": " the module "},'
                             ' "voices", {"name": "", "why": "x"}, "voices", {"name": "y", "why": "' + "w" * 300 + '"}]},'
                             ' {"title": "b"}, {"title": "c", "context": []}, {"title": "d", "context": "voices"}]')
    assert got[0]["context"] == [{"name": "architecture", "why": "the module"}, {"name": "voices", "why": ""},
                                 {"name": "y", "why": "w" * 200}]
    assert got[1]["context"] is None
    assert got[2]["context"] == []
    assert got[3]["context"] is None


def test_conclude_plan_stores_the_context(conn, tmp_path, monkeypatch):
    _src(tmp_path, monkeypatch)
    gid = selfwork.submit(conn, "two things")
    _plan(conn, tmp_path, monkeypatch, gid,
          'ITEMS:\n[{"title": "a", "files": ["eki/a.py"], "context": [{"name": "voices", "why": "dialogue"}]},'
          ' {"title": "b", "files": ["eki/b.py"]}]\n')
    rows = {r["title"]: r["context"] for r in conn.execute("SELECT title, context FROM items WHERE goal_id=?", (gid,))}
    assert json.loads(rows["a"]) == [{"name": "voices", "why": "dialogue"}]
    assert rows["b"] is None
    assert knowledge.names(rows["a"]) == ["voices"]
    assert knowledge.names(rows["b"]) is None
    assert knowledge.names("not json") is None


def test_a_planned_brief_carries_only_its_cards(proj):
    _cards(proj)
    out = knowledge.brief(str(proj), context=["architecture", "nope"])
    assert out.splitlines()[0].startswith("Shared knowledge of this project")
    assert _first(out) == ["- read first: k/decisions.md — about decisions",
                           "- read first: k/architecture.md — about architecture",
                           "- read first: AGENTS.md — how to work here"]
    assert out.splitlines()[-1] == "Also available if you need them: story-core, voices (see components/)."
    assert "look here when relevant" not in out


def test_an_empty_context_keeps_always_cards_and_instructions(proj):
    _cards(proj)
    out = knowledge.brief(str(proj), context=[])
    assert [ln.split(": ", 1)[1].split(" — ")[0] for ln in _first(out)] == ["k/decisions.md", "AGENTS.md"]
    assert "architecture, story-core, voices" in out


def test_all_picked_has_no_available_line_and_none_is_the_full_brief(proj):
    _cards(proj)
    assert "Also available" not in knowledge.brief(str(proj), context=list(CARDS))
    full = knowledge.brief(str(proj))
    assert knowledge.brief(str(proj), context=None) == full
    assert "AGENTS.md" not in full and "Also available" not in full
    assert full.count("look here when relevant") == 3


def test_a_stored_context_can_name_documents_of_one_shelf(proj):
    (proj / "k").mkdir()
    for n in ("rules", "voices", "map"):
        (proj / "k" / f"{n}.md").write_text("x\n")
    d = proj / "components" / "shelf"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "knowledge", "about": "the shelf", "docs": [
        {"name": n, "files": [f"k/{n}.md"], "about": f"about {n}", "read": "always" if n == "rules" else "when-relevant"}
        for n in ("rules", "voices", "map")]}))
    picked = knowledge.names(json.dumps([{"name": "voices", "why": "dialogue"}]))
    out = knowledge.brief(str(proj), context=picked)
    assert _first(out) == ["- read first: k/rules.md — rules: about rules", "- read first: k/voices.md — voices: about voices"]
    assert out.splitlines()[-1] == "Also available if you need them: map (see components/)."
