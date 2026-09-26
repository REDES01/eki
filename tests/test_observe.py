import json
import sqlite3
import traceback

import pytest

from eki import db, observe, paths, store
from conftest import run_inline


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_tables_exist_on_a_fresh_and_an_existing_db(home):
    c = db.connect()
    assert {"journal", "build_scores"} <= _tables(c)
    c.close()
    old = sqlite3.connect(paths.db())                     # a file from before the journal
    old.execute("DROP TABLE journal")
    old.execute("DROP TABLE build_scores")
    old.commit()
    old.close()
    c = db.connect()
    assert {"journal", "build_scores"} <= _tables(c)
    idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"journal_t", "journal_kind"} <= idx


def test_record_inside_and_outside_a_transaction(conn):
    a = observe.record(conn, "limit", provider="fake", data={"reason": "five_hour"})
    with db.tx(conn):
        b = observe.record(conn, "handoff", run_id="r1", provider="fake", build="b7")
    c = observe.record(None, "limit", data={"n": 1})
    assert a and b and c
    got = observe.entries(conn)
    assert [e["kind"] for e in got] == ["limit", "handoff", "limit"]
    assert got[0]["data"] == {"reason": "five_hour"} and got[0]["build"] == "dev"
    assert got[1]["build"] == "b7" and got[1]["run_id"] == "r1"
    assert observe.entries(conn, kind="handoff")[0]["id"] == b


def test_record_never_raises(conn):
    conn.close()
    assert observe.record(conn, "fault", data={"x": 1}) is None
    assert observe.fault(conn, "here", "not a traceback") is None
    assert observe.run_ended(conn, "nope") is None
    assert observe.check_correction(conn, "nope") is False


def test_a_fault_in_eki_names_its_frame(conn):
    try:
        observe.parse_since("soon")
    except ValueError:
        fid = observe.fault(conn, "test", run_id="r9")
    e = observe.entries(conn, kind="fault")[0]
    assert e["id"] == fid and e["run_id"] == "r9"
    d = e["data"]
    assert d["where"] == "test" and d["ours"] is True and d["exc"] == "ValueError"
    assert d["frame"].startswith("eki/observe.py:")
    assert observe.top_frame(d["traceback"])[0] == "eki/observe.py"


def test_a_fault_only_in_the_stdlib_is_not_ours(conn):
    try:
        json.loads("{")
    except ValueError:
        tb = traceback.format_exc()
    observe.fault(conn, "test", tb)
    d = observe.entries(conn, kind="fault")[0]["data"]
    assert d["ours"] is False and d["frame"] is None and d["exc"].endswith("JSONDecodeError")


def test_top_frame_in_a_build_folder():
    tb = ('Traceback (most recent call last):\n'
          '  File "/Users/a/.eki/builds/abc123/eki/engine.py", line 40, in tick\n'
          '    x()\n'
          '  File "/Users/a/.eki/builds/abc123/eki/providers/fake.py", line 7, in x\n'
          '    y()\n'
          '  File "/usr/lib/python3.12/json/__init__.py", line 346, in loads\n'
          '    z\n'
          'KeyError: \'q\'\n')
    assert observe.top_frame(tb) == ("eki/providers/fake.py", 7)


def test_long_tracebacks_are_cut(conn):
    tb = "Traceback (most recent call last):\n" + "x" * 10000 + "\nRuntimeError: boom\n"
    observe.fault(conn, "test", tb)
    d = observe.entries(conn, kind="fault")[0]["data"]
    assert len(d["traceback"]) == observe.TB_MAX and d["traceback"].endswith("boom\n")
    assert d["exc"] == "RuntimeError"


def test_secrets_are_scrubbed(conn):
    s = observe.scrub("use sk-ant-abcdef1234567890 and password=hunter2 please")
    assert "hunter2" not in s and "sk-ant" not in s and s.count("[secret]") == 2
    assert "ghp_" not in observe.scrub("token ghp_ABCDEFGH12345678")
    assert "abc.def" not in observe.scrub("Authorization: Bearer abc.def")
    assert "xyz" not in observe.scrub('{"api_key": "xyz"}')
    assert observe.scrub("nothing to hide") == "nothing to hide"
    observe.record(conn, "fault", data={"tb": "password: hunter2", "nested": ["sk-abcdefghijk"]})
    raw = conn.execute("SELECT data FROM journal").fetchone()[0]
    assert "hunter2" not in raw and "sk-abcdefghijk" not in raw


def test_run_ended_for_a_fake_run(conn):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hello", provider="fake")
    r = run_inline(conn, rid)
    assert r["state"] == "done"
    assert observe.run_ended(conn, rid) is None     # the worker wrote it already: once per run
    rows = observe.entries(conn, kind="run")
    assert len(rows) == 1
    e = rows[0]
    assert e["run_id"] == rid and e["thread_id"] == tid and e["provider"] == "fake"
    d = e["data"]
    assert d["state"] == "done" and d["local"] is False and d["handed_off"] is False
    assert d["seconds"] >= 0 and d["first_text"] is not None and d["first_text"] <= d["seconds"]


def test_run_ended_ignores_a_run_not_ended(conn):
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hello", provider="fake")
    assert observe.run_ended(conn, rid) is None and not observe.entries(conn)


def _two(conn, first, second, gap=60.0, provider=None, tid=None):
    tid = tid or store.create_thread(conn, "t", None)
    a = store.create_run(conn, tid, first, provider=provider)
    b = store.create_run(conn, tid, second, provider=provider)
    t0 = conn.execute("SELECT created_at FROM runs WHERE id=?", (a,)).fetchone()[0]
    conn.execute("UPDATE runs SET created_at=? WHERE id=?", (t0 + gap, b))
    return a, b


def test_a_correction_within_ten_minutes(conn):
    a, b = _two(conn, "write me a poem about the sea", "No, a haiku please")
    assert observe.check_correction(conn, b)
    e = observe.entries(conn, kind="correction")[0]
    assert e["run_id"] == b and e["data"] == {"previous": a, "prompt": "No, a haiku please",
                                              "redo": False}
    assert not observe.check_correction(conn, a)                      # nothing before it


def test_a_near_duplicate_is_a_redo(conn):
    _, b = _two(conn, "summarise  the design doc", "Summarise the design doc.")
    assert observe.check_correction(conn, b)
    assert observe.entries(conn, kind="correction")[0]["data"]["redo"] is True


@pytest.mark.parametrize("second", ["nothing else, thanks", "now do the tests"])
def test_words_that_only_start_like_no_are_not(conn, second):
    _, b = _two(conn, "write me a poem", second)
    assert not observe.check_correction(conn, b)


def test_not_a_correction_after_eleven_minutes(conn):
    _, b = _two(conn, "write me a poem", "actually, a limerick", gap=660)
    assert not observe.check_correction(conn, b)


def test_not_a_correction_for_a_command_run(conn):
    _, b = _two(conn, "ls", "ls", provider="command")
    assert not observe.check_correction(conn, b)


def test_not_a_correction_in_a_self_work_thread(conn):
    tid = store.create_thread(conn, "self", None)
    conn.execute("INSERT INTO goals(id, text, thread_id, created_at) VALUES ('g1','x',?,?)",
                 (tid, db.now()))
    _, b = _two(conn, "build item a", "build item a", tid=tid)
    assert not observe.check_correction(conn, b)
    assert not observe.entries(conn)


def test_prune_removes_old_rows_only(conn):
    old = observe.record(conn, "limit")
    new = observe.record(conn, "limit")
    conn.execute("UPDATE journal SET t=? WHERE id=?", (db.now() - 91 * 86400, old))
    assert observe.prune(conn) == 1
    assert [e["id"] for e in observe.entries(conn)] == [new]


def test_entries_window(conn):
    a = observe.record(conn, "limit")
    conn.execute("UPDATE journal SET t=100 WHERE id=?", (a,))
    b = observe.record(conn, "handoff")
    assert [e["id"] for e in observe.entries(conn, since=200)] == [b]
    assert [e["id"] for e in observe.entries(conn, until=200)] == [a]


def test_parse_since():
    assert observe.parse_since("30m") == 1800
    assert observe.parse_since("24h") == 86400
    assert observe.parse_since("7d") == 7 * 86400
    for bad in ("", "24", "h", "3w", "-1h"):
        with pytest.raises(ValueError):
            observe.parse_since(bad)


def test_a_checkout_called_eki_is_not_mistaken_for_the_package():
    assert observe._eki_relative("/Users/x/eki/tests/test_observe.py") is None
    assert observe._eki_relative("/Users/x/eki/eki/queue.py") == "eki/queue.py"
    assert observe._eki_relative("/Users/x/.eki/builds/abc/eki/cli/self.py") == "eki/cli/self.py"
