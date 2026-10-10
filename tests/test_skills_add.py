"""Skills come from a folder or a git URL; where from is remembered and undone on remove."""
import json
import os
import subprocess

import pytest

from eki import paths, skills


def _skill(root, name, desc="does a thing"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    return d


def _git(where, *args):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(where), *args], check=True,
                   capture_output=True)


@pytest.fixture
def bare(tmp_path):
    """A local bare repo holding skills/tidy/SKILL.md."""
    src = tmp_path / "src"
    _skill(src / "skills", "tidy", "keeps things tidy")
    _git(src.parent, "init", "-q", str(src))
    _git(src, "add", ".")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "skills")
    repo = tmp_path / "skills.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(repo)], check=True)
    return repo


def test_add_from_folder_records_source_and_links(tmp_path):
    folder = _skill(tmp_path / "mine", "notes")
    assert skills.add(str(folder)) == "notes"
    got = skills.all_skills()
    assert [(s["name"], s["source"]) for s in got] == [("notes", str(folder.resolve()))]
    assert got[0]["added_at"] > 0
    assert (skills.agents_dir() / "notes" / "SKILL.md").exists()     # add syncs Codex itself
    assert skills.remove("notes")
    assert not os.path.lexists(skills.agents_dir() / "notes")
    assert "notes" not in json.loads(paths.config("skill-sources").read_text())


def test_add_from_git_url_with_subdir(bare):
    url = f"file://{bare}#skills/tidy"
    assert skills.is_git(url)
    assert skills.add(url) == "tidy"
    s = skills.all_skills()[0]
    assert (s["name"], s["source"], s["description"]) == ("tidy", url, "keeps things tidy")
    assert not (paths.skills() / "tidy" / ".git").exists()
    assert (skills.agents_dir() / "tidy").is_symlink()
    assert list((paths.home() / "tmp").iterdir()) == []             # the clone is gone
    assert skills.remove("tidy")
    assert skills.all_skills() == [] and skills.sources() == {}
    assert not os.path.lexists(skills.agents_dir() / "tidy")


def test_failed_clone_leaves_nothing(bare):
    with pytest.raises(ValueError, match="no SKILL.md"):
        skills.add(f"file://{bare}#skills")                         # a folder, not a skill
    with pytest.raises(ValueError, match="outside"):
        skills.add(f"file://{bare}#../..")
    with pytest.raises(ValueError, match="can't clone"):
        skills.add(f"file://{bare}-missing")
    assert list((paths.home() / "tmp").iterdir()) == []
    assert skills.all_skills() == []


def test_what_counts_as_git(tmp_path):
    assert skills.is_git("https://github.com/anthropics/skills#skills/pdf")
    assert skills.is_git("git@github.com:anthropics/skills.git")
    assert skills.is_git("anthropics/skills#skills/pdf")
    assert skills._clone_url("anthropics/skills") == "https://github.com/anthropics/skills.git"
    assert not skills.is_git(str(tmp_path))
    assert not skills.is_git("~/somewhere")


def test_cli_add_takes_a_url(bare, capsys):
    from eki.cli import skills as cli
    import argparse
    p = argparse.ArgumentParser()
    cli.add(p)
    assert cli.run(p.parse_args(["add", f"file://{bare}#skills/tidy"])) == 0
    assert "added tidy" in capsys.readouterr().out
    assert cli.run(p.parse_args(["add", f"file://{bare}#nope"])) == 1
