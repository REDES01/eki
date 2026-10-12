"""Every run of a planned item — build, rebuild, resolve, judge — gets the item's chosen context."""
import json

from eki import components, db, judge, store, worker
from eki.components import knowledge
from tests.test_item_context import _cards, _first

ARCH = json.dumps([{"name": "architecture", "why": "x"}])
PICKED = ["- read first: k/decisions.md — about decisions",
          "- read first: k/architecture.md — about architecture",
          "- read first: AGENTS.md — how to work here"]


def _item(conn, proj, iid, context):
    tid = store.create_thread(conn, f"item {iid}", str(proj), by="eki")
    conn.execute("INSERT INTO items(id, goal_id, title, spec, thread_id, worktree, context, created_at, updated_at) "
                 "VALUES (?, 'g', 'fix the road', 'the spec', ?, ?, ?, ?, ?)",
                 (iid, tid, str(proj), context, db.now(), db.now()))
    return tid


def _note(conn, tid, prompt="build it", row=None):
    turn = worker.build_turn(conn, store.run(conn, store.create_run(conn, tid, prompt, row=row)), "fake")
    return turn.extra.get("note") or ""


def test_a_build_reads_its_chosen_cards_and_names_the_rest(conn, proj):
    _cards(proj)
    tid = _item(conn, proj, "i1", ARCH)
    note = _note(conn, tid)
    assert _first(note) == PICKED
    also = [ln for ln in note.splitlines() if ln.startswith("Also available")]
    assert also == ["Also available if you need them: story-core, voices (see components/)."]
    assert "k/voices.md" not in note and "k/story-core.md" not in note


def test_resolve_and_rebuild_in_the_same_thread_get_the_same_note(conn, proj):
    _cards(proj)
    tid = _item(conn, proj, "i1", ARCH)
    first = _note(conn, tid)
    assert _note(conn, tid, "resolve the conflict") == first
    assert _note(conn, tid, "the checks failed; fix it") == first


def test_the_judge_prompt_carries_the_same_brief(conn, proj):
    _cards(proj)
    _item(conn, proj, "i1", ARCH)
    it = conn.execute("SELECT * FROM items WHERE id='i1'").fetchone()
    out = judge.prompt(conn, it, "item", [("spec", judge.SPEC_TEXT)])
    told = knowledge.brief(str(proj), conn, context=["architecture"])
    assert told and f"What the builder was told to read:\n\n{told}\n" in out
    assert out.index("the spec") < out.index(told) < out.index("The checks to judge")


def test_an_item_without_context_gets_the_full_brief(conn, proj):
    _cards(proj)
    tid = _item(conn, proj, "i1", None)
    note = _note(conn, tid)
    assert knowledge.brief(str(proj), conn) in note
    assert "k/voices.md" in note and "Also available" not in note
    it = conn.execute("SELECT * FROM items WHERE id='i1'").fetchone()
    assert knowledge.brief(str(proj), conn) in judge.prompt(conn, it, "item", [("spec", judge.SPEC_TEXT)])


def test_a_chat_in_the_project_gets_todays_note(conn, proj):
    _cards(proj)
    _item(conn, proj, "i1", ARCH)
    tid = store.create_thread(conn, "a chat", str(proj))
    want = "\n\n".join(x for x in (components.brief(str(proj), conn), knowledge.brief(str(proj), conn)) if x)
    assert _note(conn, tid, "hi") == want
    assert "Also available" not in want


def test_a_judge_prompt_without_cards_is_unchanged(conn, tmp_path):
    conn.execute("INSERT INTO items(id, goal_id, title, worktree, created_at, updated_at) "
                 "VALUES ('e1', 'g', 't', ?, ?, ?)", (str(tmp_path), db.now(), db.now()))
    out = judge.prompt(conn, conn.execute("SELECT * FROM items WHERE id='e1'").fetchone(), "item", [])
    assert "What the builder was told to read" not in out
