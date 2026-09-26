"""Gate 2's candidate checks, run against this checkout in throwaway homes."""
import sys
from pathlib import Path

import pytest

from eki import candidate, db, paths, store

HERE = Path(__file__).resolve().parent.parent


@pytest.fixture
def two_threads(home):
    c = db.connect()
    store.create_thread(c, "one", None)
    store.create_thread(c, "two", None)
    c.close()
    return paths.db()


@pytest.mark.drill
def test_all_three_pass_on_this_checkout(two_threads):
    got = candidate.check(HERE, two_threads, HERE, sys.executable)
    assert [(n, ok) for n, ok, _ in got] == [
        ("engine boots", True), ("opens a copy of the db", True),
        ("the running build opens the migrated copy", True)], got
    assert candidate.threads_in(two_threads) == 2


def test_the_copy_lists_the_same_threads(two_threads, tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()
    assert candidate.opens_copy(HERE, two_threads, copy, sys.executable) == ""
    assert candidate.threads_in(copy / "eki.db") == 2


def test_a_missing_db_is_zero_threads(tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()
    assert candidate.opens_copy(HERE, tmp_path / "nope.db", copy, sys.executable) == ""
    assert not (tmp_path / "nope.db").exists()


@pytest.mark.drill
def test_main_prints_three_ticks(two_threads, capsys):
    assert candidate.main(["--db", str(two_threads), "--running", str(HERE),
                           "--python", sys.executable]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3 and all(line.startswith("✓ ") for line in lines)


@pytest.mark.drill
def test_a_running_path_that_isnt_eki_fails(two_threads, tmp_path, capsys):
    got = candidate.check(HERE, two_threads, tmp_path, sys.executable)
    assert got[2][1] is False and "isn't an eki" in got[2][2]
    assert candidate.main(["--db", str(two_threads), "--running", str(tmp_path),
                           "--python", sys.executable]) == 1
    assert "✗ the running build opens the migrated copy" in capsys.readouterr().out


@pytest.mark.drill
def test_a_check_that_raises_is_a_failed_line(two_threads, monkeypatch):
    def boom(*_):
        raise RuntimeError("no")
    monkeypatch.setattr(candidate, "engine_boots", boom)
    got = candidate.check(HERE, two_threads, HERE, sys.executable)
    assert got[0] == ("engine boots", False, "RuntimeError: no")
    assert got[1][1] and got[2][1]
