"""ROADMAP.md read as open entries (docs/self-build.md, "The loop")."""
import time
from pathlib import Path

from eki import integration, roadmap
from eki.workspace import git

REAL = Path(__file__).resolve().parent.parent / "ROADMAP.md"

SAMPLE = """# Roadmap

## First

- [x] **Done thing.** Already landed.
- [ ] **Review run.** Between gate 1 and
      the queue, once the loop runs.
- [ ] Plain entry with no bold
- [ ] **Hands.** Needs the locked files. *(For a person)*

## Second

- [ ] **Idle shift.** Background work.

## Not planned

- [ ] **Never.** Not to be taken.
"""


def test_real_roadmap_parses():
    text = REAL.read_text()
    entries = roadmap.open_entries(text)
    assert entries
    open_lines = sum(1 for l in text.splitlines() if l.startswith("- [ ]"))
    assert 0 < len(entries) <= open_lines
    for e in entries:
        assert "for a person" not in e.text.lower()
        assert not e.text.startswith("[x]")
        assert e.section and e.section.lower() != "not planned"
    done = [l for l in text.splitlines() if l.startswith("- [x]")]
    assert all(l[6:].strip() not in [e.text for e in entries] for l in done)


def test_synthetic_sections_titles_continuations():
    entries = roadmap.open_entries(SAMPLE)
    assert [e.title for e in entries] == ["Review run.", "Plain entry with no bold", "Idle shift."]
    review, plain, idle = entries
    assert review.section == "First" and idle.section == "Second"
    assert review.text == "**Review run.** Between gate 1 and\nthe queue, once the loop runs."
    assert not review.text.startswith("- [")
    assert all("Never" not in e.text for e in entries)
    assert all("Hands" not in e.text for e in entries)


def test_title_trimmed():
    [e] = roadmap.open_entries("## S\n\n- [ ] " + "word " * 40 + "\n")
    assert len(e.title) == 100


def test_key_stable_and_changes_with_text():
    a = roadmap.open_entries(SAMPLE)[0].key
    assert a == roadmap.open_entries(SAMPLE)[0].key
    assert len(a) == 12
    edited = SAMPLE.replace("Between gate 1", "Between gate 2")
    assert roadmap.open_entries(edited)[0].key != a
    # formatting alone is not an edit
    reflowed = SAMPLE.replace("**Review run.** Between gate 1 and\n      the queue",
                              "Review run. `Between`  gate 1 and the\n      queue")
    assert roadmap.open_entries(reflowed)[0].key == a


def test_eligible_drops_picked_whatever_the_state(conn):
    entries = roadmap.open_entries(SAMPLE)
    for i, (e, state) in enumerate(zip(entries, ["left", "failed"])):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, pick_key) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (f"g{i}", e.text, "roadmap", "eki", state, time.time(), f"roadmap:{e.key}"))
    conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, pick_key) "
                 "VALUES ('gf','x','fault','eki','planned',?,?)", (time.time(), f"fault:{entries[2].key}"))
    conn.commit()
    assert roadmap.picked_keys(conn) == {entries[0].key, entries[1].key}
    assert roadmap.eligible(conn, entries) == [entries[2]]


def test_read_main(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README").write_text("x\n")
    git(r, "add", "README")
    git(r, "commit", "-q", "-m", "first")
    monkeypatch.setattr(integration, "repo", lambda: r)
    assert roadmap.read_main() == ""
    (r / "ROADMAP.md").write_text(SAMPLE)
    git(r, "add", "ROADMAP.md")
    git(r, "commit", "-q", "-m", "roadmap")
    assert len(roadmap.open_entries(roadmap.read_main())) == 3
