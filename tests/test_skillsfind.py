"""Discover lists anthropics/skills through gh (faked here), cached a day."""
import base64
import json

from eki import github, paths, skills, skillsfind


def _fake(calls):
    def gh(args, cwd=None):
        calls.append(args)
        path = args[1]
        if path == "repos/anthropics/skills/contents/skills":
            return json.dumps([{"name": "pdf", "path": "skills/pdf", "type": "dir"},
                               {"name": "xlsx", "path": "skills/xlsx", "type": "dir"},
                               {"name": "README.md", "path": "skills/README.md", "type": "file"}])
        name = path.split("/")[-2]
        md = f"---\nname: {name}\ndescription: \"Work with {name} files\"\n---\n"
        return json.dumps({"name": "SKILL.md", "content": base64.b64encode(md.encode()).decode()})
    return gh


def test_discover_lists_marks_installed_and_caches(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(github, "_gh", _fake(calls))
    mine = tmp_path / "pdf"
    mine.mkdir()
    (mine / "SKILL.md").write_text("---\nname: pdf\ndescription: mine\n---\n")
    skills.add(str(mine))
    got = skillsfind.discover()
    assert got["why"] is None
    assert [(s["name"], s["description"], s["repo"], s["path"], s["installed"]) for s in got["skills"]] == [
        ("pdf", "Work with pdf files", "anthropics/skills", "skills/pdf", True),
        ("xlsx", "Work with xlsx files", "anthropics/skills", "skills/xlsx", False)]
    assert (paths.home() / "cache" / "discover-skills.json").is_file()
    n = len(calls)
    again = skillsfind.discover()                      # from the cache, no gh
    assert len(calls) == n and [s["name"] for s in again["skills"]] == ["pdf", "xlsx"]
    skills.remove("pdf")
    assert [s["installed"] for s in skillsfind.discover()["skills"]] == [False, False]
    skillsfind.discover(refresh=True)
    assert len(calls) > n


def test_without_gh_is_empty_with_a_why():
    got = skillsfind.discover()                        # the test gh always fails
    assert got["skills"] == [] and got["why"]
    assert not (paths.home() / "cache" / "discover-skills.json").exists()
