"""A knowledge card holds many documents: the brief, the planner's context and files() work per
document; the old one-document shape still loads as a one-doc card."""
import json

from eki import components
from eki.components import knowledge


def _write(proj, rel, text="x\n"):
    p = proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _put(proj, name, spec):
    d = proj / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps(spec))


SHELF = {"kind": "knowledge", "about": "the shelf", "docs": [
    {"name": "decisions", "files": ["design/DECISIONS.md"], "about": "owner rules", "read": "always"},
    {"name": "story", "files": ["story/core.md", "story/voices"], "about": "writing dialogue", "read": "when-relevant"},
    {"name": "notes", "files": ["notes/*.md", "notes/gone.md"], "about": "playtests", "read": "when-relevant"},
]}


def _shelf(proj):
    for rel in ("design/DECISIONS.md", "story/core.md", "story/voices/ana.md", "notes/a.md"):
        _write(proj, rel)
    _put(proj, "knowledge", SHELF)


def _first(out):
    return [ln for ln in out.splitlines() if ln.startswith("- read first:")]


def test_full_brief_one_line_per_document(proj):
    _shelf(proj)
    assert knowledge.brief(str(proj)).splitlines()[1:] == [
        "- read first: design/DECISIONS.md — decisions: owner rules",
        "- look here when relevant: story/core.md, story/voices/ — story: writing dialogue",
        "- look here when relevant: notes/a.md — notes: playtests",
    ]


def test_context_by_document_name_narrows_the_brief(proj):
    _shelf(proj)
    out = knowledge.brief(str(proj), context=["story", "nope"])
    assert _first(out) == ["- read first: design/DECISIONS.md — decisions: owner rules",
                           "- read first: story/core.md, story/voices/ — story: writing dialogue"]
    assert out.splitlines()[-1] == "Also available if you need them: notes (see components/)."
    assert "look here when relevant" not in out


def test_an_old_card_name_still_resolves_to_all_its_docs(proj):
    _shelf(proj)
    out = knowledge.brief(str(proj), context=["knowledge"])
    assert len(_first(out)) == 3 and "Also available" not in out
    cards = [c for c in components.own(proj)[0].values() if c.kind == "knowledge"]
    assert [d["name"] for _, d in knowledge.resolve(cards, ["notes", "x"])] == ["notes"]
    assert [d["name"] for _, d in knowledge.resolve(cards, ["knowledge"])] == ["decisions", "story", "notes"]


def test_files_carry_the_doc_and_notes_count_documents(proj):
    _shelf(proj)
    comp = components.own(proj)[0]["knowledge"]
    fs = knowledge.files(comp)
    assert [(f["doc"], f["path"], f["missing"]) for f in fs] == [
        ("decisions", "design/DECISIONS.md", False), ("story", "story/core.md", False),
        ("story", "story/voices/ana.md", False), ("notes", "notes/a.md", False), ("notes", "notes/gone.md", True)]
    one = knowledge.doc_files(comp, knowledge.docs(comp)[1])
    assert [f["path"] for f in one] == ["story/core.md", "story/voices/ana.md"] and "doc" not in one[0]
    assert knowledge.notes(comp) == ["3 documents, 4 files, 1 missing: notes/gone.md"]


def test_old_single_doc_shape_loads_as_one_doc(proj):
    _write(proj, "design/DECISIONS.md")
    _put(proj, "decisions", {"kind": "knowledge", "about": "rules", "files": ["design/DECISIONS.md"],
                             "read": "always"})
    found, errors = components.own(proj)
    assert errors == []
    comp = found["decisions"]
    assert knowledge.docs(comp) == [{"name": "decisions", "files": ["design/DECISIONS.md"], "about": "rules",
                                     "read": "always"}]
    assert knowledge.brief(str(proj)).splitlines()[1:] == ["- read first: design/DECISIONS.md — rules"]
    assert knowledge.files(comp)[0]["doc"] == "decisions"
    assert knowledge.notes(comp) == ["1 file"]


def test_validation_names_the_document(tmp_path):
    def doc(name, read="always", files=("a.md",)):
        return {"name": name, "files": list(files), "about": "x", "read": read}
    ok = {"kind": "knowledge", "about": "s", "docs": [doc("a"), doc("b", "when-relevant")]}
    assert knowledge.validate(ok, tmp_path) == []
    dup = knowledge.validate({**ok, "docs": [doc("a"), doc("a")]}, tmp_path)
    assert dup == ["document a: the name is used twice in this card"]
    bad = knowledge.validate({**ok, "docs": [doc("a"), doc("b", "sometimes")]}, tmp_path)
    assert len(bad) == 1 and bad[0].startswith("document b: read must be one of always, when-relevant")
    assert knowledge.validate({**ok, "files": ["a.md"], "read": "always"}, tmp_path) == [
        'use either "docs" (a list of documents) or top-level "files", not both']
    assert knowledge.validate({**ok, "docs": []}, tmp_path)[0].startswith("docs must be a non-empty list")
    assert "document c: files" in knowledge.validate({**ok, "docs": [doc("c", files=["../x"])]}, tmp_path)[0]
    assert "name must be" in knowledge.validate({**ok, "docs": [doc("a b")]}, tmp_path)[0]


def test_a_broken_multi_doc_card_is_an_error_not_a_card(proj):
    _put(proj, "shelf", {"kind": "knowledge", "about": "s", "docs": [
        {"name": "a", "files": ["a.md"], "about": "x", "read": "always"},
        {"name": "a", "files": ["b.md"], "about": "y", "read": "always"}]})
    found, errors = components.own(proj)
    assert "shelf" not in found and any("used twice" in str(e) for e in errors)
