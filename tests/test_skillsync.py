"""Skills, Claude client -> eki: import, refresh, removal and clashes, all in the test homes."""
import shutil

from eki import paths, skills, skillsync, syncstate


def client(tmp_path, name, body="---\nname: x\ndescription: d\n---\n# s\n", account="acct"):
    d = tmp_path / "user" / ".claude" / "skills" / "synced" / account / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body)
    return d


def stored(name, body="# mine\n", source=None):
    d = paths.skills() / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body)
    if source:
        skills.set_source(name, source)
    return d


def state():
    return syncstate.status()["skills-in"]


def test_imports_every_client_skill_with_source_and_baseline(conn, tmp_path):
    a = client(tmp_path, "alpha")
    (a / "ref.md").write_text("more")
    b = client(tmp_path, "beta", account="other")
    said = skillsync.tick(conn)
    assert said == ["skills: imported alpha from Claude", "skills: imported beta from Claude"]
    assert (paths.skills() / "alpha" / "ref.md").read_text() == "more"
    assert skills.sources() == {"alpha": "claude", "beta": "claude"}
    base = syncstate.baseline("skills")
    assert base["alpha"] == {"hash": syncstate.tree_hash(a), "from": str(a)}
    assert base["beta"]["hash"] == syncstate.tree_hash(paths.skills() / "beta") == syncstate.tree_hash(b)
    assert state()["count"] == 2 and state()["clashes"] == []
    assert not list((paths.home() / "tmp").iterdir())


def test_refresh_when_the_client_changes_and_nothing_when_it_does_not(conn, tmp_path, monkeypatch):
    a = client(tmp_path, "alpha")
    skillsync.tick(conn)
    copies = []
    real = shutil.copytree
    monkeypatch.setattr(shutil, "copytree", lambda *a, **k: copies.append(a) or real(*a, **k))
    assert skillsync.tick(conn) == []
    assert copies == []                               # an idle pass never copies
    (a / "SKILL.md").write_text("# new\n")
    assert skillsync.tick(conn) == ["skills: refreshed alpha from Claude"]
    assert (paths.skills() / "alpha" / "SKILL.md").read_text() == "# new\n"
    assert syncstate.baseline("skills")["alpha"]["hash"] == syncstate.tree_hash(a)
    assert skillsync.tick(conn) == []


def test_removed_when_the_client_folder_goes(conn, tmp_path):
    a = client(tmp_path, "alpha")
    client(tmp_path, "beta")
    skillsync.tick(conn)
    shutil.rmtree(a)
    assert skillsync.tick(conn) == ["skills: removed alpha (gone from Claude)"]
    assert not (paths.skills() / "alpha").exists()
    assert skills.sources() == {"beta": "claude"}
    assert "alpha" not in syncstate.baseline("skills")
    assert state()["count"] == 1


def test_a_skill_edited_in_the_store_is_kept_and_shown(conn, tmp_path):
    a = client(tmp_path, "alpha")
    skillsync.tick(conn)
    (paths.skills() / "alpha" / "SKILL.md").write_text("# edited here\n")
    (a / "SKILL.md").write_text("# client moved on\n")
    assert skillsync.tick(conn) == []
    assert (paths.skills() / "alpha" / "SKILL.md").read_text() == "# edited here\n"
    assert state()["clashes"] == ["alpha: kept eki's copy (edited in eki since the import)"]
    shutil.rmtree(a)
    assert skillsync.tick(conn) == []                  # not removed either
    assert (paths.skills() / "alpha" / "SKILL.md").read_text() == "# edited here\n"
    assert skills.sources()["alpha"] == "claude"


def test_learned_and_folder_skills_with_the_same_name_win(conn, tmp_path):
    client(tmp_path, "taught")
    client(tmp_path, "local")
    client(tmp_path, "plain")
    stored("taught", source="learned")
    stored("local", source="/somewhere/local")
    stored("plain")
    assert skillsync.tick(conn) == []
    for name in ("taught", "local", "plain"):
        assert (paths.skills() / name / "SKILL.md").read_text() == "# mine\n"
    assert skills.sources() == {"taught": "learned", "local": "/somewhere/local"}
    assert syncstate.baseline("skills") == {}
    assert state()["clashes"] == ["local: kept eki's copy (added from /somewhere/local)",
                                  "plain: kept eki's copy (added in eki, source unknown)",
                                  "taught: kept eki's copy (learned in eki)"]
    assert state()["count"] == 0


def test_a_lost_folder_with_a_claude_source_is_imported_again(conn, tmp_path):
    client(tmp_path, "alpha")
    skillsync.tick(conn)
    shutil.rmtree(paths.skills() / "alpha")
    assert skillsync.tick(conn) == ["skills: imported alpha from Claude"]
    assert (paths.skills() / "alpha" / "SKILL.md").is_file()


def test_no_claude_folder_is_no_error_and_count_zero(conn, tmp_path):
    assert not (tmp_path / "user" / ".claude").exists()
    assert skillsync.tick(conn) == []
    assert state()["count"] == 0 and state()["clashes"] == [] and state()["at"]
    assert not (tmp_path / "user" / ".claude").exists()


def test_the_sync_tick_runs_it(conn, tmp_path):
    client(tmp_path, "alpha")
    assert "skills: imported alpha from Claude" in syncstate.tick(conn)
