import json

import pytest

from eki import builds, db, observe, score, store


def _at(conn, t, kind, **kw):
    jid = observe.record(conn, kind, build=kw.pop("build", "b0"), **kw)
    conn.execute("UPDATE journal SET t=? WHERE id=?", (t, jid))
    return jid


def _run(conn, t, *, state="done", local=False, first=None):
    _at(conn, t, "run", run_id=f"r{t}", provider="fake",
        data={"state": state, "seconds": 1.0, "local": local, "handed_off": state == "handed_off",
              "first_text": first})


def _runs(conn, start, n, **kw):
    for i in range(n):
        _run(conn, start + i, **kw)


def test_compute_on_a_hand_built_journal(conn):
    _run(conn, 10, local=True, first=1.0)                     # local, finished: counts
    _run(conn, 11, state="handed_off", local=True, first=3.0)  # local but handed off: doesn't
    _run(conn, 12, first=2.0)
    _run(conn, 13, state="failed")                            # not finished: outside local_share
    _at(conn, 12.5, "fault", data={"frame": "eki/x.py:1"})
    _at(conn, 12.6, "correction")
    _at(conn, 12.7, "correction")
    _at(conn, 50, "fault")                                    # outside the window
    _run(conn, 60, local=True)
    got = score.compute(conn, 10, 20)
    assert got["runs"] == 4
    assert got["local_share"] == pytest.approx(1 / 3, abs=1e-3)
    assert got["fault_rate"] == 25.0 and got["correction_rate"] == 50.0
    assert got["handoff_rate"] == 25.0
    assert got["median_first_text"] == 2.0
    assert (got["since"], got["until"]) == (10, 20)
    assert score.compute(conn, 10)["runs"] == 5


def test_an_empty_window_has_no_rates(conn):
    _at(conn, 5, "fault")
    got = score.compute(conn, 0, 100)
    assert got["runs"] == 0
    assert all(got[k] is None for k in ("local_share", "fault_rate", "correction_rate",
                                        "handoff_rate", "median_first_text"))


def test_window_falls_back_to_the_last_runs(conn):
    _runs(conn, 100, 40)                     # t 100..139
    _run(conn, 200)
    got = score.window(conn, 190, 300)
    assert got["runs"] == 30 and got["since"] == 111 and got["until"] == 300
    assert score.window(conn, 100, 300)["runs"] == 41          # enough: as asked
    assert score.window(conn, 190)["runs"] == 1                # no until: no fallback
    few = score.last_runs(conn, 100, 300)
    assert few["runs"] == 41 and few["since"] == 100
    assert score.last_runs(conn, 30, 50)["runs"] == 0


def _s(fault=0.0, corr=0.0, local=0.5):
    return {"fault_rate": fault, "correction_rate": corr, "local_share": local}


def test_verdict_rules():
    assert score.verdict(_s(), _s()) == "same"
    assert score.verdict(_s(fault=2), _s(fault=4)) == "worse"      # +100% and +2
    assert score.verdict(_s(fault=2), _s(fault=3.9)) == "same"     # +95% but under 2
    assert score.verdict(_s(fault=0), _s(fault=1.9)) == "same"     # the 2-absolute floor
    assert score.verdict(_s(fault=0), _s(fault=2)) == "worse"
    assert score.verdict(_s(fault=10), _s(fault=14)) == "same"     # +4 but only 40%
    assert score.verdict(_s(corr=3), _s(corr=6)) == "worse"
    assert score.verdict(_s(local=0.6), _s(local=0.49)) == "worse"   # 11 points down
    assert score.verdict(_s(local=0.6), _s(local=0.55)) == "same"
    assert score.verdict(_s(fault=6), _s(fault=2)) == "better"
    assert score.verdict(_s(corr=2), _s(corr=0.5)) == "same"       # fell 75% but only 1.5
    assert score.verdict(_s(local=0.3), _s(local=0.45)) == "better"
    assert score.verdict(_s(fault=2, local=0.3), _s(fault=6, local=0.9)) == "worse"   # worse wins
    none = {"fault_rate": None, "correction_rate": None, "local_share": None}
    assert score.verdict(none, _s(fault=50)) == "same"
    assert score.verdict(_s(), none) == "same"


def test_healthy_at_reads_the_marker(home):
    d = builds.root() / "abc"
    d.mkdir()
    assert score.healthy_at("abc") is None
    (d / ".healthy").write_text("1234.5\n")
    assert score.healthy_at("abc") == 1234.5
    (d / ".healthy").write_text("garbage")
    assert score.healthy_at("abc") == pytest.approx((d / ".healthy").stat().st_mtime)


def test_record_build_is_idempotent_and_starts_at_the_previous_build(conn):
    _runs(conn, 0, 40)                       # 0..39
    _runs(conn, 1000, 35)                    # 1000..1034
    assert score.record_build(conn, "b1", 500.0)
    assert not score.record_build(conn, "b1", 500.0)
    assert score.record_build(conn, "b2", 2000.0)
    rows = {r["build"]: json.loads(r["before"]) for r in conn.execute("SELECT * FROM build_scores")}
    assert rows["b1"]["runs"] == 40 and rows["b1"]["since"] == 0
    assert rows["b2"]["runs"] == 35 and rows["b2"]["since"] == 500.0   # from b1's healthy time
    assert conn.execute("SELECT COUNT(*) FROM build_scores").fetchone()[0] == 2


def test_settle_recomputes_then_freezes(conn):
    _runs(conn, 0, 30)
    score.record_build(conn, "b1", 100.0)
    _runs(conn, 100, 10, state="failed")
    for t in range(100, 110):
        _at(conn, t + 0.5, "fault")
    assert score.settle(conn, now=200) == []
    row = conn.execute("SELECT * FROM build_scores WHERE build='b1'").fetchone()
    assert row["verdict"] is None and json.loads(row["after"])["runs"] == 10 and row["measured_at"] == 200
    _runs(conn, 110, 20)
    said = score.settle(conn, now=300)
    assert said and "worse" in said[0]
    row = conn.execute("SELECT * FROM build_scores WHERE build='b1'").fetchone()
    assert row["verdict"] == "worse" and json.loads(row["after"])["runs"] == 30
    assert score.verdict_of(conn, "b1") == "worse" and score.verdict_of(conn, "nope") is None
    regs = observe.entries(conn, kind="regression")
    assert len(regs) == 1 and regs[0]["build"] == "b1" and regs[0]["data"]["verdict"] == "worse"
    _runs(conn, 130, 50)                                        # frozen: never again
    assert score.settle(conn, now=400) == []
    assert json.loads(conn.execute("SELECT after FROM build_scores").fetchone()[0])["runs"] == 30
    assert len(observe.entries(conn, kind="regression")) == 1
    assert [r["build"] for r in score.worse(conn)] == ["b1"]
    assert score.worse(conn, since=301) == []


def test_settle_same_writes_no_regression(conn):
    _runs(conn, 0, 30)
    score.record_build(conn, "b1", 100.0)
    _runs(conn, 100, 30)
    score.settle(conn, now=200)
    assert score.verdict_of(conn, "b1") == "same"
    assert observe.entries(conn, kind="regression") == []


def _carried(conn, gid, title, landed_at, sha, touched, build="bx"):
    iid = store.new_id()
    conn.execute("INSERT INTO items(id, goal_id, title, state, commit_sha, rebased, touched, build,"
                 " landed_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (iid, gid, title, "live", sha, sha + "r", db.dumps(touched), build, landed_at, 1, 1))
    return iid


def test_undo_queues_a_revert_per_item_newest_first(conn):
    _carried(conn, "g0", "older", 10.0, "aaa", ["eki/a.py"])
    _carried(conn, "g0", "newer", 20.0, "bbb", ["eki/b.py", "tests/test_b.py"])
    _carried(conn, "g0", "elsewhere", 30.0, "ccc", ["x"], build="by")
    gid = score.undo(conn, "bx")
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    assert (g["text"], g["source"], g["owner"], g["state"]) == ("undo build bx", "ask", "you", "planned")
    its = conn.execute("SELECT * FROM items WHERE goal_id=? ORDER BY rowid", (gid,)).fetchall()
    assert [i["title"] for i in its] == ["revert: newer", "revert: older"]
    assert all(i["state"] == "waiting" for i in its)
    assert "git revert --no-edit bbbr" in its[0]["spec"]
    assert json.loads(its[0]["files"]) == ["eki/b.py", "tests/test_b.py"]
    assert json.loads(its[0]["deps"]) == [] and json.loads(its[1]["deps"]) == [its[0]["id"]]
    with pytest.raises(ValueError):
        score.undo(conn, "nothing-here")
