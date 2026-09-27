import time

import pytest

from conftest import run_inline
from eki import db, housekeep, quota, selfwork, standing, store, workspace


@pytest.fixture
def src(tmp_path, monkeypatch):
    """eki's own source, so a self round has something to plan against."""
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "x.py").write_text("# x\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def says(tmp_path, monkeypatch, text):
    p = tmp_path / "says.txt"
    p.write_text(text)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))


def row(conn, sid):
    return conn.execute("SELECT * FROM standing WHERE id=?", (sid,)).fetchone()


def rounds(conn, sid):
    return conn.execute("SELECT * FROM goals WHERE standing_id=? ORDER BY created_at", (sid,)).fetchall()


def finish_plan(conn, g):
    run_inline(conn, g["plan_run"])
    selfwork.tick(conn)


def test_a_round_opens_at_background_priority_and_only_one_at_a_time(conn, proj):
    sid = standing.add(conn, proj, "keep the tests fast")
    st = row(conn, sid)
    assert st["state"] == "on" and st["project"]
    said = standing.tick(conn)
    assert any("round goal" in s for s in said)
    [g] = rounds(conn, sid)
    assert g["project"] == st["project"] and g["owner"] == "eki" and g["source"] == "standing"
    run = store.run(conn, g["plan_run"])
    assert run["priority"] == "background"
    assert "keep the tests fast" in run["prompt"] and str(proj.resolve()) in g["text"]
    assert "ITEMS: []" in g["text"]
    assert row(conn, sid)["rounds"] == 1 and row(conn, sid)["last_round_at"]
    assert standing.tick(conn) == [] and len(rounds(conn, sid)) == 1       # one open round is enough


def test_an_empty_plan_rests_the_goal(conn, proj, tmp_path, monkeypatch):
    sid = standing.add(conn, proj, "tidy up")
    standing.tick(conn)
    says(tmp_path, monkeypatch, "nothing to do\nITEMS: []")
    finish_plan(conn, rounds(conn, sid)[0])
    assert rounds(conn, sid)[0]["state"] == "planned"
    said = standing.tick(conn)
    st = row(conn, sid)
    assert any("resting" in s for s in said) and "nothing to do" in st["why"]
    assert st["rest_until"] > time.time() + 23 * 3600 and st["failures"] == 0
    assert standing.tick(conn) == [] and len(rounds(conn, sid)) == 1       # counted once, still resting
    standing.now(conn, sid[:6])
    assert standing.tick(conn) and len(rounds(conn, sid)) == 2             # `now` clears the rest
    assert "tidy up" in rounds(conn, sid)[1]["text"]
    assert f"round {rounds(conn, sid)[0]['id']} (planned): nothing to do" in rounds(conn, sid)[1]["text"]


def test_proposed_items_hold_rounds(conn, proj):
    sid = standing.add(conn, proj, "more features")
    pid = row(conn, sid)["project"]
    for n in range(3):
        gid = selfwork.submit(conn, f"thing {n}", plan=False, project=pid)
        conn.execute("UPDATE items SET state='proposed' WHERE goal_id=?", (gid,))
    assert standing.tick(conn) == []
    assert row(conn, sid)["why"] == "3 proposed items waiting for you" and not rounds(conn, sid)
    conn.execute("UPDATE items SET state='applied' WHERE state='proposed' AND rowid=(SELECT MIN(rowid) FROM items)")
    assert standing.tick(conn) and row(conn, sid)["why"] is None


def test_a_busy_mac_or_the_budget_hold_rounds(conn, proj, monkeypatch):
    sid = standing.add(conn, proj, "more features")
    monkeypatch.setenv("EKI_MACHINE", "busy")
    assert standing.tick(conn) == []
    assert row(conn, sid)["why"] == "waiting for the Mac: EKI_MACHINE=busy"
    monkeypatch.setenv("EKI_MACHINE", "ok")
    quota.record(conn, "fake", {"five_hour": {"used": 0.95, "resets_at": time.time() + 3600}})
    assert standing.tick(conn) == []
    assert row(conn, sid)["why"].startswith("waiting for fake: kept for you: 5h at 95%")
    assert not rounds(conn, sid)


def test_three_failed_rounds_make_it_stuck_and_resume_clears_it(conn, proj):
    sid = standing.add(conn, proj, "fail every time")
    for n in range(1, 4):
        assert standing.tick(conn, now=time.time() + 2 * 3600)    # past the last failure's hour of rest
        finish_plan(conn, rounds(conn, sid)[-1])
        assert rounds(conn, sid)[-1]["state"] == "failed"
        standing.tick(conn)                            # settles it; the rest holds the next round
        standing.tick(conn)                            # counted once however often it ticks
        assert row(conn, sid)["failures"] == n and len(rounds(conn, sid)) == n
        assert row(conn, sid)["rest_until"] > time.time() + 3000
    st = row(conn, sid)
    assert st["state"] == "stuck" and "resume" in st["why"]
    assert standing.tick(conn, now=time.time() + 10 * 3600) == []
    standing.resume(conn, sid)
    st = row(conn, sid)
    assert st["state"] == "on" and st["failures"] == 0
    assert standing.tick(conn) and len(rounds(conn, sid)) == 4
    assert row(conn, sid)["failures"] == 0


def test_eki_s_own_source_has_no_project(conn, src):
    sid = standing.add(conn, src, "make eki calmer")
    assert row(conn, sid)["project"] is None
    assert not conn.execute("SELECT 1 FROM projects").fetchone()
    standing.tick(conn)
    [g] = rounds(conn, sid)
    assert g["project"] is None and "eki itself" in g["text"]


def test_refusals_and_ids(conn, tmp_path, proj):
    with pytest.raises(ValueError):
        standing.add(conn, tmp_path / "nowhere", "x")
    sid = standing.add(conn, proj, "x")
    with pytest.raises(KeyError):
        standing.pause(conn, "zzz-no-such")
    standing.pause(conn, sid[:5])
    assert row(conn, sid)["state"] == "paused" and standing.tick(conn) == []
    standing.drop(conn, sid)
    assert row(conn, sid)["state"] == "dropped"


def test_housekeeping_runs_it():
    assert "standing" in [name for name, _ in housekeep.STEPS]
