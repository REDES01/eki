"""Delete hides a chat at once; purge removes its rows once nothing runs; files stay."""
import pytest

from eki import asking, db, store, threadacts, threadkeep


def count(conn, table, tid):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id=?", (tid,)).fetchone()[0]


def test_delete_running_then_purge(conn, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    tid, rid = asking.submit(conn, "write a file", cwd=str(work))
    (work / "made.txt").write_text("kept")
    with db.tx(conn):
        store.update_run(conn, rid, state="running")
        store.add_event(conn, rid, 1, "text", {"text": "on it"})
        store.save_session(conn, tid, "claude", "sess-1", rid)
        conn.execute("INSERT INTO asks(id, run_id, thread_id, kind, payload, created_at) "
                      "VALUES ('a1', ?, ?, 'question', '{}', ?)", (rid, tid, db.now()))
    assert threadacts.delete(conn, tid) == 1
    assert store.run(conn, rid)["cancel"] == 1
    assert store.thread(conn, tid)["deleted_at"] is not None
    with pytest.raises(KeyError):
        threadacts.update(conn, tid, {"pin": True})
    assert threadacts.purge(conn) == []          # still running: the rows stay
    with db.tx(conn):
        store.update_run(conn, rid, state="cancelled", ended_at=db.now())
    said = threadkeep.tick(conn)
    assert any(tid in line for line in said)
    assert store.thread(conn, tid) is None
    for table in ("runs", "asks", "sessions"):
        assert count(conn, table, tid) == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (rid,)).fetchone()[0] == 0
    assert (work / "made.txt").read_text() == "kept"


def test_delete_ended_thread_purges_at_once(conn):
    tid, rid = asking.submit(conn, "hi")
    with db.tx(conn):
        store.update_run(conn, rid, state="done", ended_at=db.now())
    assert threadacts.delete(conn, tid) == 0
    assert store.thread(conn, tid) is None
    with pytest.raises(KeyError):
        threadacts.delete(conn, tid)


def test_eki_own_thread_is_refused(conn):
    with db.tx(conn):
        tid = store.create_thread(conn, "gate 2: x", None, by="eki")
    with pytest.raises(ValueError, match="eki's own thread"):
        threadacts.delete(conn, tid)
    assert store.thread(conn, tid)["deleted_at"] is None
