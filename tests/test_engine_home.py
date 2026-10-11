"""An engine whose home is gone stops (eki/engine.py home_here), and tests never start a real one."""
import shutil

from eki import engine, paths
from eki.cli import common


def test_home_here_until_the_home_is_deleted(conn, home):
    assert engine.home_here()
    shutil.rmtree(home)
    assert not engine.home_here()


def test_a_cli_command_in_a_test_never_starts_a_real_engine(home, capsys, monkeypatch):
    monkeypatch.setattr(engine, "running_pid", lambda: None)
    common.ensure_engine()
    assert "pid 0" in capsys.readouterr().err
