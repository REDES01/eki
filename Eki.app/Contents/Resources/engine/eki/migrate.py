"""Carry an older install across the rename from "hub" to "eki".

Everything that had the old name in it moves here: the data directory, the
database, the scripts that start local models, the rows that point at those
scripts, the status-line bridge in Claude Code's settings, and the login
agent. It runs at startup, does nothing at all once there is nothing left to
move, and never touches what it has already moved.

Anyone installing for the first time never sees this file do anything.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import List

OLD_HOME = Path("~/.hub").expanduser()
NEW_HOME = Path("~/.eki").expanduser()
OLD_LABEL = "local.hub.engine"
OLD_PLIST = Path("~/Library/LaunchAgents/local.hub.engine.plist").expanduser()
CLAUDE_SETTINGS = Path("~/.claude/settings.json").expanduser()
OLD_MARKER = "hub.quota.statusline_bridge"
NEW_MARKER = "eki.quota.statusline_bridge"


def run(root: Path | None = None) -> List[str]:
    """Move an old install to the new name. Returns what it did, for the log."""
    notes: List[str] = []
    _move_home(notes)
    _rename_db(notes)
    _rewrite_scripts(notes)
    _rewrite_provider_rows(notes)
    _rewrite_claude_bridge(root, notes)
    _retire_old_agent(notes)
    return notes


def _move_home(notes: List[str]) -> None:
    if NEW_HOME.exists() or not OLD_HOME.exists():
        return
    shutil.move(str(OLD_HOME), str(NEW_HOME))
    notes.append(f"moved {OLD_HOME} to {NEW_HOME}")


def _rename_db(notes: List[str]) -> None:
    old, new = NEW_HOME / "hub.db", NEW_HOME / "eki.db"
    if old.exists() and not new.exists():
        old.rename(new)
        notes.append("renamed hub.db to eki.db")


def _rewrite_scripts(notes: List[str]) -> None:
    """Model start/stop scripts hold their own paths and log locations."""
    for script in sorted((NEW_HOME / "models").glob("*/*.sh")):
        text = script.read_text()
        if "/.hub/" not in text:
            continue
        script.write_text(text.replace("/.hub/", "/.eki/"))
        notes.append(f"repointed {script.name} for {script.parent.name}")


def _rewrite_provider_rows(notes: List[str]) -> None:
    """Providers store the commands that start their server."""
    db = NEW_HOME / "eki.db"
    if not db.exists():
        return
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT key, runtime FROM providers").fetchall()
    except sqlite3.DatabaseError:
        conn.close()
        return
    moved = 0
    for key, runtime in rows:
        if not runtime or "/.hub/" not in runtime:
            continue
        conn.execute("UPDATE providers SET runtime = ? WHERE key = ?",
                     (runtime.replace("/.hub/", "/.eki/"), key))
        moved += 1
    if moved:
        conn.commit()
        notes.append(f"repointed {moved} provider runtime(s)")
    conn.close()


def _rewrite_claude_bridge(root: Path | None, notes: List[str]) -> None:
    """The opt-in status line runs a module whose package just got renamed."""
    try:
        settings = json.loads(CLAUDE_SETTINGS.read_text())
    except (OSError, ValueError):
        return
    line = settings.get("statusLine")
    if not isinstance(line, dict):
        return
    command = str(line.get("command", ""))
    if OLD_MARKER not in command:
        return
    # The command carries an interpreter path and a working directory, both of
    # which the rename may have moved. Rebuilding it beats patching strings.
    try:
        from .quota import claude_bridge
        fixed = claude_bridge.bridge_command()
    except Exception:                               # noqa: BLE001
        fixed = command.replace(OLD_MARKER, NEW_MARKER)
        if root is not None:
            fixed = fixed.replace("/hub &&", f"/{root.name} &&")
    line["command"] = fixed
    settings["statusLine"] = line
    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    notes.append("repointed Claude Code's status line")


def _retire_old_agent(notes: List[str]) -> None:
    """The old login agent starts a module that no longer exists."""
    if not OLD_PLIST.exists():
        return
    subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{OLD_LABEL}"],
                   capture_output=True)
    OLD_PLIST.unlink()
    notes.append("removed the old login agent — `eki agent install` replaces it")


def _uid() -> int:
    import os
    return os.getuid()
