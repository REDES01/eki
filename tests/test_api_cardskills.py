"""A skills card on the project page: its skills in the card JSON, add and remove over the API."""
import shutil

import pytest

from eki import api_cards, apiroutes, components


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder)))
    conn.commit()


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def card(conn, name):
    return api_cards.card_of(conn, "p1", name)


def skill(where, name, desc="does a thing"):
    where.mkdir(parents=True)
    (where / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    return where


@pytest.fixture
def cards(conn, proj):
    project(conn, proj)
    base = proj / "components"
    shutil.copytree(components.templates_dir() / "skills", base / "skills")
    shutil.copytree(components.templates_dir() / "voice", base / "voice")
    return base


def test_card_json_lists_skills_and_broken_ones(conn, cards):
    skill(cards / "skills" / "holo", "holo", "makes cards")
    (cards / "skills" / "bad").mkdir()
    (cards / "skills" / "bad" / "SKILL.md").write_text("no front matter\n")
    got = card(conn, "skills")
    assert got["skills"] == [{"name": "holo", "description": "makes cards"}]
    assert [b["path"].endswith("bad") for b in got["broken_skills"]] == [True]
    assert "front matter" in got["broken_skills"][0]["error"]
    assert "skills" not in card(conn, "voice") and "broken_skills" not in card(conn, "voice")


def test_add_then_refuse_then_replace(conn, cards, tmp_path):
    src = skill(tmp_path / "src" / "design", "design", "first")
    got = call(conn, "POST", "/api/projects/p1/cards/skills/skills", {"source": str(src)})
    assert got["added"] == "design" and got["card"]["skills"] == [{"name": "design", "description": "first"}]
    assert (cards / "skills" / "design" / "SKILL.md").is_file()
    (src / "SKILL.md").write_text("---\nname: design\ndescription: second\n---\n")
    with pytest.raises(ValueError, match="already there"):
        call(conn, "POST", "/api/projects/p1/cards/skills/skills", {"source": str(src)})
    assert card(conn, "skills")["skills"][0]["description"] == "first"
    got = call(conn, "POST", "/api/projects/p1/cards/skills/skills", {"source": str(src), "replace": True})
    assert got["card"]["skills"] == [{"name": "design", "description": "second"}]


def test_a_folder_without_skill_md_is_refused(conn, cards, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="SKILL.md"):
        call(conn, "POST", "/api/projects/p1/cards/skills/skills", {"source": str(tmp_path / "empty")})
    with pytest.raises(ValueError):
        call(conn, "POST", "/api/projects/p1/cards/skills/skills", {"source": " "})


def test_delete_removes_and_unknown_is_404(conn, cards):
    skill(cards / "skills" / "holo", "holo")
    assert call(conn, "DELETE", "/api/projects/p1/cards/skills/skills/holo") == {"removed": "holo"}
    assert not (cards / "skills" / "holo").exists() and card(conn, "skills")["skills"] == []
    with pytest.raises(KeyError):
        call(conn, "DELETE", "/api/projects/p1/cards/skills/skills/holo")
    with pytest.raises(KeyError):
        call(conn, "DELETE", "/api/projects/p1/cards/nope/skills/holo")


def test_other_kinds_and_unknown_cards_are_refused(conn, cards, tmp_path):
    src = skill(tmp_path / "design", "design")
    with pytest.raises(ValueError, match="not a skills card"):
        call(conn, "POST", "/api/projects/p1/cards/voice/skills", {"source": str(src)})
    with pytest.raises(ValueError, match="not a skills card"):
        call(conn, "DELETE", "/api/projects/p1/cards/voice/skills/x")
    with pytest.raises(KeyError):
        call(conn, "POST", "/api/projects/p1/cards/nope/skills", {"source": str(src)})
    assert not (cards / "voice" / "design").exists()


def test_the_panel_part_is_loaded_and_names_its_routes():
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "eki" / "web"
    assert '<script src="/ui/projectcardskills.js"></script>' in (web / "index.html").read_text()
    js = (web / "projectcardskills.js").read_text()
    for name in ('"/skills"', '"/skills/"', '"DELETE"', "confirm(", "replace", "broken_skills", "Add skill"):
        assert name in js, name
    assert "projectCardSkills.draw" in (web / "projectcardpanel.js").read_text()
