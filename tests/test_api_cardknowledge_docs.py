"""A knowledge card holding many documents on the project page: GET …/files answers each document with
its files, total size and newest change; any file of any document reads and writes; an old one-document
card answers one document named after the card; and saving the docs list turns the old shape into the
new one (top-level files and read removed)."""
import json

import pytest

from eki import api_cardedit
from eki.api_settings import Invalid
from test_api_cardknowledge import call, knowledge_card, project

URL = "/api/projects/p1/cards/knowledge"
DOCS = [{"name": "decisions", "files": ["design/DECISIONS.md"], "about": "what we decided", "read": "always"},
        {"name": "notes", "files": ["notes/*.md", "notes/GONE.md"], "about": "loose notes", "read": "when-relevant"}]


@pytest.fixture
def shelf(conn, proj):
    project(conn, proj)
    (proj / "design").mkdir()
    (proj / "design" / "DECISIONS.md").write_text("# Decisions\n")
    (proj / "notes").mkdir()
    (proj / "notes" / "a.md").write_text("a\n")
    (proj / "notes" / "b.md").write_text("bbb\n")
    (proj / "components" / "knowledge").mkdir(parents=True)
    (proj / "components" / "knowledge" / "component.json").write_text(json.dumps(
        {"kind": "knowledge", "about": "the shelf", "docs": DOCS}))
    return proj


def test_files_answers_each_document(conn, shelf):
    got = call(conn, "GET", URL + "/files")
    assert [d["name"] for d in got["docs"]] == ["decisions", "notes"]
    dec, notes = got["docs"]
    assert dec["read"] == "always" and dec["about"] == "what we decided"
    assert [f["path"] for f in dec["files"]] == ["design/DECISIONS.md"] and dec["size"] == len("# Decisions\n")
    assert [f["path"] for f in notes["files"]] == ["notes/a.md", "notes/b.md", "notes/GONE.md"]
    assert notes["files"][2]["missing"] and notes["size"] == 2 + 4
    assert notes["changed"] == max(f["changed"] for f in notes["files"][:2])
    assert {f["path"]: f["doc"] for f in got["files"]}["notes/b.md"] == "notes"


def test_a_file_of_the_second_document_reads_and_writes(conn, shelf):
    assert call(conn, "GET", URL + "/file", q={"path": "notes/b.md"})["text"] == "bbb\n"
    call(conn, "POST", URL + "/file", {"path": "notes/b.md", "text": "new\n"})
    assert (shelf / "notes" / "b.md").read_text() == "new\n"
    out = call(conn, "POST", URL + "/file", {"path": "notes/GONE.md", "text": "back\n"})
    assert not out["missing"] and (shelf / "notes" / "GONE.md").read_text() == "back\n"
    with pytest.raises(ValueError):
        call(conn, "GET", URL + "/file", q={"path": "app.py"})


def test_an_old_shape_card_is_one_document_named_after_it(conn, shelf):
    knowledge_card(shelf / "components", "rules", ["design/DECISIONS.md"], read="when-relevant")
    got = call(conn, "GET", "/api/projects/p1/cards/rules/files")
    assert len(got["docs"]) == 1
    doc = got["docs"][0]
    assert doc["name"] == "rules" and doc["read"] == "when-relevant" and doc["about"] == "decisions"
    assert [f["path"] for f in doc["files"]] == ["design/DECISIONS.md"] and doc["size"] == len("# Decisions\n")


def test_saving_docs_turns_the_old_shape_into_the_new(conn, shelf):
    knowledge_card(shelf / "components", "rules", ["design/DECISIONS.md"])
    docs = [{"name": "rules", "files": ["design/DECISIONS.md"], "about": "decisions", "read": "always"},
            {"name": "notes", "files": ["notes/*.md"], "about": "notes", "read": "when-relevant"}]
    out = call(conn, "POST", "/api/projects/p1/cards/rules", {"settings": {"docs": docs, "files": None, "read": None}})
    spec = json.loads((shelf / "components" / "rules" / "component.json").read_text())
    assert spec == {"kind": "knowledge", "about": "decisions", "docs": docs}
    assert out["card"]["kind"] == "knowledge" and not out["card"]["error"]
    got = call(conn, "GET", "/api/projects/p1/cards/rules/files")
    assert [d["name"] for d in got["docs"]] == ["rules", "notes"]


def test_a_bad_document_writes_nothing_and_says_which(conn, shelf):
    f = shelf / "components" / "knowledge" / "component.json"
    before = f.read_text()
    bad = DOCS + [{"name": "extra", "files": [], "about": "", "read": "always"}]
    with pytest.raises(Invalid) as e:
        api_cardedit.save_settings(f.parent, {"docs": bad, "files": None, "read": None})
    assert "document extra:" in "; ".join(e.value.problems.values())
    assert f.read_text() == before
