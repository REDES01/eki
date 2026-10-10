"""The instructions kind: the project's AGENTS.md as a card, its starter files and CLAUDE.md link."""
import json

import pytest

from eki import components
from eki.components import Call, cards, instructions


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "game"
    r.mkdir()
    return r


def test_validate_rejects_escapes(root):
    assert instructions.validate({"file": "AGENTS.md"}, root) == []
    for bad in ("../x", "/abs", "", None):
        errs = instructions.validate({"file": bad}, root)
        assert errs and errs[0].startswith("file")


def test_summary_heading_and_lines(root):
    (root / "AGENTS.md").write_text("intro\n## Working on game\nmore\n# not this\n")
    s = instructions.summary(root)
    assert set(s) == {"file", "exists", "heading", "lines", "text", "read_first", "claude"}
    assert (s["exists"], s["heading"], s["lines"], s["claude"]) == (True, "Working on game", 4, "none")
    missing = instructions.summary(root, "OTHER.md")
    assert (missing["exists"], missing["lines"], missing["text"], missing["heading"]) == (False, 0, "", "")


def test_save_writes_the_file(root):
    instructions.save(root, "AGENTS.md", "# new\n")
    assert (root / "AGENTS.md").read_text() == "# new\n"
    instructions.save(root, "AGENTS.md", "# newer\n")
    assert (root / "AGENTS.md").read_text() == "# newer\n"
    assert [p.name for p in root.iterdir()] == ["AGENTS.md"]      # no temp file left
    with pytest.raises(ValueError):
        instructions.save(root, "AGENTS.md", None)
    with pytest.raises(ValueError):
        instructions.save(root, "../AGENTS.md", "x")


def test_ensure_files_writes_starter(root):
    said = instructions.ensure_files(root)
    text = (root / "AGENTS.md").read_text()
    assert text.startswith("# game\n")
    assert "## Read first" in text and "## How to check your work" in text
    assert (root / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert said == ["wrote AGENTS.md (a starter)", "wrote CLAUDE.md (@AGENTS.md)"]
    assert instructions.claude_state(root) == "linked"


def test_ensure_files_never_overwrites(root):
    (root / "AGENTS.md").write_bytes(b"# mine\nkeep\n")
    (root / "CLAUDE.md").write_bytes(b"# claude\nno link")
    said = instructions.ensure_files(root)
    assert (root / "AGENTS.md").read_bytes() == b"# mine\nkeep\n"
    assert (root / "CLAUDE.md").read_bytes() == b"# claude\nno link"
    assert said == ["AGENTS.md exists: the card points at it",
                    "CLAUDE.md exists without @AGENTS.md: Link CLAUDE.md on the card adds it"]


def test_ensure_files_keeps_existing_claude_only(root):
    (root / "CLAUDE.md").write_bytes(b"mine\n")
    instructions.ensure_files(root)
    assert (root / "CLAUDE.md").read_bytes() == b"mine\n"
    assert (root / "AGENTS.md").is_file()


def test_claude_state_and_link(root):
    assert instructions.claude_state(root) == "none"
    (root / "CLAUDE.md").write_text("# rules\nbe kind")
    assert instructions.claude_state(root) == "unlinked"
    assert instructions.link_claude(root) is True
    assert (root / "CLAUDE.md").read_text() == "# rules\nbe kind\n@AGENTS.md\n"
    assert instructions.claude_state(root) == "linked"
    before = (root / "CLAUDE.md").read_bytes()
    assert instructions.link_claude(root) is False
    assert (root / "CLAUDE.md").read_bytes() == before
    (root / "CLAUDE.md").write_text("  @AGENTS.md  \n")
    assert instructions.claude_state(root) == "linked"


def test_link_claude_creates_when_missing(root):
    assert instructions.link_claude(root) is True
    assert (root / "CLAUDE.md").read_text() == "@AGENTS.md\n"


def test_read_first_only_inside_the_project(root, tmp_path):
    (root / "design").mkdir()
    (root / "design" / "DECISIONS.md").write_text("x")
    (root / "notes.md").write_text("x")
    (tmp_path / "outside.md").write_text("secret")
    (root / "sneaky.md").symlink_to(tmp_path / "outside.md")
    (root / "CLAUDE.md").write_text("@AGENTS.md\n")
    text = ("Read `design/DECISIONS.md` and `../outside.md`, `/etc/hosts`, `missing.md`, "
            "`sneaky.md`, `notes.md`, `design/DECISIONS.md` again, `CLAUDE.md`, `AGENTS.md`, `design`.")
    (root / "AGENTS.md").write_text(text)
    assert instructions.read_first(root, text) == ["design/DECISIONS.md", "notes.md"]
    assert instructions.summary(root)["read_first"] == ["design/DECISIONS.md", "notes.md"]


def test_safe_path(root, tmp_path):
    assert instructions.safe_path(root, "a/b.md") == root.resolve() / "a" / "b.md"
    (root / "out").symlink_to(tmp_path)
    for bad in ("/etc/hosts", "../x", "a/../../x", "", "out/x"):
        with pytest.raises(ValueError):
            instructions.safe_path(root, bad)


def test_add_template_writes_starter_and_loads(root):
    out, said = cards.add_template(root / "components", "instructions")
    assert out == root / "components" / "instructions"
    assert json.loads((out / "component.json").read_text())["file"] == "AGENTS.md"
    assert "wrote AGENTS.md (a starter)" in said and "wrote CLAUDE.md (@AGENTS.md)" in said
    found, errors = components.own(root)
    assert errors == []
    comp = found["instructions"]
    assert comp.kind == "instructions" and instructions.root_of(comp) == root
    assert components.entries(found, errors)[0]["notes"] == ["game: 9 lines"]
    r = instructions.call(comp, Call(args=[], caller_dir="", caller_run=None, run_id="r",
                                     emit=lambda *_: None, extra={}))
    assert r.text == (root / "AGENTS.md").read_text() and not r.error


def test_add_template_keeps_existing_and_notes(root):
    (root / "AGENTS.md").write_text("# Mine\n")
    (root / "CLAUDE.md").write_text("old\n")
    out, said = cards.add_template(root / "components", "instructions")
    assert (root / "AGENTS.md").read_text() == "# Mine\n"
    assert (root / "CLAUDE.md").read_text() == "old\n"
    comp = components.own(root)[0]["instructions"]
    assert instructions.notes(comp) == ["Mine: 1 line", "CLAUDE.md doesn't include @AGENTS.md"]
    (root / "AGENTS.md").unlink()
    assert instructions.notes(comp) == ["AGENTS.md is missing"]
    r = instructions.call(comp, Call(args=[], caller_dir="", caller_run=None, run_id="r",
                                     emit=lambda *_: None, extra={}))
    assert r.error == f"no AGENTS.md in {root}"


def test_pool_card_writes_no_files():
    out, said = cards.add_template(components.pool_dir(), "instructions")
    assert not (components.pool_dir().parent / "AGENTS.md").exists()
    assert said == ["a pool instructions card writes no files: attach it to a project"]
