"""A title chore names each new chat of yours (eki/titles.py)."""
import json

import pytest

from eki import chores, db, store, titles

#: the fake stands in for the local model on chores
pytestmark = pytest.mark.usefixtures("text_fake")


def use_local(home, name="bare"):
    cfg = json.loads((home / "routing.json").read_text())
    cfg["self"] = {"local": name}
    (home / "routing.json").write_text(json.dumps(cfg))


def chat(conn, prompt="please fix the sidebar so it folds", answer="Done: the sidebar folds now.",
         by=None, title_by=None):
    with db.tx(conn):
        tid = store.create_thread(conn, prompt, None, by=by)
        rid = store.create_run(conn, tid, prompt)
        store.add_event(conn, rid, 1, "text", {"text": answer})
        store.update_run(conn, rid, state="done")
        if title_by:
            conn.execute("UPDATE threads SET title_by=? WHERE id=?", (title_by, tid))
    return tid


def finish(conn, rid, state="done", text=None):
    with db.tx(conn):
        if text:
            store.add_event(conn, rid, 1, "text", {"text": text})
        store.update_run(conn, rid, state=state, error=None if state == "done" else "it broke")


def title(conn, tid):
    row = store.thread(conn, tid)
    return row["title"], row["title_by"]


def test_a_new_chat_gets_a_cleaned_title_once(home, conn):
    use_local(home)
    tid = chat(conn)
    said = titles.start_due(conn)
    assert len(said) == 1
    c = chores.latest(conn, "title", tid)
    assert c["state"] == "open"
    run = store.run(conn, c["run_id"])
    assert run["row"] == "chore" and run["priority"] == "background"
    assert "please fix the sidebar" in run["prompt"] and "the sidebar folds now" in run["prompt"]
    assert "3–6 word title" in run["prompt"]
    assert titles.start_due(conn) == []                       # one try per thread
    assert titles.settle(conn) == []                          # still running
    finish(conn, c["run_id"], text='"Fixing The Sidebar."\nmore')
    assert titles.settle(conn)
    assert title(conn, tid) == ("Fixing The Sidebar", "eki")
    assert chores.latest(conn, "title", tid)["state"] == "done"
    assert titles.start_due(conn) == []                       # closed: still no second try


def test_a_title_you_set_while_the_chore_ran_wins(home, conn):
    use_local(home)
    tid = chat(conn)
    titles.start_due(conn)
    with db.tx(conn):
        conn.execute("UPDATE threads SET title='mine', title_by='you' WHERE id=?", (tid,))
    finish(conn, chores.latest(conn, "title", tid)["run_id"], text="Fixing The Sidebar")
    titles.settle(conn)
    assert title(conn, tid) == ("mine", "you")
    assert chores.latest(conn, "title", tid)["state"] == "done"


def test_a_failed_or_empty_chore_keeps_the_prompt_title(home, conn):
    use_local(home)
    failed, empty = chat(conn, "first chat here"), chat(conn, "second chat here")
    titles.start_due(conn)
    finish(conn, chores.latest(conn, "title", failed)["run_id"], state="failed")
    finish(conn, chores.latest(conn, "title", empty)["run_id"], text='  \n ""\n')
    titles.settle(conn)
    assert title(conn, failed) == ("first chat here", "prompt")
    assert title(conn, empty) == ("second chat here", "prompt")
    assert chores.latest(conn, "title", failed)["state"] == "failed"
    assert chores.latest(conn, "title", empty)["state"] == "failed"
    assert titles.start_due(conn) == []


def test_a_skipped_chore_keeps_the_prompt_title_and_counts_as_the_try(home, conn):
    use_local(home, "off")
    tid = chat(conn)
    titles.start_due(conn)
    assert chores.latest(conn, "title", tid)["state"] == "skipped"
    assert titles.settle(conn) == []
    assert title(conn, tid)[1] == "prompt"
    use_local(home)
    assert titles.start_due(conn) == []


def test_old_eki_own_unanswered_and_deleted_threads_get_no_chore(home, conn):
    use_local(home)
    old = chat(conn, title_by="old")
    own = chat(conn, by="eki")
    gone = chat(conn)
    with db.tx(conn):
        conn.execute("UPDATE threads SET deleted_at=? WHERE id=?", (db.now(), gone))
        quiet = store.create_thread(conn, "no answer yet", None)
        store.create_run(conn, quiet, "no answer yet")
    assert titles.start_due(conn) == []
    for tid in (old, own, gone, quiet):
        assert chores.latest(conn, "title", tid) is None


def test_at_most_three_per_pass(home, conn):
    use_local(home)
    tids = [chat(conn, f"chat number {i}") for i in range(5)]
    assert len(titles.start_due(conn)) == 3
    assert len(titles.start_due(conn)) == 2
    assert all(chores.latest(conn, "title", t) for t in tids)


@pytest.mark.parametrize("raw,want", [
    ('"Fixing The Sidebar."\nmore', "Fixing The Sidebar"),
    ("\n  “修复侧边栏”  \n", "修复侧边栏"),
    ("'Plan the trip'", "Plan the trip"),
    ("Wait for it...", "Wait for it..."),
    ("x" * 120, "x" * 80),
    ("", ""),
])
def test_clean(raw, want):
    assert titles.clean(raw) == want
