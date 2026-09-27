"""`eki goal`: add, pause, resume, drop, now and the listing."""
import pytest

from eki import budget, db, selfwork, standing, store, workspace
from eki.cli import main


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "garden"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r.resolve()


def row(conn, sid):
    return conn.execute("SELECT * FROM standing WHERE id=?", (sid,)).fetchone()


def added(capsys, proj, *extra):
    assert main(["goal", "add", str(proj), "keep the tests fast\nand small", *extra]) == 0
    return capsys.readouterr().out.strip()


def test_add_records_a_project_and_a_standing_goal(conn, capsys, proj):
    sid = added(capsys, proj, "--check", "true", "--branch", "trunk")
    st = row(conn, sid)
    assert st["state"] == "on" and st["text"].startswith("keep the tests fast")
    p = conn.execute("SELECT * FROM projects WHERE id=?", (st["project"],)).fetchone()
    assert p["path"] == str(proj) and p["branch"] == "trunk" and p["check_cmd"] == "true"


def test_add_refuses_a_folder_that_isnt_a_repo(conn, capsys, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["goal", "add", str(plain), "anything"]) == 1
    assert "not a git repo" in capsys.readouterr().err
    assert conn.execute("SELECT COUNT(*) FROM standing").fetchone()[0] == 0


def test_pause_resume_now_drop(conn, capsys, proj):
    sid = added(capsys, proj)
    assert main(["goal", "pause", sid[:6]]) == 0
    assert row(conn, sid)["state"] == "paused"
    assert main(["goal", "resume", sid]) == 0
    assert row(conn, sid)["state"] == "on"
    with db.tx(conn):
        conn.execute("UPDATE standing SET rest_until=? WHERE id=?", (db.now() + 3600, sid))
    assert main(["goal", "now", sid]) == 0
    assert row(conn, sid)["rest_until"] <= db.now()
    assert main(["goal", "drop", sid]) == 0
    assert row(conn, sid)["state"] == "dropped"
    capsys.readouterr()
    assert main(["goal"]) == 0
    assert sid not in capsys.readouterr().out


def test_an_unknown_id_fails(conn, capsys):
    assert main(["goal", "pause", "nope"]) == 1
    assert "no standing goal" in capsys.readouterr().err


def test_the_listing(conn, capsys, proj):
    sid = added(capsys, proj)
    st = row(conn, sid)
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("UPDATE standing SET rounds=2, why=? WHERE id=?", ("waiting for the Mac: busy", sid))
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project, standing_id)"
                     " VALUES (?,?,?,?,?,?,?,?)", (gid, "round", "standing", "eki", "planned", db.now(),
                                                   st["project"], sid))
    proposed = selfwork.new_item(conn, gid, "say hello", "do it", ["app.py"], [], False)
    selfwork._set(conn, proposed, state="proposed", branch=f"eki/{proposed}")
    waiting = selfwork.new_item(conn, gid, "say bye", "do it", ["bye.py"], [], False)
    done = selfwork.new_item(conn, gid, "old one", "done", ["old.py"], [], False)
    selfwork._set(conn, done, state="applied")
    assert main(["goal"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"{sid}  garden  on  keep the tests fast"
    assert "  why: waiting for the Mac: busy" in lines
    assert "  rounds: 2" in lines
    assert f"git -C {proj} merge eki/{proposed}" in out
    assert any(waiting in ln and "waiting" in ln and f"eki/{waiting}" in ln for ln in lines)
    assert done not in out
    assert lines[-1] == budget.describe()


def test_a_resting_goal_says_until_when(conn, capsys, proj):
    sid = added(capsys, proj)
    with db.tx(conn):
        conn.execute("UPDATE standing SET rest_until=?, why=? WHERE id=?",
                     (db.now() + 24 * 3600, "nothing worth doing now: all good", sid))
    assert main(["goal"]) == 0
    out = capsys.readouterr().out
    assert "why: resting until" in out and "all good" in out


def test_no_goals_still_shows_the_budget(conn, capsys):
    assert main(["goal"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "no standing goals" in lines[0] and lines[-1].startswith("budget:")
