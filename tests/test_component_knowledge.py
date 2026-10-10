"""Knowledge cards: their files, and what every run in the project is told of them."""
import json

from eki import components, store, worker, workspace
from eki.cli import main
from eki.components import knowledge


def _card(proj, name, files, read="always", about="what it is for"):
    d = proj / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "knowledge", "about": about,
                                                  "files": files, "read": read}))
    return d


def _write(proj, rel, text="x\n"):
    p = proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _comp(proj, name):
    return components.own(proj)[0][name]


def test_always_and_when_relevant_with_about_lines(proj):
    _write(proj, "design/DECISIONS.md")
    _write(proj, "story/core.md")
    _write(proj, "story/voices/ana.md")
    _write(proj, "notes/a.md")
    _write(proj, "notes/b.md")
    _card(proj, "decisions", ["design/DECISIONS.md"], about="owner-approved decisions, newest first")
    _card(proj, "story", ["story/core.md", "story/voices"], read="when-relevant", about="writing dialogue")
    _card(proj, "notes", ["notes/*.md"], read="when-relevant", about="playtest notes")
    out = knowledge.brief(str(proj))
    assert out.splitlines() == [
        "Shared knowledge of this project (knowledge cards in its components/; paths are relative to this folder):",
        "- read first: design/DECISIONS.md — owner-approved decisions, newest first",
        "- look here when relevant: notes/a.md, notes/b.md — playtest notes",
        "- look here when relevant: story/core.md, story/voices/ — writing dialogue",
    ]


def test_missing_files_are_skipped_but_shown(proj):
    _write(proj, "design/DECISIONS.md")
    _card(proj, "decisions", ["design/DECISIONS.md", "design/GONE.md"])
    _card(proj, "gone", ["nothing/here.md"], about="never there")
    (proj / "components" / "broken").mkdir()
    (proj / "components" / "broken" / "component.json").write_text('{"kind": "knowledge"}')
    out = knowledge.brief(str(proj))
    assert "GONE" not in out and "never there" not in out
    assert "- read first: design/DECISIONS.md — what it is for" in out
    fs = knowledge.files(_comp(proj, "decisions"))
    assert [(f["path"], f["missing"]) for f in fs] == [("design/DECISIONS.md", False), ("design/GONE.md", True)]
    assert knowledge.notes(_comp(proj, "decisions")) == ["1 file, 1 missing: design/GONE.md"]


def test_no_cards_or_no_project_is_empty(proj, tmp_path):
    (proj / "components").mkdir()
    assert knowledge.brief(str(proj)) == ""
    (tmp_path / "loose").mkdir()
    assert knowledge.brief(str(tmp_path / "loose")) == ""
    assert knowledge.brief(None) == ""


def test_five_cards_all_appear_in_order(proj):
    names = ["e-arch", "d-voices", "c-story", "b-rules", "a-memory"]
    for i, n in enumerate(names):
        _write(proj, f"k/{n}.md")
        _card(proj, n, [f"k/{n}.md"], read="always" if i % 2 else "when-relevant", about=n)
    lines = knowledge.brief(str(proj)).splitlines()[1:]
    assert [ln.rsplit(" — ", 1)[1] for ln in lines] == ["b-rules", "d-voices", "a-memory", "c-story", "e-arch"]
    assert all(ln.startswith("- read first:") for ln in lines[:2])
    assert all(ln.startswith("- look here when relevant:") for ln in lines[2:])


def test_author_from_git_log(proj):
    _write(proj, "design/DECISIONS.md")
    _write(proj, "design/new.md")
    workspace.git(proj, "add", "design/DECISIONS.md")
    workspace.git(proj, "-c", "user.name=Ada Writer", "-c", "user.email=a@x", "commit", "-q", "-m", "d")
    _card(proj, "decisions", ["design"])
    got = {f["path"]: f["author"] for f in knowledge.files(_comp(proj, "decisions"))}
    assert got == {"design/DECISIONS.md": "Ada Writer", "design/new.md": ""}


def test_worktree_runs_are_named_their_own_copy(proj, tmp_path):
    _write(proj, "design/DECISIONS.md")
    _write(proj, "story/voices/ana.md")
    _write(proj, "notes/a.md")
    workspace.git(proj, "add", "design", "story", "notes")
    workspace.git(proj, "-c", "user.name=a", "-c", "user.email=a@x", "commit", "-q", "-m", "k")
    _write(proj, "notes/only-in-main.md")
    _card(proj, "decisions", ["design/DECISIONS.md"])
    _card(proj, "story", ["story/voices", "notes/*.md"], read="when-relevant")
    wt = tmp_path / "wt"
    workspace.git(proj, "worktree", "add", "-q", str(wt))
    out = knowledge.brief(str(wt))
    assert str(proj.resolve()) not in out and str(wt.resolve()) not in out
    named = [p.strip().rstrip("/") for ln in out.splitlines()[1:]
             for p in ln.split(": ", 1)[1].rsplit(" — ", 1)[0].split(",")]
    assert named == ["design/DECISIONS.md", "story/voices", "notes/a.md"]
    assert all((wt / p).exists() for p in named)


def test_build_turn_carries_the_block_and_a_chore_doesnt(conn, proj):
    _write(proj, "design/DECISIONS.md")
    _card(proj, "decisions", ["design/DECISIONS.md"], about="owner-approved decisions")
    tid = store.create_thread(conn, "t", str(proj))
    turn = worker.build_turn(conn, store.run(conn, store.create_run(conn, tid, "hi")), "fake")
    assert "Shared knowledge of this project" in turn.extra["note"]
    assert "read first:" in turn.extra["note"] and "owner-approved decisions" in turn.extra["note"]
    chore = worker.build_turn(conn, store.run(conn, store.create_run(conn, tid, "hi", row="chore")), "fake")
    assert "Shared knowledge" not in (chore.extra.get("note") or "")


def test_cli_prints_the_block(proj, capsys):
    (proj / "components").mkdir()
    assert main(["component", "knowledge", str(proj)]) == 0
    assert capsys.readouterr().out.strip() == "no knowledge cards here"
    _write(proj, "design/DECISIONS.md")
    _card(proj, "decisions", ["design/DECISIONS.md"])
    assert main(["component", "knowledge", str(proj)]) == 0
    assert capsys.readouterr().out.strip() == knowledge.brief(str(proj))


def test_cli_add_knowledge_and_mcp(proj, capsys):
    assert main(["component", "add", "knowledge", "--name", "rules", "-C", str(proj)]) == 0
    assert main(["component", "add", "mcp", "--name", "tools", "-C", str(proj)]) == 0
    found, errors = components.own(proj)
    assert found["rules"].kind == "knowledge" and found["tools"].kind == "mcp"
    assert "1 missing: design/DECISIONS.md" in capsys.readouterr().out
