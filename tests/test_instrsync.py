"""~/.claude/CLAUDE.md and eki's AGENTS.md kept as one text, in the test homes only."""
import os

from eki import instrsync, syncstate


def claude_md(tmp_path):
    d = tmp_path / "user" / ".claude"
    d.mkdir(exist_ok=True)
    return d / "CLAUDE.md"


def age(path, seconds):
    t = path.stat().st_mtime - seconds
    os.utime(path, (t, t))


def state():
    return syncstate.status()["instructions"]


def test_both_missing_does_nothing(conn, home):
    assert instrsync.tick(conn) == []
    assert not syncstate.agents_md().exists()
    assert state()["note"] == "none" and state()["count"] == 0
    assert instrsync.diff() == ""


def test_claude_md_only_seeds_agents_md(conn, home, tmp_path):
    a = claude_md(tmp_path)
    a.write_text("# mine\n")
    said = instrsync.tick(conn)
    assert len(said) == 1
    assert syncstate.agents_md().read_text() == "# mine\n"
    assert state()["note"] == "one missing" and state()["count"] == 1


def test_agents_md_only_seeds_an_existing_claude_dir(conn, home, tmp_path):
    a = claude_md(tmp_path)
    syncstate.agents_md().write_text("# eki\n")
    assert len(instrsync.tick(conn)) == 1
    assert a.read_text() == "# eki\n"


def test_a_missing_claude_dir_is_not_created(conn, home, tmp_path):
    syncstate.agents_md().write_text("# eki\n")
    assert instrsync.tick(conn) == []
    assert not (tmp_path / "user" / ".claude").exists()
    assert state()["note"] == "one missing" and state()["count"] == 0


def test_equal_files_write_nothing(conn, home, tmp_path):
    a = claude_md(tmp_path)
    a.write_text("same\n")
    syncstate.agents_md().write_text("same\n")
    before = (a.stat().st_mtime, syncstate.agents_md().stat().st_mtime)
    assert instrsync.tick(conn) == []
    assert (a.stat().st_mtime, syncstate.agents_md().stat().st_mtime) == before
    assert list(syncstate.backups().iterdir()) == []
    assert instrsync.diff() == ""
    assert state()["note"] == "equal" and state()["count"] == 1 and state()["clashes"] == []


def test_a_newer_claude_md_wins(conn, home, tmp_path):
    a, b = claude_md(tmp_path), syncstate.agents_md()
    b.write_text("old line\n")
    a.write_text("new line\n")
    age(b, 100)
    said = instrsync.tick(conn)
    assert len(said) == 3
    assert b.read_text() == "new line\n" and a.read_text() == "new line\n"
    backups = list(syncstate.backups().iterdir())
    assert len(backups) == 1 and backups[0].name.startswith("AGENTS.md-")
    assert backups[0].suffix == ".md" and backups[0].read_text() == "old line\n"
    d = instrsync.diff()
    assert f"--- {b}" in d and f"+++ {a}" in d
    assert "-old line" in d and "+new line" in d
    s = state()
    assert s["count"] == 1 and s["note"] == "equal"
    assert s["clashes"] == [f"drift: CLAUDE.md replaced AGENTS.md, backup {backups[0]}"]


def test_a_newer_agents_md_wins(conn, home, tmp_path):
    a, b = claude_md(tmp_path), syncstate.agents_md()
    a.write_text("owner's old\n")
    b.write_text("eki's new\n")
    age(a, 100)
    instrsync.tick(conn)
    assert a.read_text() == "eki's new\n"
    backups = list(syncstate.backups().iterdir())
    assert len(backups) == 1 and backups[0].name.startswith("CLAUDE.md-")
    assert backups[0].read_text() == "owner's old\n"
    assert f"--- {a}" in instrsync.diff() and f"+++ {b}" in instrsync.diff()
    assert state()["clashes"][0].startswith("drift: AGENTS.md replaced CLAUDE.md")


def test_a_second_pass_writes_nothing_and_keeps_the_clash(conn, home, tmp_path):
    a, b = claude_md(tmp_path), syncstate.agents_md()
    b.write_text("old\n")
    a.write_text("new\n")
    age(b, 100)
    instrsync.tick(conn)
    clashes = state()["clashes"]
    mtimes = (a.stat().st_mtime, b.stat().st_mtime)
    assert instrsync.tick(conn) == []
    assert (a.stat().st_mtime, b.stat().st_mtime) == mtimes
    assert len(list(syncstate.backups().iterdir())) == 1
    assert state()["clashes"] == clashes and state()["note"] == "equal"


def test_a_symlinked_claude_md_stays_a_link(conn, home, tmp_path):
    real = tmp_path / "elsewhere.md"
    real.write_text("old\n")
    a = claude_md(tmp_path)
    a.symlink_to(real)
    syncstate.agents_md().write_text("new\n")
    age(real, 100)
    instrsync.tick(conn)
    assert a.is_symlink() and real.read_text() == "new\n"


def test_the_sync_tick_runs_it(conn, home, tmp_path):
    claude_md(tmp_path).write_text("# mine\n")
    syncstate.tick(conn)
    assert syncstate.agents_md().read_text() == "# mine\n"
