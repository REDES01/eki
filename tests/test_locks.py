import sqlite3

from eki import db, locks, paths

NEW = ["queued_at", "head", "rebased", "gate2_run", "gate2_on", "gate2",
       "landed_at", "build", "locked"]


def test_locked_in_picks_out_locked_files():
    files = ["eki/web.py", "./bin/check", "eki/providers/base.py", "README.md"]
    assert locks.locked_in(files) == ["bin/check", "eki/providers/base.py"]


def test_locked_in_ordinary_files():
    assert locks.locked_in(["eki/web.py", "tests/test_web.py"]) == []
    assert locks.locked_in([]) == []


def test_locked_in_glob():
    # fnmatch lets * cross /, so a wide glob errs on the locked side
    assert locks.locked_in(["eki/*.py"]) == ["eki/drill.py", "eki/launchd.py",
                                          "eki/providers/base.py", "eki/quota.py"]
    assert locks.locked_in(["./bin/*"]) == ["bin/check", "bin/eki-launcher"]


def _cols(conn):
    return {r["name"] for r in conn.execute("PRAGMA table_info(items)")}


def test_old_items_table_gains_columns(home):
    old = sqlite3.connect(paths.db())
    old.execute("""CREATE TABLE items (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, title TEXT NOT NULL,
        spec TEXT NOT NULL DEFAULT '', files TEXT NOT NULL DEFAULT '[]',
        deps TEXT NOT NULL DEFAULT '[]', independent INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'waiting', thread_id TEXT, worktree TEXT,
        branch TEXT, base TEXT, commit_sha TEXT, touched TEXT NOT NULL DEFAULT '[]',
        run_id TEXT, tries INTEGER NOT NULL DEFAULT 0, summary TEXT, verdict TEXT,
        error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    old.execute("INSERT INTO items (id, goal_id, title, created_at, updated_at) "
                "VALUES ('i1', 'g1', 'old', 0, 0)")
    old.commit()
    old.close()
    conn = db.connect()
    assert set(NEW) <= _cols(conn)
    row = conn.execute("SELECT * FROM items WHERE id='i1'").fetchone()
    assert all(row[c] is None for c in NEW)


def test_fresh_db_has_columns(conn):
    assert set(NEW) <= _cols(conn)
