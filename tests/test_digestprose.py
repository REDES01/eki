"""The digest's patch notes and triage from the local model (eki/digestprose.py)."""
import json
import re

from eki import chores, db, digest, digestprose, housekeep, observe, patchnotes, store

NOTES = """Pieces now merge on their own, and the Station reads at a glance.

Self-build
- Pieces merge and go live on their own.

Web UI
- Station: one summary line per section, details on click."""

ANSWER = f"""<think>let me see</think>NOTES:
{NOTES}

TRIAGE:
- the flaky codex start — watch — twice this week
* a KeyError in the score — fix — eki self "handle a missing scope in score.compute"
"""


def use_local(home, name="bare"):
    cfg = json.loads((home / "routing.json").read_text())
    cfg["self"] = {"local": name}
    (home / "routing.json").write_text(json.dumps(cfg))


def add_item(conn, iid, title, touched, t, state="live"):
    cols = {"id": iid, "goal_id": "g1", "title": title, "state": state,
            "created_at": t - 100, "updated_at": t, "touched": json.dumps(touched)}
    conn.execute(f"INSERT INTO items({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 list(cols.values()))


def page(conn):
    now = db.now()
    add_item(conn, "a1b2c3d4e5", "merge pieces on their own", ["eki/queue.py"], now - 60)
    add_item(conn, "f0e1d2c3b4", "the Station folds its sections", ["eki/web/station.js"], now - 50)
    return digest.write(conn, now), now - digest.DAY, now


def finish(conn, rid, state="done", text=ANSWER):
    with db.tx(conn):
        if text:
            store.add_event(conn, rid, 1, "text", {"text": text})
        store.update_run(conn, rid, state=state, error=None if state == "done" else "it broke")


def test_good_notes_replace_the_short_page_and_triage_goes_on_the_long_page_once(home, conn):
    use_local(home)
    path, since, until = page(conn)
    before = path.read_text().splitlines()
    assert patchnotes.areas_in(path.read_text()) == ["Self-build", "Web UI"]
    rid = digestprose.start(conn, path, since, until)
    assert rid and store.run(conn, rid)["row"] == "chore"
    assert digestprose.start(conn, path, since, until) is None          # one a page
    assert digestprose.tick(conn) == []                                  # still running
    finish(conn, rid)
    said = digestprose.tick(conn)
    assert said and "written" in said[0]
    short = path.read_text()
    lines = short.splitlines()
    assert lines[0] == before[0] and lines[-1] == before[-1]            # title and closing kept
    assert short == patchnotes.with_notes("\n".join(before), NOTES)
    assert "\n\nWeb UI\n" in short
    assert "<think>" not in short and "## Triage" not in short
    long = digest.long_of(path)
    text = long.read_text()
    tri = text.splitlines().index("## Triage")
    assert text.splitlines()[tri + 2] == "- the flaky codex start — watch — twice this week"
    assert text.splitlines()[tri + 3].startswith("- a KeyError in the score — fix")
    assert tri < text.splitlines().index("## Score") and text.count("## Triage") == 1
    assert chores.latest(conn, "digest", str(path))["state"] == "done"
    assert digestprose.tick(conn) == [] and long.read_text() == text and path.read_text() == short
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2   # triage only suggests


def test_triage_goes_on_once_across_two_chores(home, conn):
    use_local(home)
    path, since, until = page(conn)
    for _ in range(2):
        rid = digestprose.start(conn, path, since, until) or \
            chores.start(conn, "digest", str(path), "again", "background")
        finish(conn, rid)
        digestprose.tick(conn)
        with db.tx(conn):
            conn.execute("UPDATE chores SET state='failed' WHERE subject=?", (str(path),))
    assert digest.long_of(path).read_text().count("## Triage") == 1


def bad(conn, home, notes):
    use_local(home)
    path, since, until = page(conn)
    before, long_before = path.read_bytes(), digest.long_of(path).read_bytes()
    rid = digestprose.start(conn, path, since, until)
    finish(conn, rid, text=f"NOTES:\n{notes}\nTRIAGE:\n- nothing — ignore — a quiet day\n")
    said = digestprose.tick(conn)
    assert path.read_bytes() == before
    got = chores.latest(conn, "digest", str(path))
    assert got["state"] == "failed"
    long = digest.long_of(path).read_bytes()
    assert long != long_before and long.count(b"## Triage") == 1      # triage lands anyway
    return said, got["result"]


def test_notes_with_an_id_are_rejected_and_the_rules_page_stands(home, conn):
    said, why = bad(conn, home, "A good day.\n\nSelf-build\n- Build a1b2c3d4e5 merged.")
    assert "id" in why and "a1b2c3d4e5" in why and "kept to the rules" in said[0]


def test_notes_with_a_long_line_are_rejected(home, conn):
    long_line = " ".join(f"word{i}" for i in range(15))
    _, why = bad(conn, home, f"A good day.\n\nWeb UI\n- {long_line}")
    assert "14 words" in why


def test_notes_with_an_area_not_on_the_page_are_rejected(home, conn):
    _, why = bad(conn, home, "A good day.\n\nPictures\n- New pictures.")
    assert "Pictures" in why


def test_a_failed_or_unreadable_chore_leaves_both_pages_byte_identical(home, conn):
    use_local(home)
    path, since, until = page(conn)
    long = digest.long_of(path)
    before, long_before = path.read_bytes(), long.read_bytes()
    rid = digestprose.start(conn, path, since, until)
    finish(conn, rid, state="failed")
    assert "failed" in digestprose.tick(conn)[0]
    assert path.read_bytes() == before and long.read_bytes() == long_before
    assert chores.latest(conn, "digest", str(path))["state"] == "failed"

    other = path.with_name("2000-01-01.md")
    other.write_bytes(before)
    digest.long_of(other).write_bytes(long_before)
    rid = digestprose.start(conn, other, since, until)
    finish(conn, rid, text="NOTES: only notes, no triage")
    digestprose.tick(conn)
    assert other.read_bytes() == before and digest.long_of(other).read_bytes() == long_before
    got = chores.latest(conn, "digest", str(other))
    assert got["state"] == "failed" and got["result"] == "no NOTES: and TRIAGE: blocks"


def test_parse_reads_both_blocks_or_nothing():
    notes, triage = digestprose.parse(ANSWER)
    assert notes == NOTES.replace("\n\n", "\n")
    assert triage[1].startswith("- a KeyError") and len(triage) == 2
    assert digestprose.parse("notes: headline here\n- x\ntriage: - y — ignore — z\n- y — ignore — z") \
        == ("headline here\n- x", ["- y — ignore — z"] * 2)
    assert digestprose.parse("NOTES:\n\nTRIAGE:\n- a — b — c") is None
    assert digestprose.parse("NOTES: a\nTRIAGE:\nnothing listed") is None
    assert digestprose.parse("") is None


def test_no_local_model_means_no_chore_and_unchanged_pages(home, conn, monkeypatch):
    monkeypatch.setattr(digest, "due", lambda now=None: True)
    said = housekeep._digest(conn)
    path = digest.latest()
    before, long_before = path.read_bytes(), digest.long_of(path).read_bytes()
    assert said == [f"digest written: {path}"]
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert chores.latest(conn, "digest", str(path))["state"] == "skipped"
    assert digestprose.tick(conn) == []
    assert path.read_bytes() == before and digest.long_of(path).read_bytes() == long_before


def test_housekeeping_starts_it_with_the_digest_window(home, conn, monkeypatch):
    use_local(home)
    monkeypatch.setattr(digest, "due", lambda now=None: True)
    (digest.folder() / ".last").write_text(repr(db.now() - 3600))
    seen = {}
    real = digestprose.start
    monkeypatch.setattr(digestprose, "start",
                        lambda c, p, s, u: seen.update(since=s, until=u) or real(c, p, s, u))
    said = housekeep._digest(conn)
    assert len(said) == 2 and "run" in said[1]
    assert abs(seen["since"] - (seen["until"] - 3600)) < 5
    assert seen["until"] == digest.last()
    assert ("digestprose", digestprose.tick) in housekeep.STEPS
    assert [n for n, _ in housekeep.STEPS].index("digestprose") == \
        [n for n, _ in housekeep.STEPS].index("digest") + 1


def test_the_prompt_holds_the_rules_no_ids_and_is_capped_and_scrubbed(home, conn):
    use_local(home)
    path, since, until = page(conn)
    observe.fault(conn, "somewhere")
    conn.execute("INSERT INTO journal(t, kind, data) VALUES (?,?,?)",
                 (until - 10, "handoff", json.dumps({"reason": "api_key=hunter2hunter2 " + "x" * 30000})))
    conn.execute("INSERT INTO journal(t, kind, data) VALUES (?,?,?)",
                 (until - 10, "run_end", json.dumps({"note": "not asked for"})))
    rid = digestprose.start(conn, path, since, until + 1)
    prompt = store.run(conn, rid)["prompt"]
    brief, body = prompt.split("\n---\n\n", 1)
    assert patchnotes.EXAMPLE in brief and patchnotes.RULES_TEXT in brief
    assert "[Self-build] (change) merge pieces on their own" in brief
    assert "[Web UI] (change) the Station folds its sections" in brief
    assert "NOTES:" in brief and "TRIAGE:" in brief and 'eki self "' in brief
    assert "a1b2c3d4e5" not in prompt and "f0e1d2c3b4" not in prompt
    assert not re.search(r"\b[0-9a-f]{10}\b", brief)
    assert "# eki digest" not in prompt                                  # the old page is gone
    assert "hunter2" not in prompt and "[secret]" in prompt
    assert "not asked for" not in prompt and "handoff" in prompt
    assert len(body) < digestprose.CAP + 100 and "characters of the journal cut" in body
