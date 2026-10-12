"""Checks and verdicts: one verdict shape, filed only by a registered judge run (eki/verdicts.py)."""
import json

import pytest

from eki import db, judgebrief, judgerun, roles, store, verdicts


def good(**over):
    rec = {"check_id": "c1", "verdict": "passed", "evidence": "ran `eki slot -- bin/check`: 12 passed",
           "reasoning": "the test covers the check", "counter_evidence": "none found", "limitation": "",
           "suggestion": ""}
    rec.update(over)
    return rec


def judge_run(conn, checks=("c1", "c2"), scope="final", provider="fake", state="running", gid="g1"):
    with db.tx(conn):
        if conn.execute("SELECT 1 FROM goals WHERE id=?", (gid,)).fetchone() is None:
            conn.execute("INSERT INTO goals(id, text, created_at) VALUES (?,?,?)", (gid, "a goal", db.now()))
        tid = store.create_thread(conn, "judge", None, by="eki")
        rid = store.create_run(conn, tid, "judge it", provider=provider, model="m-1", fresh=True)
        store.update_run(conn, rid, state=state)
        verdicts.register(conn, rid, gid, None, scope, checks, 1, ["fake2"])
    return rid


@pytest.mark.parametrize("over, says", [
    ({"evidence": ""}, "evidence is empty"),
    ({"evidence": "   "}, "evidence is empty"),
    ({"reasoning": ""}, "reasoning is empty"),
    ({"verdict": "failed"}, "a failed verdict needs a suggestion"),
    ({"verdict": "uncertain", "limitation": "no screen"}, "an uncertain verdict needs a suggestion"),
    ({"verdict": "uncertain", "suggestion": "add a test"}, "an uncertain verdict needs a limitation"),
    ({"verdict": "maybe"}, "verdict must be one of"),
    ({"extra": "x"}, "unknown key 'extra'"),
    ({"evidence": 3}, "evidence must be a string"),
    ({"evidence": "x" * 4001}, "over 4000 characters"),
])
def test_a_bad_verdict_is_rejected(over, says):
    got = verdicts.validate(good(**over))
    assert any(says in p for p in got), got


def test_a_missing_key_is_rejected():
    rec = good()
    del rec["evidence"]
    assert "missing key 'evidence'" in verdicts.validate(rec)
    assert verdicts.validate(good()) == []
    assert verdicts.validate([1]) == ["a verdict is a JSON object"]


def test_submit_joins_every_problem(conn):
    rid = judge_run(conn)
    with pytest.raises(ValueError) as e:
        verdicts.submit(conn, rid, good(evidence="", reasoning=""))
    assert str(e.value) == "evidence is empty; reasoning is empty"


def test_a_valid_verdict_is_stored_with_provider_and_model(conn):
    rid = judge_run(conn)
    vid = verdicts.submit(conn, rid, good(check_id="C1"))
    row = conn.execute("SELECT * FROM verdicts WHERE id=?", (vid,)).fetchone()
    assert (row["check_id"], row["verdict"], row["provider"], row["model"]) == ("c1", "passed", "fake", "m-1")
    assert (row["goal_id"], row["scope"], row["round"]) == ("g1", "final", 1)
    assert set(verdicts.outcome(conn, rid)) == {"c1"}


def test_once_per_check_per_run(conn):
    rid = judge_run(conn)
    verdicts.submit(conn, rid, good())
    with pytest.raises(ValueError, match="already filed a verdict for c1"):
        verdicts.submit(conn, rid, good(verdict="failed", suggestion="fix it"))


def test_only_a_registered_active_judge_run_for_its_own_checks(conn):
    rid = judge_run(conn)
    with pytest.raises(ValueError, match="check c9 is not one this run judges"):
        verdicts.submit(conn, rid, good(check_id="c9"))
    with db.tx(conn):                               # a build run: never in judgings
        tid = store.create_thread(conn, "build", None)
        build = store.create_run(conn, tid, "build it", provider="fake")
        store.update_run(conn, build, state="running")
    with pytest.raises(ValueError, match="is not a judge run"):
        verdicts.submit(conn, build, good())
    ended = judge_run(conn, state="done")
    with pytest.raises(ValueError, match="has ended"):
        verdicts.submit(conn, ended, good())
    assert conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0] == 0


def test_checks_in_reads_only_the_checks_block():
    text = """What must be true when done
C1: not a check, it isn't under Checks
Read first
- docs/design.md

## Checks
C1: run `eki slot -- bin/check tests/test_x.py` and see it pass
- C2: `eki self show g` prints a checks: block
C2: a duplicate id is dropped
  `C3`: the web page shows the line
## After
C4: not a check either
"""
    assert verdicts.checks_in(text) == [
        ("c1", "run `eki slot -- bin/check tests/test_x.py` and see it pass"),
        ("c2", "`eki self show g` prints a checks: block"),
        ("c3", "the web page shows the line")]
    assert verdicts.checks_in("C1: no heading at all") == []


def test_store_lines_and_view(conn):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, created_at) VALUES ('g1','a goal',?)", (db.now(),))
        verdicts.store_checks(conn, "g1", [("c1", "the first thing"), ("c2", "the second thing")])
    assert [r["id"] for r in verdicts.checks_of(conn, "g1")] == ["c1", "c2"]
    assert verdicts.lines(conn, "g1") == ["c1  —        the first thing", "c2  —        the second thing"]
    rid = judge_run(conn)
    verdicts.submit(conn, rid, good(check_id="c2"))
    play = judge_run(conn, scope="play")
    verdicts.submit(conn, play, good(check_id="c2", verdict="uncertain", limitation="no screen",
                                     suggestion="look at it by hand"))
    lines = verdicts.lines(conn, "g1")
    assert lines[1] == "c2  uncertain the second thing  (play · fake · round 1)"   # the worst stands
    v = verdicts.view(conn, "g1")[1]
    assert (v["verdict"], v["scope"], v["provider"], v["model"]) == ("uncertain", "play", "fake", "m-1")
    second = judge_run(conn, scope="second", checks=("c2",))
    verdicts.submit(conn, second, good(check_id="c2"))
    assert verdicts.latest(conn, "g1")["c2"]["scope"] == "second"            # a second judge settles it
    assert [r["scope"] for r in verdicts.history(conn, "g1", "C2")] == ["second", "play", "final"]
    assert verdicts.lines(conn, "nope") == ["no checks — not judged"]


def test_pick_other_avoids_the_given_providers(conn):
    assert roles.pick("judge", conn).provider == "fake"
    assert roles.pick_other("judge", conn, set()).provider == "fake"
    p = roles.pick_other("judge", conn, {"fake"})
    assert p.provider == "fake2" and "fake" in p.why
    p = roles.pick_other("judge", conn, {"fake", "fake2"})
    assert p.provider is None


def test_pick_other_falls_back_to_another_model(home, conn):
    got = json.loads((home / "providers.json").read_text())
    got["fake"] = {"kind": "fake", "models": {"fast": "fake-small", "deep": "fake-big"}}
    (home / "providers.json").write_text(json.dumps(got))
    p = roles.pick_other("judge", conn, {"fake", "fake2"})
    assert p.provider == "fake" and p.model == "fake-small"


def test_the_brief_and_the_play_line():
    text = judgebrief.brief("g" * 3000, [("spec", "the spec"), ("c1", "it works")], "item",
                            item={"title": "T", "spec": "do it"}, diff="git diff abc..def",
                            claim="all done, perfect")
    assert "g" * 2000 in text and "g" * 2001 not in text
    assert "c1: it works" in text and "git diff abc..def" in text and "**T**" in text
    assert "The builder's claim — not evidence" in text and "eki slot -- " in text
    assert "eki verdict '{\"check_id\"" in text and "JUDGED: <n>" in text

    class Card:
        playtester = "playtester"
    line = judgebrief.play_line(Card(), "a change", [("c1", "it shows")])
    assert line.startswith("eki use playtester '") and line.endswith("| eki verdict --lines")
    assert "Focus:" in line and "c1: it shows" in line and "VERDICT: {" in line


def test_judgerun_registers_the_run_on_another_backend(conn, proj):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, created_at) VALUES ('g1','a goal',?)", (db.now(),))
    rid, why = judgerun.start(conn, goal_id="g1", item_id=None, scope="final", checks=["c1"], round=1,
                              avoid=["fake"], tree="accept-g1", repo=str(proj), ref="HEAD", prompt="judge",
                              background=True)
    assert why == "" and rid
    run = store.run(conn, rid)
    assert run["provider"] == "fake2" and run["fresh"] == 1 and run["priority"] == "background"
    j = verdicts.judging(conn, rid)
    assert (j["scope"], verdicts.listed(j), json.loads(j["avoid"])) == ("final", ["c1"], ["fake"])
    assert store.thread(conn, run["thread_id"])["cwd"].endswith("accept-g1")
