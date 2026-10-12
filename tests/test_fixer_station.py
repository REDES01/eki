"""The fixer on the Station (eki/fixer.py, eki/selfview.py, eki/api_station.py): the daily count,
a fixing item's stage, and the fixer's summary under an item left for you."""
import datetime
import json

from eki import api_station, db, selfview, selfwork


def goal(conn, gid="g1"):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     (gid, "fewer items for a person", "ask", "you", "planned", db.now()))


def item(conn, iid, state, gid="g1", **fields):
    with db.tx(conn):
        conn.execute("INSERT INTO items(id, goal_id, title, spec, files, deps, independent, created_at, "
                     "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (iid, gid, f"item {iid}", "do it", '["eki/a.py"]', "[]", 0, db.now(), db.now()))
        selfwork._set(conn, iid, state=state, **fields)
    return selfwork.store_item(conn, iid)


def note(stage="gate1", summary=None, run="r" * 32):
    return json.dumps({"stage": stage, "failure": "FAILED test_order - expected the old order", "run": run,
                       "at": db.now(), "head": "a" * 40, "summary": summary})


def test_station_counts_fixed_and_left_today(conn):
    goal(conn)
    item(conn, "i1", "landed", fixer=note(summary="updated the stale test"), rebased="e" * 40)
    item(conn, "i2", "unfit", fixer=note(summary="a real bug; could not fix"), error="gate 1 red")
    item(conn, "i3", "proposed")                                          # no fixer: not counted
    days = api_station.station(conn)["fixer"]
    assert len(days) == 7 and days[0]["day"] == datetime.date.today().isoformat()
    assert {k: days[0][k] for k in ("fixed", "left")} == {"fixed": 1, "left": 1}
    assert all(d["fixed"] == d["left"] == 0 for d in days[1:])


def test_board_stage_of_a_fixing_item(conn):
    goal(conn)
    item(conn, "i1", "fixing", fixer=note(stage="gate2"), run_id="abcdef0123456789", head="c" * 40)
    q = [x for x in selfview.board(conn)["queue"] if x["id"] == "i1"]
    assert q and q[0]["stage"] == "fixing after gate2 (run abcdef01)" and q[0]["pos"] is None
    it = selfwork.store_item(conn, "i1")
    assert selfview.where(it) == "fixing after gate2 (run abcdef01)"
    g = selfview.board(conn)["goals"][0]
    assert g["stands"] == "in flight"


def test_unfit_item_line_carries_the_fixer_summary(conn):
    goal(conn)
    it = item(conn, "i1", "unfit", fixer=note(summary="the parser really drops the last line; left it"),
              error="gate 1 red: test_parse failed")
    line = selfview.note(conn, it)
    assert line.startswith("! gate 1 red: test_parse failed")
    assert "the fixer tried (gate1): the parser really drops the last line; left it" in line
    plain = item(conn, "i2", "left", error="resolve failed")                 # no fixer: as before
    assert selfview.note(conn, plain) == "! resolve failed"
