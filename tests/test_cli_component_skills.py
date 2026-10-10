"""`eki component skills add | remove`: skills into and out of a project's skills card."""
import json

from eki import db, projectchats
from eki.cli import main


def _skill(where, name, desc="does a thing", body="hi\n"):
    d = where / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}")
    (d / "notes.txt").write_text("extra file\n")
    return d


def test_add_copies_into_the_card_and_makes_component_json(proj, tmp_path, capsys):
    src = _skill(tmp_path / "src", "holo-card-maker", "makes holo cards")
    assert main(["component", "skills", "add", str(proj), str(src)]) == 0
    card = proj / "components" / "skills"
    assert (card / "holo-card-maker" / "SKILL.md").is_file()
    assert (card / "holo-card-maker" / "notes.txt").read_text() == "extra file\n"
    assert json.loads((card / "component.json").read_text())["kind"] == "skills"
    out = capsys.readouterr().out
    assert f"added holo-card-maker to {card}" in out
    assert "holo-card-maker — makes holo cards" in out


def test_second_add_needs_replace(proj, tmp_path, capsys):
    src = _skill(tmp_path / "src", "design", body="one\n")
    assert main(["component", "skills", "add", str(proj), str(src)]) == 0
    (src / "SKILL.md").write_text("---\nname: design\ndescription: d\n---\ntwo\n")
    capsys.readouterr()
    assert main(["component", "skills", "add", str(proj), str(src)]) == 1
    assert "--replace" in capsys.readouterr().err
    held = proj / "components" / "skills" / "design" / "SKILL.md"
    assert held.read_text().endswith("one\n")
    assert main(["component", "skills", "add", str(proj), str(src), "--replace"]) == 0
    assert "replaced design" in capsys.readouterr().out
    assert held.read_text().endswith("two\n")


def test_remove_deletes_and_unknown_fails(proj, tmp_path, capsys):
    src = _skill(tmp_path / "src", "gone")
    assert main(["component", "skills", "add", str(proj), str(src)]) == 0
    assert main(["component", "skills", "remove", str(proj), "gone"]) == 0
    assert "removed gone" in capsys.readouterr().out
    assert not (proj / "components" / "skills" / "gone").exists()
    assert main(["component", "skills", "remove", str(proj), "gone"]) == 1
    assert "no skill gone" in capsys.readouterr().err


def test_a_broken_sibling_is_reported(proj, tmp_path, capsys):
    card = proj / "components" / "skills"
    (card / "half").mkdir(parents=True)
    (card / "half" / "SKILL.md").write_text("no front matter here\n")
    src = _skill(tmp_path / "src", "good")
    assert main(["component", "skills", "add", str(proj), str(src)]) == 0
    out = capsys.readouterr().out
    assert "good — does a thing" in out
    assert f"broken: {card / 'half'}: SKILL.md has no front matter" in out


def test_project_by_name_and_unknown(proj, tmp_path, capsys):
    projectchats.create(db.connect(), "game", proj)
    src = _skill(tmp_path / "src", "named")
    assert main(["component", "skills", "add", "game", str(src)]) == 0
    assert (proj / "components" / "skills" / "named" / "SKILL.md").is_file()
    assert main(["component", "skills", "add", "nosuch", str(src)]) == 1
    assert "no project or folder nosuch" in capsys.readouterr().err


def test_a_folder_without_skill_md_fails(proj, tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert main(["component", "skills", "add", str(proj), str(tmp_path / "empty")]) == 1
    assert "no SKILL.md" in capsys.readouterr().err
