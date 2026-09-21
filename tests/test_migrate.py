# SPDX-License-Identifier: Apache-2.0
"""Carrying an install across the rename, without losing anything."""
import json
import sqlite3
from pathlib import Path

import pytest

from eki import migrate


@pytest.fixture
def old_install(tmp_path, monkeypatch):
    """A believable ~/.hub: database, model scripts, a Claude status line."""
    old = tmp_path / ".hub"
    (old / "models" / "qwen").mkdir(parents=True)
    (old / "models" / "qwen" / "start.sh").write_text(
        'exec python -m mlx_lm server >> "$HOME/.hub/models/qwen/server.log" 2>&1\n')
    (old / "models" / "qwen" / "stop.sh").write_text("# stops qwen\n")
    conn = sqlite3.connect(old / "hub.db")
    conn.execute("CREATE TABLE providers (key TEXT PRIMARY KEY, runtime TEXT)")
    conn.execute("INSERT INTO providers VALUES ('qwen', ?)",
                 (json.dumps({"port": 8080, "start": "~/.hub/models/qwen/start.sh"}),))
    conn.execute("CREATE TABLE turns (id INTEGER)")
    conn.execute("INSERT INTO turns VALUES (7)")
    conn.commit()
    conn.close()

    claude = tmp_path / ".claude" / "settings.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({
        "theme": "dark",
        "statusLine": {"type": "command",
                       "command": "cd /Users/x/hub && python -m hub.quota.statusline_bridge"},
    }))

    plist = tmp_path / "LaunchAgents" / "local.hub.engine.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")

    monkeypatch.setattr(migrate, "OLD_HOME", old)
    monkeypatch.setattr(migrate, "NEW_HOME", tmp_path / ".eki")
    monkeypatch.setattr(migrate, "CLAUDE_SETTINGS", claude)
    monkeypatch.setattr(migrate, "OLD_PLIST", plist)
    return tmp_path


def test_everything_moves_and_keeps_pointing_at_itself(old_install):
    notes = migrate.run(Path("/Users/x/eki"))
    new = old_install / ".eki"

    assert not (old_install / ".hub").exists()
    assert (new / "eki.db").exists() and not (new / "hub.db").exists()
    # history came with it
    conn = sqlite3.connect(new / "eki.db")
    assert conn.execute("SELECT id FROM turns").fetchone()[0] == 7
    runtime = json.loads(conn.execute("SELECT runtime FROM providers").fetchone()[0])
    conn.close()
    assert runtime["start"] == "~/.eki/models/qwen/start.sh"

    assert "/.eki/models/qwen/server.log" in (new / "models" / "qwen" / "start.sh").read_text()

    settings = json.loads(migrate.CLAUDE_SETTINGS.read_text())
    # rebuilt from where this install actually lives, not patched by hand
    command = settings["statusLine"]["command"]
    assert command.endswith("-m eki.quota.statusline_bridge")
    assert "hub" not in command
    assert settings["theme"] == "dark"              # nothing else touched

    assert not migrate.OLD_PLIST.exists()
    assert len(notes) >= 5


def test_running_it_again_does_nothing(old_install):
    migrate.run(Path("/Users/x/eki"))
    before = (old_install / ".eki" / "eki.db").stat().st_mtime
    assert migrate.run(Path("/Users/x/eki")) == []
    assert (old_install / ".eki" / "eki.db").stat().st_mtime == before


def test_a_fresh_install_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "OLD_HOME", tmp_path / "nothing-here")
    monkeypatch.setattr(migrate, "NEW_HOME", tmp_path / ".eki")
    monkeypatch.setattr(migrate, "CLAUDE_SETTINGS", tmp_path / "no-claude.json")
    monkeypatch.setattr(migrate, "OLD_PLIST", tmp_path / "no-plist")
    assert migrate.run() == []
    assert not (tmp_path / ".eki").exists()
