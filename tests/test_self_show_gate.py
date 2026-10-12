"""What gate 1 ran (items.judged_by) on the board, its queue line and in `eki self show`."""
import json
import types
from pathlib import Path

import pytest

from eki import db, selfview, selfwork
from eki.cli import common, main, selfboard
import eki.cli.self as self_cmd

T = 1_000_000.0
SAID = "gate 1: 4 test files for eki/queue.py"


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: None)
    monkeypatch.setattr(selfwork, "source", lambda: Path("/src/eki"))
    monkeypatch.setattr(common, "time", types.SimpleNamespace(time=lambda: T + 60))


def seed(conn):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     ("g1", "a wish", "ask", "you", "planned", T))
        for n, (iid, state, judged) in enumerate((("i1", "queued", SAID), ("i2", "waiting", None))):
            conn.execute("INSERT INTO items(id, goal_id, title, spec, files, deps, independent, touched, "
                         "judged_by, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (iid, "g1", f"item {iid}", "do it", '["eki/queue.py"]', "[]", 0, '["eki/queue.py"]',
                          judged, state, T + n, T + n))


def test_the_board_carries_judged_by_on_items_and_the_queue(home):
    conn = db.connect()
    seed(conn)
    b = selfview.board(conn)
    g = next(g for g in b["goals"] if g["id"] == "g1")
    assert {it["id"]: it["judged_by"] for it in g["items"]} == {"i1": SAID, "i2": None}
    assert [(q["id"], q["judged_by"]) for q in b["queue"]] == [("i1", SAID)]
    json.dumps(b)  # the web gets it as JSON


def test_the_queue_line_says_it_after_docs_only(home, capsys):
    conn = db.connect()
    seed(conn)
    selfboard._queue(conn)
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "i1" in ln)
    assert line.endswith(f"item i1  {SAID}")


def test_show_prints_gate_1_after_commit(home, capsys):
    conn = db.connect()
    seed(conn)
    assert main(["self", "show", "i1"]) == 0
    out = capsys.readouterr().out
    assert f"gate 1:   {SAID}\n" in out and out.index("commit:") < out.index("gate 1:")
    assert main(["self", "show", "i2"]) == 0
    assert "gate 1:   -\n" in capsys.readouterr().out
