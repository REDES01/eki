"""Thread names: who made a thread, how it was titled, and the backfill of eki's own."""
from eki import asking, db, housekeep, store, threadkeep


def by_of(conn, tid):
    return conn.execute("SELECT by FROM threads WHERE id=?", (tid,)).fetchone()["by"]


def test_create_thread_writes_by_title_by_and_seen_at(conn):
    mine = store.create_thread(conn, "hello", None)
    own = store.create_thread(conn, "gate 2: x", None, by="eki")
    row = conn.execute("SELECT * FROM threads WHERE id=?", (mine,)).fetchone()
    assert row["by"] is None and row["title_by"] == "prompt"
    assert row["seen_at"] == row["created_at"] and row["unread"] == 0
    assert by_of(conn, own) == "eki"


def test_an_old_row_reads_title_by_old(conn):
    conn.execute("INSERT INTO threads(id, title, created_at) VALUES ('old1', 't', 1)")
    row = conn.execute("SELECT * FROM threads WHERE id='old1'").fetchone()
    assert row["title_by"] == "old" and row["by"] is None and row["seen_at"] is None
    assert row["pinned_at"] is None and row["archived_at"] is None and row["deleted_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM chat_groups").fetchone()[0] == 0


def old_thread(conn, title="t"):
    """A thread as made before `by` existed."""
    tid = store.new_id()
    conn.execute("INSERT INTO threads(id, title, created_at) VALUES (?,?,?)", (tid, title, db.now()))
    return tid


def test_backfill_marks_eki_threads_and_leaves_yours(conn):
    chore = old_thread(conn)
    conn.execute("INSERT INTO chores(id, kind, subject, thread_id, created_at) VALUES ('c1','review','i',?,1)",
                 (chore,))
    command = old_thread(conn)
    store.create_run(conn, command, "bin/check", provider="command")
    gate2 = old_thread(conn)
    rid = store.create_run(conn, gate2, "check")
    conn.execute("INSERT INTO items(id, goal_id, title, gate2_run, created_at, updated_at) "
                 "VALUES ('i1','g1','x',?,1,1)", (rid,))
    titled = old_thread(conn, "gate 2: x")
    plain = old_thread(conn, "just a chat")
    yours, _ = asking.submit(conn, "hello there")
    assert threadkeep.backfill(conn) == 4
    for tid in (chore, command, gate2, titled):
        assert by_of(conn, tid) == "eki"
    assert by_of(conn, plain) is None and by_of(conn, yours) is None
    assert threadkeep.backfill(conn) == 0


def test_tick_is_a_housekeeping_step(conn):
    assert "threads" in [name for name, _ in housekeep.STEPS]
    old_thread(conn, "train check: build abc")
    assert threadkeep.tick(conn) == ["threads: marked 1 as eki's own"]
    assert threadkeep.tick(conn) == []
