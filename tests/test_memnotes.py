"""Claude's memory, client -> eki: an index of the mirror in every run's brief, never the bodies."""
from eki import memnotes, store, syncstate, worker


def _write(rel, text):
    p = syncstate.memory_dir() / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _mirror():
    _write("profile.md", "---\nname: profile\ndescription: who the owner is\n---\nSECRET BODY profile\n")
    _write("preferences.md", "\n\n# Short answers, no fluff\nSECRET BODY prefs\n")
    _write("areas/games.md", "---\ndescription: \"game design work\"\n---\nSECRET BODY\n")
    _write("topics/eki.md", "---\nname: eki\n---\n\nthe station on the Mac\nSECRET BODY\n")
    _write("people/sam.md", "---\ndescription: a friend\n---\n")


def _note(conn, proj, row=None, provider="fake"):
    tid = store.create_thread(conn, "t", str(proj), by="person")
    turn = worker.build_turn(conn, store.run(conn, store.create_run(conn, tid, "hi", row=row)), provider)
    return turn.extra.get("note") or ""


def test_descriptions_from_front_matter_or_first_line(home):
    _mirror()
    got = {n["path"]: n["description"] for n in memnotes.index()}
    assert got == {"/areas/games.md": "game design work", "/people/sam.md": "a friend",
                   "/preferences.md": "Short answers, no fluff", "/profile.md": "who the owner is",
                   "/topics/eki.md": "the station on the Mac"}
    assert [n["path"] for n in memnotes.index()] == sorted(got)


def test_a_long_description_is_cut(home):
    _write("topics/long.md", "x" * 500)
    assert memnotes.index()[0]["description"] == "x" * 120


def test_a_missing_folder_is_no_notes(home):
    assert not syncstate.memory_dir().exists()
    assert memnotes.index() == [] and memnotes.brief() == ""
    assert memnotes.tick(None) == []
    assert syncstate.status()["memory-in"]["count"] == 0


def test_the_brief_is_capped_and_never_pastes_a_body(home):
    for i in range(300):
        _write(f"topics/t{i:03}.md", f"---\ndescription: topic number {i} " + "y" * 60 + "\n---\nSECRET BODY\n")
    out = memnotes.brief()
    assert len(out) <= memnotes.CAP
    assert "SECRET" not in out
    assert str(syncstate.memory_dir()) in out
    shown = [ln for ln in out.splitlines() if ln.startswith("/topics/")]
    assert out.splitlines()[-1] == f"… and {300 - len(shown)} more"
    memnotes.tick(None)
    assert syncstate.status()["memory-in"]["count"] == 300


def test_a_chat_run_carries_the_index_and_agents_md(conn, proj):
    _mirror()
    syncstate.agents_md().write_text("# how to work\n")
    note = _note(conn, proj)
    assert "/profile.md — who the owner is" in note
    assert "SECRET" not in note
    assert f"The owner's instructions for every run: read {syncstate.agents_md()}" in note


def test_no_agents_md_line_without_the_file(conn, proj):
    _mirror()
    note = _note(conn, proj)
    assert "/profile.md" in note and "instructions for every run" not in note


def test_claude_code_gets_the_index_but_not_the_agents_md_line(conn, proj, monkeypatch):
    _mirror()
    syncstate.agents_md().write_text("# how to work\n")
    real = worker.providers.config
    monkeypatch.setattr(worker.providers, "config", lambda: {**real(), "fake": {"kind": "claude"}})
    note = _note(conn, proj)
    assert "/profile.md" in note and "instructions for every run" not in note


def test_a_chore_gets_neither(conn, proj):
    _mirror()
    syncstate.agents_md().write_text("# how to work\n")
    note = _note(conn, proj, row="chore")
    assert "/profile.md" not in note and "instructions for every run" not in note


def test_a_broken_brief_never_fails_the_run(conn, proj, monkeypatch):
    def boom():
        raise RuntimeError("no")
    monkeypatch.setattr(memnotes, "brief", boom)
    _note(conn, proj)
