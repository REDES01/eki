"""The digest's prose and triage from the local model (eki/digestprose.py)."""
import json

from eki import chores, db, digest, digestprose, housekeep, observe, store

ANSWER = """<think>let me see</think>PROSE:
A quiet day. One build landed and
nothing broke.

TRIAGE:
- the flaky codex start — watch — twice this week
- a KeyError in score.py — fix — eki self "handle a missing scope in score.compute"
"""


def use_local(home, name="bare"):
    cfg = json.loads((home / "routing.json").read_text())
    cfg["self"] = {"local": name}
    (home / "routing.json").write_text(json.dumps(cfg))


def page(conn):
    now = db.now()
    return digest.write(conn, now), now - digest.DAY, now


def finish(conn, rid, state="done", text=ANSWER):
    with db.tx(conn):
        if text:
            store.add_event(conn, rid, 1, "text", {"text": text})
        store.update_run(conn, rid, state=state, error=None if state == "done" else "it broke")


def test_a_done_chore_adds_in_short_and_triage_once(home, conn):
    use_local(home)
    path, since, until = page(conn)
    rid = digestprose.start(conn, path, since, until)
    assert rid and store.run(conn, rid)["row"] == "chore"
    assert digestprose.start(conn, path, since, until) is None          # one a page
    assert digestprose.tick(conn) == []                                  # still running
    finish(conn, rid)
    said = digestprose.tick(conn)
    assert said and "added" in said[0]
    text = path.read_text()
    lines = text.splitlines()
    assert lines[0].startswith("# eki digest") and lines[2] == "## In short"
    assert "A quiet day. One build landed and nothing broke." in text and "<think>" not in text
    tri = lines.index("## Triage")
    assert lines[tri + 2].startswith("- the flaky codex start — watch")
    assert tri < lines.index("## Score") and lines.index("## Score") - tri == 5
    assert text.count("## In short") == 1 and text.count("## Triage") == 1
    assert chores.latest(conn, "digest", str(path))["state"] == "done"
    assert digestprose.tick(conn) == [] and path.read_text() == text
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0   # triage only suggests


def test_a_page_that_already_has_it_is_left_alone(home, conn):
    use_local(home)
    path, since, until = page(conn)
    rid = digestprose.start(conn, path, since, until)
    finish(conn, rid)
    woven = digestprose.weave(path.read_text(), "Said before.", ["- x — ignore — y"])
    path.write_text(woven)
    digestprose.tick(conn)
    assert path.read_text() == woven
    assert chores.latest(conn, "digest", str(path))["state"] == "done"


def test_a_failed_or_unreadable_chore_leaves_the_page_byte_identical(home, conn):
    use_local(home)
    path, since, until = page(conn)
    before = path.read_bytes()
    rid = digestprose.start(conn, path, since, until)
    finish(conn, rid, state="failed")
    assert "failed" in digestprose.tick(conn)[0]
    assert path.read_bytes() == before
    assert chores.latest(conn, "digest", str(path))["state"] == "failed"

    other = path.with_name("2000-01-01.md")
    other.write_bytes(before)
    rid = digestprose.start(conn, other, since, until)
    finish(conn, rid, text="PROSE: only prose, no triage")
    digestprose.tick(conn)
    assert other.read_bytes() == before
    assert chores.latest(conn, "digest", str(other))["state"] == "failed"


def test_no_local_model_means_no_chore_and_an_unchanged_page(home, conn, monkeypatch):
    monkeypatch.setattr(digest, "due", lambda now=None: True)
    said = housekeep._digest(conn)
    path = digest.latest()
    before = path.read_bytes()
    assert said == [f"digest written: {path}"]
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert chores.latest(conn, "digest", str(path))["state"] == "skipped"
    assert digestprose.tick(conn) == [] and path.read_bytes() == before


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


def test_the_prompt_is_capped_and_scrubbed(home, conn):
    use_local(home)
    path, since, until = page(conn)
    observe.fault(conn, "somewhere")
    conn.execute("INSERT INTO journal(t, kind, data) VALUES (?,?,?)",
                 (until - 10, "handoff", json.dumps({"reason": "api_key=hunter2hunter2 " + "x" * 30000})))
    conn.execute("INSERT INTO journal(t, kind, data) VALUES (?,?,?)",
                 (until - 10, "run_end", json.dumps({"note": "not asked for"})))
    rid = digestprose.start(conn, path, since, until + 1)
    prompt = store.run(conn, rid)["prompt"]
    assert "hunter2" not in prompt and "[secret]" in prompt
    assert "not asked for" not in prompt and "handoff" in prompt
    body = prompt.split("\n---\n\n", 1)[1]
    assert len(body) < digestprose.CAP + 100 and "characters of the journal cut" in body
    assert "PROSE:" in prompt and "TRIAGE:" in prompt and 'eki self "' in prompt
