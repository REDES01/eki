"""The planner picks knowledge per document: the plan brief lists every document by name, a
plan answer's context names documents, and the item's build brief reads those first. Old card
names (from older plans) still resolve."""
import json

from eki import projectbrief, projects, selfbrief, selfview, selfwork
from eki.components import knowledge

SHELF = {"kind": "knowledge", "about": "the shelf", "docs": [
    {"name": "decisions", "files": ["design/DECISIONS.md"], "about": "owner rules", "read": "always"},
    {"name": "story", "files": ["story/core.md"], "about": "writing dialogue", "read": "when-relevant"},
    {"name": "levels", "files": ["design/levels.md"], "about": "level layout", "read": "when-relevant"},
]}
OLD = {"kind": "knowledge", "about": "playtest notes", "files": ["notes/play.md"], "read": "when-relevant"}


def _write(proj, rel, text="x\n"):
    p = proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _cards(proj):
    for rel in ("design/DECISIONS.md", "story/core.md", "design/levels.md", "notes/play.md"):
        _write(proj, rel)
    for name, spec in (("knowledge", SHELF), ("playtests", OLD)):
        _write(proj, f"components/{name}/component.json", json.dumps(spec))


def _first(out):
    return [ln for ln in out.splitlines() if ln.startswith("- read first:")]


def test_plan_lists_every_document_with_its_read_mode(conn, proj):
    _cards(proj)
    text = projectbrief.plan("fix it", "0" * 40, projects.get(conn, projects.add(conn, proj)))
    assert "- decisions (read: always; card knowledge): owner rules — files: design/DECISIONS.md" in text
    assert "- story (read: when-relevant; card knowledge): writing dialogue — files: story/core.md" in text
    assert "- levels (read: when-relevant; card knowledge): level layout — files: design/levels.md" in text
    assert "- playtests (read: when-relevant; card playtests): playtest notes — files: notes/play.md" in text
    assert "knowledge documents" in text and "by document\nname" in text
    assert projectbrief.CONTEXT_KEY.strip(",\n ") in text


def test_planned_context_reaches_the_build_brief(conn, proj):
    _cards(proj)
    projects.add(conn, proj)
    answer = ('ok\nITEMS:\n[{"title": "t", "spec": "s", "files": ["a.py"], "deps": [], "independent": false,'
              ' "context": [{"name": "story", "why": "dialogue"}, {"name": "playtests", "why": "old name"}]}]')
    it = selfbrief.items_in(answer)[0]
    conn.execute("INSERT INTO goals(id, text, state, created_at) VALUES ('g1','g','planned',0)")
    iid = selfwork.new_item(conn, "g1", it["title"], it["spec"], it["files"], [], False, context=it["context"])
    row = conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    assert json.loads(row["context"]) == [{"name": "story", "why": "dialogue"},
                                          {"name": "playtests", "why": "old name"}]
    assert [x["name"] for x in selfview.context(row)] == ["story", "playtests"]

    out = knowledge.brief(str(proj), conn, context=knowledge.names(row["context"]))
    assert _first(out) == ["- read first: design/DECISIONS.md — decisions: owner rules",
                           "- read first: story/core.md — story: writing dialogue",
                           "- read first: notes/play.md — playtest notes"]
    assert out.splitlines()[-1] == "Also available if you need them: levels (see components/)."


def test_an_old_card_name_in_context_still_resolves(conn, proj):
    _cards(proj)
    projects.add(conn, proj)
    out = knowledge.brief(str(proj), conn, context=["knowledge"])
    assert len(_first(out)) == 3
    assert out.splitlines()[-1] == "Also available if you need them: playtests (see components/)."
