"""`eki self digest [now]` prints the day's page; `eki self undo <build>` queues the reverts."""
import pytest

from eki import db, digest, store
from eki.cli import main
import eki.cli.self as self_cmd


@pytest.fixture(autouse=True)
def no_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: calls.append(quiet))
    return calls


def test_digest_now_writes_a_page_and_prints_it_with_its_path(conn, capsys):
    assert main(["self", "digest", "now"]) == 0
    out = capsys.readouterr().out
    path = digest.latest()
    assert path is not None and str(path) in out
    assert path.read_text() in out


def test_digest_without_a_page_writes_one(conn, capsys):
    assert digest.latest() is None
    assert main(["self", "digest"]) == 0
    path = digest.latest()
    assert path is not None and capsys.readouterr().out == path.read_text()
    assert conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 0   # not taken as a goal


def test_digest_shows_the_latest_page_without_writing_again(conn, capsys):
    path = digest.write(conn)
    path.write_text("# kept\n")
    assert main(["self", "digest"]) == 0
    assert capsys.readouterr().out == "# kept\n"


def test_undo_with_carried_items_makes_the_goal(conn, capsys, no_engine):
    for title, sha in (("one", "aaa"), ("two", "bbb")):
        conn.execute("INSERT INTO items(id, goal_id, title, state, commit_sha, touched, build, landed_at,"
                     " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (store.new_id(), "g0", title, "live", sha, db.dumps(["eki/a.py"]), "bx", 1.0, 1, 1))
    conn.commit()
    assert main(["self", "undo", "bx"]) == 0
    out = capsys.readouterr().out
    g = conn.execute("SELECT * FROM goals WHERE text='undo build bx'").fetchone()
    assert g is not None
    assert f"goal {g['id']}: undo build bx — 2 revert item(s), building when there is room" in out
    assert no_engine == [True]


def test_undo_of_an_unknown_build_exits_1_with_a_message(conn, capsys):
    assert main(["self", "undo", "nope"]) == 1
    assert "eki: build nope carried no items" in capsys.readouterr().err
    assert conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 0
