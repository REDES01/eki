"""The `eki self` board's words as data (eki/selfview.py): the CLI prints the same, the web gets a dict."""
import json
import types
from pathlib import Path

import pytest

from eki import asks, db, selfview, selfwork, store
from eki.cli import common, main
import eki.cli.self as self_cmd

T = 1_000_000.0


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: None)
    monkeypatch.setattr(selfwork, "source", lambda: Path("/src/eki"))
    monkeypatch.setattr(common, "time", types.SimpleNamespace(time=lambda: T + 7200))


def item(conn, iid, gid, n, state, **fields):
    conn.execute("INSERT INTO items(id, goal_id, title, spec, files, deps, independent, created_at, "
                 "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (iid, gid, f"item {iid}", "do it", '["eki/a.py"]', "[]", 0, T + n, T + n))
    selfwork._set(conn, iid, state=state, **fields)


def seed(conn):
    tid = store.create_thread(conn, "draft", "/tmp")
    rid = store.create_run(conn, tid, "wish", provider="fake")
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     ("g1", "make it better\nsecond line", "ask", "you", "planned", T))
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, draft_run, error) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     ("g2", "a wish", "ask", "you", "drafting", T + 60, rid, "it went wrong"))
        item(conn, "i1", "g1", 1, "proposed", touched=json.dumps(["eki/a.py", "eki/b.py"]),
             branch="self/i1")
        item(conn, "i2", "g1", 2, "locked", locked=json.dumps(["bin/check"]), branch="self/i2")
        item(conn, "i3", "g1", 3, "queued", queued_at=T + 10, head="a" * 40, rebased="b" * 40,
             gate2="green", gate2_on="b" * 40)
        item(conn, "i4", "g1", 4, "queued", queued_at=T + 20, touched=json.dumps(["docs/x.md"]))
        item(conn, "i5", "g1", 5, "waiting", deps=json.dumps(["i3"]))
        item(conn, "i6", "g1", 6, "landed", rebased="e" * 40, run_id=rid)
        item(conn, "i7", "g1", 7, "resolving", run_id="r9", head="c" * 40)
        aid = asks.create(conn, rid, tid, "question", {"questions": [{"question": "which one?\nmore"}]})
    return tid, rid, aid


BOARD = """\
(autonomy propose, 4 at once, source /src/eki)
loop: off (eki self loop on)

queue
  1.  i3  on aaaaaaaa  gate 2 green — waiting for the ones ahead  item i3
  2.  i4  on -         rebasing                                   item i4 (docs only)
  -   i7  on cccccccc  resolving (run r9)                         item i7

g2  drafting    1h ago  a wish
           drafting  (run {rid})
           asked you: which one? — eki answer {aid}
           ! it went wrong

g1  planned     2h ago  make it better
  i1  proposed  self/i1                item i1
             ✓ 2 files; `eki self apply i1` queues it
  i2  locked    self/i2                item i2
             ! touches locked files: bin/check; `eki self apply i2 --yes` if you agree
  i3  queued    on aaaaaaaa            item i3
  i4  queued    rebasing               item i4
             docs only
  i5  waiting   after i3               item i5
  i6  landed                           item i6
             ✓ in integration main (eeeeeeeeeeee)
  i7  resolving run r9                 item i7
"""


def test_the_board_prints_exactly_what_it_did_before_the_words_moved(conn, capsys):
    """Captured from eki/cli/selfboard.py before selfview existed."""
    _, rid, aid = seed(conn)
    assert main(["self"]) == 0
    assert capsys.readouterr().out == BOARD.format(rid=rid, aid=aid)


def test_board_as_data_has_the_same_words_and_order(conn):
    tid, rid, aid = seed(conn)
    b = selfview.board(conn)
    assert (b["autonomy"], b["parallel"], b["source"]) == ("propose", 4, "/src/eki")
    assert [(q["id"], q["pos"]) for q in b["queue"]] == [("i3", 1), ("i4", 2), ("i7", None)]
    assert b["queue"][0]["stage"] == "gate 2 green — waiting for the ones ahead"
    assert b["queue"][1]["stage"] == "rebasing" and b["queue"][1]["docs_only"]
    assert b["queue"][2]["stage"] == "resolving (run r9)" and b["queue"][2]["thread"] is None
    g2, g1 = b["goals"]
    assert g2["id"] == "g2" and g2["thread"] == tid and g2["error"] == "it went wrong"
    assert g2["drafting"] == f"drafting  (run {rid})\n           asked you: which one? — eki answer {aid}"
    assert g1["drafting"] is None and g1["wish"] == "make it better" and g1["thread"] is None
    its = {it["id"]: it for it in g1["items"]}
    assert list(its) == ["i1", "i2", "i3", "i4", "i5", "i6", "i7"]
    assert its["i1"]["where"] == "self/i1" and its["i1"]["note"] == "✓ 2 files; `eki self apply i1` queues it"
    assert its["i2"]["note"].startswith("! touches locked files: bin/check")
    assert its["i3"]["where"] == "on aaaaaaaa" and its["i3"]["note"] == ""
    assert its["i4"]["where"] == "rebasing" and its["i4"]["note"] == "docs only" and its["i4"]["docs_only"]
    assert its["i5"]["where"] == "after i3"
    assert its["i6"]["note"] == "✓ in integration main (eeeeeeeeeeee)" and its["i6"]["thread"] == tid
    assert its["i7"]["where"] == "run r9" and its["i7"]["branch"] is None


def test_an_empty_board(conn):
    assert selfview.board(conn)["goals"] == [] and selfview.board(conn)["queue"] == []


def test_verdicts_from_build_scores(conn):
    assert selfview.verdicts(conn) == {}
    with db.tx(conn):
        conn.execute("INSERT INTO build_scores(build, healthy_at, verdict) VALUES ('b1', 1, 'worse')")
        conn.execute("INSERT INTO build_scores(build, healthy_at) VALUES ('b2', 2)")
    assert selfview.verdicts(conn) == {"b1": "worse", "b2": "measuring"}


def test_open_prs_on_the_board_and_the_item(conn):
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at) VALUES ('p1','proj','/p','main',?)", (T,))
        conn.execute("INSERT INTO goals(id, text, source, owner, state, project, created_at) "
                     "VALUES ('g1','x','ask','you','planned','p1',?)", (T,))
    url = "https://github.com/me/proj/pull/7"
    item(conn, "i1", "g1", 1, "proposed", pr=url, pr_state="open", branch="eki/i1")
    item(conn, "i2", "g1", 2, "proposed", branch="eki/i2")
    item(conn, "i3", "g1", 3, "applied", pr="https://github.com/me/proj/pull/6", pr_state="merged")
    b = selfview.board(conn)
    assert b["prs"] == {"proj": 1}
    its = {it["id"]: it for it in b["goals"][0]["items"]}
    assert its["i1"]["pr"] == url and its["i2"]["pr"] is None
    assert its["i1"]["note"] == f"PR open: {url}"
    assert its["i2"]["note"] == "✓ 0 files; `eki self apply i2` queues it"
