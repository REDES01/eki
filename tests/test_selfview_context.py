"""An item's context (the knowledge cards the planner picked) on the board and in `eki self show`."""
import json
import types
from pathlib import Path

import pytest

from eki import db, selfview, selfwork
from eki.cli import common, main
import eki.cli.self as self_cmd

T = 1_000_000.0
PICKED = [{"name": "architecture", "why": "loader"}, {"name": "voices", "why": ""}]


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: None)
    monkeypatch.setattr(selfwork, "source", lambda: Path("/src/eki"))
    monkeypatch.setattr(common, "time", types.SimpleNamespace(time=lambda: T + 60))


def seed(conn):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     ("g1", "a wish", "ask", "you", "planned", T))
        for n, (iid, ctx) in enumerate((("i1", json.dumps(PICKED)), ("i2", None), ("i3", "not json"))):
            conn.execute("INSERT INTO items(id, goal_id, title, spec, files, deps, independent, context, "
                         "state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (iid, "g1", f"item {iid}", "do it", '["eki/a.py"]', "[]", 0, ctx, "waiting",
                          T + n, T + n))


def test_board_carries_the_card_names(home):
    conn = db.connect()
    seed(conn)
    g = next(g for g in selfview.board(conn)["goals"] if g["id"] == "g1")
    ctx = {it["id"]: it["context"] for it in g["items"]}
    assert ctx == {"i1": ["architecture", "voices"], "i2": None, "i3": None}
    json.dumps(g)  # the web gets it as JSON


def test_show_prints_the_context_line(home, capsys):
    conn = db.connect()
    seed(conn)
    assert main(["self", "show", "i1"]) == 0
    out = capsys.readouterr().out
    assert "context:  architecture (loader), voices\n" in out
    assert out.index("files:") < out.index("context:") < out.index("touched:")


def test_show_leaves_it_out_when_none(home, capsys):
    conn = db.connect()
    seed(conn)
    assert main(["self", "show", "i2"]) == 0
    assert "context:" not in capsys.readouterr().out
