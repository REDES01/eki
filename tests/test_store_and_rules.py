import os
from pathlib import Path

from eki import mcp, skills

ROOT = Path(__file__).resolve().parent.parent


def make_skill(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: does {name}\n---\nbody\n")
    return d


def test_skills_reach_both_programs(tmp_path):
    skills.add(str(make_skill(tmp_path, "tidy")))
    assert [s["name"] for s in skills.all_skills()] == ["tidy"]
    plugin = Path(skills.claude_plugin())
    assert (plugin / ".claude-plugin" / "plugin.json").exists()
    assert (plugin / "skills" / "tidy" / "SKILL.md").exists()
    assert skills.sync_codex() == ["tidy"]
    assert (skills.agents_dir() / "tidy" / "SKILL.md").exists()


def test_skills_never_replace_what_eki_didnt_make(tmp_path):
    theirs = skills.agents_dir() / "tidy"
    theirs.mkdir(parents=True)
    skills.add(str(make_skill(tmp_path, "tidy")))
    assert skills.sync_codex() == []
    assert not theirs.is_symlink()


def test_removed_skill_link_goes(tmp_path):
    skills.add(str(make_skill(tmp_path, "gone")))
    skills.sync_codex()
    skills.remove("gone")
    assert not os.path.lexists(skills.agents_dir() / "gone")


def test_mcp_registry():
    mcp.add("fs", command="npx", args=["srv"])
    assert mcp.claude_config().endswith("mcp.json")
    assert 'mcp_servers.fs.command="npx"' in mcp.codex_config()
    assert mcp.remove("fs") and mcp.claude_config() is None


def test_no_file_over_400_lines():
    big = [(p.relative_to(ROOT), n) for p in (ROOT / "eki").rglob("*.py")
           if (n := len(p.read_text().splitlines())) > 400]
    assert not big, f"split these: {big}"
