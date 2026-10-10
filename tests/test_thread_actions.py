"""Thread actions on the columns: pin, unread, rename, group, archive (eki/threadacts.py)."""
import pytest

from eki import asking, db, store, threadacts


def row(conn, tid):
    return conn.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()


@pytest.fixture
def tid(conn):
    with db.tx(conn):
        return store.create_thread(conn, "hello there", None)


def test_pin_sets_and_clears(conn, tid):
    threadacts.update(conn, tid, {"pin": True})
    assert row(conn, tid)["pinned_at"] is not None
    threadacts.update(conn, tid, {"pin": False})
    assert row(conn, tid)["pinned_at"] is None


def test_unread_true_and_false(conn, tid):
    seen = row(conn, tid)["seen_at"]
    threadacts.update(conn, tid, {"unread": True})
    assert row(conn, tid)["unread"] == 1
    threadacts.update(conn, tid, {"unread": False})
    t = row(conn, tid)
    assert t["unread"] == 0 and t["seen_at"] >= seen


def test_archive_and_a_new_message_unarchives(conn, tid):
    threadacts.update(conn, tid, {"archive": True})
    assert row(conn, tid)["archived_at"] is not None
    asking.submit(conn, "one more thing", thread=tid)
    assert row(conn, tid)["archived_at"] is None
    threadacts.update(conn, tid, {"archive": True})
    threadacts.update(conn, tid, {"archive": False})
    assert row(conn, tid)["archived_at"] is None


def test_group_set_cleared_and_unknown(conn, tid):
    g = threadacts.create_group(conn, "  work ")
    assert g["name"] == "work"
    threadacts.update(conn, tid, {"group": g["id"]})
    assert row(conn, tid)["chat_group"] == g["id"]
    threadacts.update(conn, tid, {"group": None})
    assert row(conn, tid)["chat_group"] is None
    with pytest.raises(KeyError):
        threadacts.update(conn, tid, {"group": "nope"})


def test_create_group_ord_increases(conn):
    a = threadacts.create_group(conn, "a")
    b = threadacts.create_group(conn, "b")
    ords = dict(conn.execute("SELECT id, ord FROM chat_groups").fetchall())
    assert ords[a["id"]] == 1 and ords[b["id"]] == 2
    with pytest.raises(ValueError):
        threadacts.create_group(conn, "  ")


def test_rename(conn, tid):
    threadacts.update(conn, tid, {"title": "  " + "x" * 100 + " "})
    t = row(conn, tid)
    assert t["title"] == "x" * 80 and t["title_by"] == "you"
    with pytest.raises(ValueError):
        threadacts.update(conn, tid, {"title": "   "})
    assert row(conn, tid)["title"] == "x" * 80


def test_unknown_thread_is_keyerror(conn):
    with pytest.raises(KeyError):
        threadacts.update(conn, "nothere", {"pin": True})
