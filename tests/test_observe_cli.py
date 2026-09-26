import pytest

from eki import cli, db, observe

TB = ('Traceback (most recent call last):\n'
      '  File "/x/eki/queue.py", line 12, in tick\n    boom()\n'
      'KeyError: \'k\'\n')


def _fill(conn):
    observe.fault(conn, "queue.tick", TB, run_id="r_fault")
    observe.record(conn, "handoff", run_id="r_hand", provider="fake", data={"reason": "limit reached"})
    observe.record(conn, "correction", run_id="r_corr", provider="fake",
                   data={"prompt": "no, the other file", "redo": False})
    observe.record(conn, "limit", run_id="r_lim", provider="fake2", data={"error": "rate limited"})
    observe.record(conn, "run", run_id="r_run", provider="fake", data={"state": "done", "seconds": 4.2})
    old = observe.record(conn, "handoff", run_id="r_old", provider="fake", data={"reason": "ancient"})
    conn.execute("UPDATE journal SET t=? WHERE id=?", (db.now() - 3 * 86400, old))
    conn.commit()


def test_lists_everything_but_runs(conn, capsys):
    _fill(conn)
    assert cli.main(["observe"]) == 0
    out = capsys.readouterr().out
    assert "eki/queue.py:12 KeyError in queue.tick" in out
    assert "limit reached" in out and "no, the other file" in out and "rate limited" in out
    assert "r_run" not in out and "r_old" not in out
    assert "1 run ended in the window" in out
    assert "Traceback" not in out
    lines = out.splitlines()
    assert lines.index(next(l for l in lines if "r_fault" in l)) < lines.index(
        next(l for l in lines if "r_lim" in l))


def test_kind_and_since(conn, capsys):
    _fill(conn)
    cli.main(["observe", "--kind", "handoff"])
    out = capsys.readouterr().out
    assert "r_hand" in out and "r_old" not in out and "r_fault" not in out
    cli.main(["observe", "--kind", "handoff", "--since", "7d"])
    assert "ancient" in capsys.readouterr().out
    cli.main(["observe", "--kind", "run"])
    out = capsys.readouterr().out
    assert "r_run" in out and "done 4s" in out


def test_full_shows_the_traceback(conn, capsys):
    _fill(conn)
    cli.main(["observe", "--kind", "fault", "--full"])
    out = capsys.readouterr().out
    assert "    Traceback (most recent call last):" in out and "boom()" in out


def test_nothing_and_a_bad_since(conn, capsys):
    assert cli.main(["observe"]) == 0
    assert "nothing written down since" in capsys.readouterr().out
    assert cli.main(["observe", "--since", "yesterday"]) == 1
    assert "eki: " in capsys.readouterr().err


def test_in_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "observe" in capsys.readouterr().out
