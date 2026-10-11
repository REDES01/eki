"""GET /api/sync: every direction in order with its last run, count, clashes and note, and the diff."""
import json

from eki import apiroutes, syncstate


def test_the_shape_with_state_and_a_diff(conn):
    syncstate.record("memory-in", count=12, note="12 notes")
    syncstate.record("skills-in", count=3, clashes=["pdf: edited in eki"])
    (syncstate.root() / "instructions.diff").write_text("-a\n+b\n")
    fn, m = apiroutes.match("GET", "/api/sync")
    got = fn(conn, m, {}, {})
    assert set(got) == {"directions", "diff"} and got["diff"] == "-a\n+b\n"
    assert [d["name"] for d in got["directions"]] == list(syncstate.DIRECTIONS)
    by = {d["name"]: d for d in got["directions"]}
    assert all(set(d) == {"name", "at", "count", "clashes", "note"} for d in got["directions"])
    assert by["skills-in"]["count"] == 3 and by["skills-in"]["clashes"] == ["pdf: edited in eki"]
    assert by["memory-in"]["note"] == "12 notes" and isinstance(by["memory-in"]["at"], float)
    assert by["skills-out"] == {"name": "skills-out", "at": None, "count": 0, "clashes": [], "note": ""}
    json.dumps(got)


def test_nothing_recorded_and_no_diff(conn):
    fn, m = apiroutes.match("GET", "/api/sync")
    got = fn(conn, m, {}, {})
    assert got["diff"] == "" and all(d["at"] is None for d in got["directions"])
