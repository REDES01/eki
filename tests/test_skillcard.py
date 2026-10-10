"""The skills card: a project's own skills, kept in the project and handed only to its runs."""
import json
import os
import subprocess

import pytest

from eki import api_cards, components, paths, skills, workspace
from eki.components import skillcard


def _skill(root, name, desc="does a thing"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    (d / "notes.txt").write_text("extra\n")
    return d


def _git(where, *args):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(where), *args], check=True,
                   capture_output=True)


@pytest.fixture
def card(proj):
    return proj / "components" / "skills"


def test_held_lists_good_and_reports_broken(card):
    _skill(card, "tidy", "keeps things tidy")
    _skill(card, "alpha", "first")
    (card / "empty").mkdir()
    (card / "plain").mkdir()
    (card / "plain" / "SKILL.md").write_text("# no front matter\n")
    (card / "nodesc").mkdir()
    (card / "nodesc" / "SKILL.md").write_text("---\nname: nodesc\n---\n")
    (card / ".hidden").mkdir()
    good, broken = skillcard.held(card)
    assert [(s["name"], s["description"]) for s in good] == [("alpha", "first"), ("tidy", "keeps things tidy")]
    assert good[0]["path"] == str(card / "alpha")
    assert {os.path.basename(p) for p, _ in broken} == {"empty", "plain", "nodesc"}
    assert skillcard.held(card / "missing") == ([], [])


def test_add_refuses_twice_then_replaces_and_remove(card, tmp_path):
    src = _skill(tmp_path / "mine", "tidy", "v1")
    assert skillcard.add(card, str(src)) == "tidy"
    assert json.loads((card / "component.json").read_text())["kind"] == "skills"
    assert (card / "tidy" / "notes.txt").is_file()
    (src / "SKILL.md").write_text("---\nname: tidy\ndescription: v2\n---\n")
    with pytest.raises(ValueError, match="--replace"):
        skillcard.add(card, str(src))
    assert skillcard.held(card)[0][0]["description"] == "v1"
    assert skillcard.add(card, str(src), replace=True) == "tidy"
    assert skillcard.held(card)[0][0]["description"] == "v2"
    with pytest.raises(ValueError, match="SKILL.md"):
        skillcard.add(card, str(tmp_path))
    assert not skillcard.remove(card, "component.json")
    assert not skillcard.remove(card, "../skills")
    assert skillcard.remove(card, "tidy") and not (card / "tidy").exists()
    assert not skillcard.remove(card, "tidy")
    assert (card / "component.json").is_file()
    assert not paths.config("skill-sources").exists() or "tidy" not in skills.sources()
    assert not (paths.skills() / "tidy").exists()


def test_add_from_a_file_git_url(card, tmp_path):
    src = tmp_path / "src"
    _skill(src / "skills", "comfy", "drives comfyui")
    _git(tmp_path, "init", "-q", str(src))
    _git(src, "add", ".")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "s")
    assert skillcard.add(card, f"file://{src}#skills/comfy") == "comfy"
    assert [s["name"] for s in skillcard.held(card)[0]] == ["comfy"]
    with pytest.raises(ValueError, match="clone"):
        skillcard.add(card, f"file://{tmp_path}/nope.git")


def test_the_card_is_a_component(card, proj):
    _skill(card, "tidy")
    skillcard._ensure(card)
    found, errors = components.find(proj)
    assert found["skills"].kind == "skills" and errors == []
    assert "tidy — does a thing" in skillcard._listing(card)
    assert "skills" in [t["name"] for t in api_cards.templates()]


def test_cards_for_project_worktree_and_elsewhere(card, proj, tmp_path):
    _skill(card, "tidy")
    skillcard._ensure(card)
    want = [(proj / "components" / "skills").resolve()]
    assert skillcard.cards_for(proj) == want
    wt = tmp_path / "wt"
    workspace.git(proj, "worktree", "add", "-q", "-b", "side", str(wt))
    assert skillcard.cards_for(wt) == want
    other = tmp_path / "other"
    other.mkdir()
    assert skillcard.cards_for(other) == []
    assert skillcard.cards_for(None) == []
    pool = components.pool_dir() / "skills"
    skillcard._ensure(pool)
    _skill(pool, "everywhere")
    assert skillcard.cards_for(other) == []


def test_claude_plugin_links_only_good_skills(card, tmp_path):
    assert skillcard.claude_plugin([card]) is None
    _skill(card, "tidy")
    (card / "broken").mkdir()
    root = skillcard.claude_plugin([card])
    assert root.startswith(str(paths.home()))
    manifest = json.loads((tmp_path / root / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"].startswith("eki-project-")
    links = sorted(os.listdir(os.path.join(root, "skills")))
    assert links == ["tidy"]
    assert os.readlink(os.path.join(root, "skills", "tidy")) == str(card / "tidy")
    skillcard.remove(card, "tidy")
    _skill(card, "neat")
    assert skillcard.claude_plugin([card]) == root
    assert sorted(os.listdir(os.path.join(root, "skills"))) == ["neat"]


def test_codex_dir_is_per_run(card, tmp_path):
    agents = skills.agents_dir()
    assert skillcard.codex_dir([card], "r1") is None
    _skill(card, "tidy")
    one, two = skillcard.codex_dir([card], "r1"), skillcard.codex_dir([card], "r2")
    assert one != two and one == str(paths.home() / "run-skills" / "r1")
    assert os.listdir(one) == ["tidy"]
    assert not agents.exists() or "tidy" not in os.listdir(agents)


def test_store_add_still_overwrites(tmp_path):
    src = _skill(tmp_path / "mine", "notes", "v1")
    assert skills.add(str(src)) == "notes"
    (src / "SKILL.md").write_text("---\nname: notes\ndescription: v2\n---\n")
    assert skills.add(str(src)) == "notes"
    assert skills.all_skills()[0]["description"] == "v2"
