"""The chat list: yours, eki's that need you, pinned, unread, archived, deleted, groups, sessions."""
import json

import pytest

from eki import api, api_threads, asks, db, store


def ids(conn, **kw):
    return [t["id"] for t in api_threads.threads(conn, **kw)]


def chat(conn, title="hi", at=None, by=None):
    tid = store.create_thread(conn, title, None, by=by)
    rid = store.create_run(conn, tid, title)
    t = at if at is not None else db.now()
    conn.execute("UPDATE runs SET created_at=?, state='done', ended_at=? WHERE id=?", (t, t, rid))
    conn.execute("UPDATE threads SET seen_at=? WHERE id=?", (t, tid))
    return tid


def test_eki_threads_show_only_with_all_or_an_open_ask(conn):
    mine = chat(conn, "mine", at=10)
    own = chat(conn, "gate 2: x", at=20, by="eki")
    assert ids(conn) == [mine]
    assert ids(conn, all=True) == [own, mine]
    rid = store.thread_runs(conn, own)[0]["id"]
    aid = asks.create(conn, rid, own, "question", {"question": "go?"})
    listed = api_threads.threads(conn)
    assert [t["id"] for t in listed] == [own, mine]
    assert listed[0]["own"] and listed[0]["needs_you"] and not listed[1]["own"]
    asks.answer(conn, aid, {"answer": "yes"})
    assert ids(conn) == [mine]


def test_pinned_first_in_pinned_order_and_kept_beyond_limit(conn):
    a, b, c, d = (chat(conn, n, at=i) for i, n in enumerate("abcd"))
    conn.execute("UPDATE threads SET pinned_at=1 WHERE id=?", (a,))
    conn.execute("UPDATE threads SET pinned_at=2 WHERE id=?", (b,))
    assert ids(conn) == [b, a, d, c]
    assert ids(conn, limit=1) == [b, a]
    assert ids(conn, limit=3) == [b, a, d]
    assert api_threads.entry(conn, b)["pinned"] and not api_threads.entry(conn, d)["pinned"]


def test_unread(conn):
    marked, later, old = chat(conn, at=10), chat(conn, at=10), chat(conn, at=10)
    conn.execute("UPDATE threads SET unread=1 WHERE id=?", (marked,))
    conn.execute("UPDATE threads SET seen_at=5 WHERE id=?", (later,))
    conn.execute("UPDATE threads SET seen_at=NULL WHERE id=?", (old,))
    got = {t["id"]: t["unread"] for t in api_threads.threads(conn)}
    assert got == {marked: True, later: True, old: False}


def test_archived_leaves_the_list(conn):
    kept, gone = chat(conn, at=1), chat(conn, at=2)
    conn.execute("UPDATE threads SET archived_at=3, pinned_at=3 WHERE id=?", (gone,))
    assert ids(conn) == [kept]
    assert ids(conn, archived=True) == [gone]
    assert api_threads.entry(conn, gone)["archived"]


def test_deleted_is_gone_everywhere(conn):
    kept, gone = chat(conn), chat(conn)
    conn.execute("UPDATE threads SET deleted_at=1 WHERE id=?", (gone,))
    assert ids(conn) == [kept] and ids(conn, all=True) == [kept]
    with pytest.raises(KeyError):
        api.thread(conn, gone)
    with pytest.raises(KeyError):
        api_threads.entry(conn, gone)
    assert api.thread(conn, kept)["id"] == kept


def test_entry_carries_group_and_sessions(conn, home):
    (home / "providers.json").write_text(json.dumps({"fake": {"kind": "fake"}, "claude": {"kind": "claude_code"}}))
    tid = chat(conn)
    conn.execute("INSERT INTO chat_groups(id, name, ord, created_at) VALUES ('g1', 'Work', 1, 1)")
    conn.execute("UPDATE threads SET chat_group='g1' WHERE id=?", (tid,))
    conn.execute("INSERT INTO sessions(thread_id, provider, session_id) VALUES (?, 'claude', 'abc')", (tid,))
    conn.execute("INSERT INTO sessions(thread_id, provider, session_id) VALUES (?, 'fake', 'zzz')", (tid,))
    e = api_threads.entry(conn, tid)
    assert e["group"] == {"id": "g1", "name": "Work"}
    assert e["sessions"] == [{"kind": "claude_code", "label": "Claude Code", "id": "abc"}]
    assert e["title_by"] == "prompt" and e["project"] is None and not e["own"]
    assert api.threads(conn)[0]["group"] == {"id": "g1", "name": "Work"}


def test_groups_follow_ord(conn):
    conn.execute("INSERT INTO chat_groups(id, name, ord, created_at) VALUES ('b', 'Later', 2, 1)")
    conn.execute("INSERT INTO chat_groups(id, name, ord, created_at) VALUES ('a', 'First', 1, 2)")
    assert api_threads.groups(conn) == [{"id": "a", "name": "First"}, {"id": "b", "name": "Later"}]


def test_an_unmarked_thread_with_an_eki_title_stays_out_until_it_needs_you(conn):
    mine = chat(conn, "mine", at=10)
    own = chat(conn, "chore: review abc", at=20)
    assert ids(conn) == [mine] and ids(conn, all=True) == [own, mine]
    asks.create(conn, store.thread_runs(conn, own)[0]["id"], own, "question", {"question": "go?"})
    assert ids(conn) == [own, mine]
