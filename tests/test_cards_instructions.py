"""The instructions card over the API: summary, suggest, save, link CLAUDE.md, read a Read-first file."""
import os

import pytest

from eki import apiroutes


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder or f"/nowhere/{pid}"), "trunk", 0, str(folder) if folder else None))
    conn.commit()


def call(conn, method, path, body=None, q=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, q or {}, body or {})


def card(conn, name, pid="p1"):
    return next(c for c in call(conn, "GET", f"/api/projects/{pid}/cards")["cards"] if c["name"] == name)


def with_card(conn, tmp_path, agents=None, claude=None):
    root = tmp_path / "game"
    root.mkdir()
    if agents is not None:
        (root / "AGENTS.md").write_text(agents)
    if claude is not None:
        (root / "CLAUDE.md").write_text(claude)
    project(conn, root)
    call(conn, "POST", "/api/projects/p1/cards", {"template": "instructions"})
    return root


def test_listing_has_heading_and_line_count_and_null_for_others(conn, tmp_path):
    root = with_card(conn, tmp_path, agents="intro\n# Game rules\n\nsee `docs/a.md`\n", claude="@AGENTS.md\n")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("a")
    call(conn, "POST", "/api/projects/p1/cards", {"template": "agent"})
    s = card(conn, "instructions")["instructions"]
    assert set(s) == {"file", "exists", "heading", "lines", "text", "read_first", "claude"}
    assert (s["file"], s["exists"], s["heading"], s["lines"]) == ("AGENTS.md", True, "Game rules", 4)
    assert s["read_first"] == ["docs/a.md"] and s["claude"] == "linked"
    assert card(conn, "agent")["instructions"] is None


def test_save_writes_the_file(conn, tmp_path):
    root = with_card(conn, tmp_path, agents="# Old\n")
    got = call(conn, "POST", "/api/projects/p1/cards/instructions/instructions", {"text": "# New\none\n"})
    assert (root / "AGENTS.md").read_text() == "# New\none\n"
    assert got["card"]["instructions"]["heading"] == "New" and got["card"]["instructions"]["lines"] == 2
    with pytest.raises(ValueError):
        call(conn, "POST", "/api/projects/p1/cards/instructions/instructions", {"text": 3})


def test_adding_writes_the_starter_files(conn, tmp_path):
    root = with_card(conn, tmp_path)
    text = (root / "AGENTS.md").read_text()
    assert text.startswith("# game\n") and "## Read first" in text and "## How to check your work" in text
    assert (root / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert card(conn, "instructions")["instructions"]["claude"] == "linked"


def test_an_existing_agents_md_is_never_overwritten(conn, tmp_path):
    root = with_card(conn, tmp_path, agents="# Mine\r\nkeep me")
    assert (root / "AGENTS.md").read_bytes() == b"# Mine\r\nkeep me"


def test_link_claude_fixes_an_unlinked_claude_md(conn, tmp_path):
    root = with_card(conn, tmp_path, agents="# A\n", claude="be nice")
    assert card(conn, "instructions")["instructions"]["claude"] == "unlinked"
    got = call(conn, "POST", "/api/projects/p1/cards/instructions/link-claude")
    assert got["changed"] is True and got["card"]["instructions"]["claude"] == "linked"
    assert (root / "CLAUDE.md").read_text() == "be nice\n@AGENTS.md\n"
    assert call(conn, "POST", "/api/projects/p1/cards/instructions/link-claude")["changed"] is False


def test_read_serves_inside_the_folder_only(conn, tmp_path):
    root = with_card(conn, tmp_path, agents="# A\n")
    (root / "design").mkdir()
    (root / "design" / "DECISIONS.md").write_text("decided")
    (tmp_path / "secret").write_text("no")
    os.symlink(tmp_path / "secret", root / "link.md")
    url = "/api/projects/p1/cards/instructions/read"
    assert call(conn, "GET", url, q={"path": "design/DECISIONS.md"}) == {"path": "design/DECISIONS.md", "text": "decided"}
    for bad in ("../secret", "/etc/hosts", "link.md"):
        with pytest.raises(ValueError):
            call(conn, "GET", url, q={"path": bad})
    with pytest.raises(KeyError):
        call(conn, "GET", url, q={"path": "nope.md"})


def test_routes_refuse_other_cards_and_unknown_ones(conn, tmp_path):
    with_card(conn, tmp_path, agents="# A\n")
    call(conn, "POST", "/api/projects/p1/cards", {"template": "agent"})
    with pytest.raises(ValueError):
        call(conn, "POST", "/api/projects/p1/cards/agent/instructions", {"text": "x"})
    with pytest.raises(KeyError):
        call(conn, "POST", "/api/projects/p1/cards/ghost/link-claude")
    fn, _ = apiroutes.match("POST", "/api/projects/p1/cards/instructions")
    assert fn.__name__ == "edit"


def test_suggest(conn, tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    (root / "AGENTS.md").write_text("# A\n")
    project(conn, root)
    got = call(conn, "GET", "/api/projects/p1/cards")["suggest"]
    assert got == {"template": "instructions", "why": "This project has AGENTS.md: add it as a card to see and edit it"}
    call(conn, "POST", "/api/projects/p1/cards", {"template": "instructions"})
    assert call(conn, "GET", "/api/projects/p1/cards")["suggest"] is None
    assert (root / "AGENTS.md").read_text() == "# A\n"
    project(conn, None, pid="p2")
    assert call(conn, "GET", "/api/projects/p2/cards")["suggest"] is None


def test_suggest_with_only_claude_md_or_nothing(conn, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "CLAUDE.md").write_text("hi")
    project(conn, a, pid="pa")
    project(conn, b, pid="pb")
    assert "has CLAUDE.md" in call(conn, "GET", "/api/projects/pa/cards")["suggest"]["why"]
    assert call(conn, "GET", "/api/projects/pb/cards")["suggest"]["why"].startswith("No AGENTS.md yet")
