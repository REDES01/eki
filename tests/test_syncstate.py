"""The sync root: folders, client skills, tree hashes, baselines, state, and the tick."""
import json
import sys
import types

from eki import housekeep, syncstate


def skill(root, account, name, body="# s\n"):
    d = root / ".claude" / "skills" / "synced" / account / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body)
    return d


def test_the_paths_sit_under_the_test_homes(home, tmp_path):
    user = tmp_path / "user"
    assert syncstate.root() == home / "sync" and syncstate.root().is_dir()
    assert syncstate.memory_dir() == home / "sync" / "claude-memory"
    assert not syncstate.memory_dir().exists()
    assert syncstate.outbox() == home / "sync" / "outbox" and syncstate.outbox().is_dir()
    assert syncstate.backups() == home / "sync" / "backups" and syncstate.backups().is_dir()
    assert syncstate.claude_dir() == user / ".claude" and not syncstate.claude_dir().exists()
    assert syncstate.agents_md() == home / "AGENTS.md"


def test_client_skills_across_accounts_first_wins(tmp_path):
    user = tmp_path / "user"
    assert syncstate.client_skills() == {}
    a = skill(user, "aaa", "shared", "first")
    skill(user, "bbb", "shared", "second")
    b = skill(user, "bbb", "only-b")
    (user / ".claude" / "skills" / "synced" / "aaa" / "no-skill-md").mkdir()
    (user / ".claude" / "skills" / "synced" / "manifest.json").write_text("{}")
    assert syncstate.client_skills() == {"shared": a, "only-b": b}


def test_a_synced_folder_with_no_skill_md_is_empty(tmp_path):
    (tmp_path / "user" / ".claude" / "skills" / "synced" / "acct" / "bare").mkdir(parents=True)
    assert syncstate.client_skills() == {}


def test_tree_hash(tmp_path):
    d = tmp_path / "s"
    assert syncstate.tree_hash(d) == ""
    d.mkdir()
    (d / "SKILL.md").write_text("one")
    first = syncstate.tree_hash(d)
    assert first and first == syncstate.tree_hash(d)
    (d / ".DS_Store").write_bytes(b"\0junk")
    assert syncstate.tree_hash(d) == first
    (d / "SKILL.md").write_text("two")
    second = syncstate.tree_hash(d)
    assert second != first
    (d / "refs").mkdir()
    (d / "refs" / "a.md").write_text("x")
    assert syncstate.tree_hash(d) not in (first, second)


def test_baseline_round_trips_and_a_bad_file_is_empty():
    assert syncstate.baseline("skills-in") == {}
    syncstate.set_baseline("skills-in", {"eki": "abc"})
    assert syncstate.baseline("skills-in") == {"eki": "abc"}
    assert not list(syncstate.root().glob(".*tmp*"))
    (syncstate.root() / "skills-in.json").write_text("{not json")
    assert syncstate.baseline("skills-in") == {}


def test_record_and_status():
    st = syncstate.status()
    assert list(st) == list(syncstate.DIRECTIONS)
    assert all(v == {"at": None, "count": 0, "clashes": [], "note": ""} for v in st.values())
    syncstate.record("skills-in", count=3, clashes=["eki"], note="ok")
    syncstate.record("memory-in", count=7)
    st = syncstate.status()
    assert st["skills-in"]["count"] == 3 and st["skills-in"]["clashes"] == ["eki"]
    assert st["skills-in"]["note"] == "ok" and st["skills-in"]["at"] > 0
    assert st["memory-in"]["count"] == 7 and st["memory-in"]["clashes"] == []
    assert st["instructions"]["at"] is None
    saved = json.loads((syncstate.root() / "state.json").read_text())
    assert set(saved) == {"skills-in", "memory-in"}


def fake(monkeypatch, name, tick):
    mod = types.ModuleType(f"eki.{name}")
    mod.tick = tick
    monkeypatch.setitem(sys.modules, f"eki.{name}", mod)


def test_tick_skips_missing_modules_and_a_raising_one_is_a_fault(conn, monkeypatch):
    for name in syncstate.MODULES:                       # whatever has been built, none of it runs here
        monkeypatch.setitem(sys.modules, f"eki.{name}", None)
    assert syncstate.tick(conn) == []
    ran = []

    def boom(c):
        ran.append("skillout")
        raise RuntimeError("outbox went wrong")

    fake(monkeypatch, "skillsync", lambda c: ran.append("skillsync") or ["imported 2"])
    fake(monkeypatch, "skillout", boom)
    fake(monkeypatch, "memoutbox", lambda c: ran.append("memoutbox") or ["wrote 1"])
    assert syncstate.tick(conn) == ["imported 2", "wrote 1"]
    assert ran == ["skillsync", "skillout", "memoutbox"]
    rows = conn.execute("SELECT data FROM journal WHERE kind='fault'").fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0]["data"])
    assert data["where"] == "sync.skillout" and "outbox went wrong" in data["traceback"]


def test_a_broken_import_is_a_fault(conn, monkeypatch):
    for name in syncstate.MODULES:
        monkeypatch.setitem(sys.modules, f"eki.{name}", None)
    monkeypatch.delitem(sys.modules, "eki.instrsync")
    real = syncstate.importlib.import_module

    def imp(name):
        if name == "eki.instrsync":
            raise ModuleNotFoundError("No module named 'yaml'", name="yaml")
        return real(name)

    monkeypatch.setattr(syncstate.importlib, "import_module", imp)
    assert syncstate.tick(conn) == []
    data = json.loads(conn.execute("SELECT data FROM journal WHERE kind='fault'").fetchone()["data"])
    assert data["where"] == "sync.instrsync"


def test_housekeeping_runs_sync():
    assert ("sync", syncstate.tick) in housekeep.STEPS
