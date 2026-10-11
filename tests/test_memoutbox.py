"""Memory, eki → client: the owner's durable facts become notes in the outbox."""
from eki import db, memoutbox, observe, store, syncstate


def _start(conn):
    """A first pass sets the cursor; runs made after it are looked at."""
    assert memoutbox.tick(conn) == []
    base = syncstate.baseline("memory-out")
    syncstate.set_baseline("memory-out", {"after": base["after"] - 1, "seen": []})


def _say(conn, text, cwd=None):
    with db.tx(conn):
        tid = store.create_thread(conn, text, cwd)
        rid = store.create_run(conn, tid, text)
    return tid, rid


def _notes():
    return sorted((syncstate.outbox() / "memory").glob("*.md"))


def _front(path):
    head = path.read_text().split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.strip().splitlines())


def test_remember_writes_one_note(conn):
    _start(conn)
    _, rid = _say(conn, "Remember that the gallery uses dark backgrounds.")
    said = memoutbox.tick(conn)
    assert len(said) == 1
    [note] = _notes()
    front = _front(note)
    assert front["signal"] == "remember" and front["project"] == "none" and front["run"] == rid
    assert front["description"].startswith('"Remember that the gallery')
    assert note.name.endswith("-remember-that-the-gallery-uses-dark-back.md")
    assert "the gallery uses dark backgrounds." in note.read_text()
    assert syncstate.status()["memory-out"]["count"] == 1


def test_decision_names_the_project(conn, tmp_path):
    folder = tmp_path / "holo-cards"
    (folder / "web").mkdir(parents=True)
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at) VALUES ('p1','holo-cards',?,'main',0)",
                     (str(folder),))
    _start(conn)
    _say(conn, "We decided to ship six rarity tiers.", cwd=str(folder / "web"))
    memoutbox.tick(conn)
    [note] = _notes()
    assert _front(note)["project"] == "holo-cards" and _front(note)["signal"] == "decision"


def test_recorded_correction(conn):
    _start(conn)
    tid, rid = _say(conn, "the blue one, please")
    observe.record(conn, "correction", run_id=rid, thread_id=tid, data={"prompt": "the blue one"})
    memoutbox.tick(conn)
    [note] = _notes()
    assert _front(note)["signal"] == "correction"


def test_goal_and_item_threads_are_skipped(conn):
    _start(conn)
    gt, _ = _say(conn, "Remember to split modules at 400 lines.")
    it, _ = _say(conn, "From now on always run the drill.")
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, thread_id, created_at) VALUES ('g1','g',?,0)", (gt,))
        conn.execute("INSERT INTO items(id, goal_id, title, thread_id, created_at, updated_at)"
                     " VALUES ('i1','g1','t',?,0,0)", (it,))
    assert memoutbox.tick(conn) == []
    assert _notes() == []


def test_plain_messages_are_skipped(conn):
    _start(conn)
    _say(conn, "make me a card with a fox")
    _say(conn, "what time is it in Tokyo")
    assert memoutbox.tick(conn) == []
    assert _notes() == []


def test_second_pass_writes_nothing_new(conn):
    _start(conn)
    _say(conn, "Don't forget: invoices go out on Fridays.")
    assert len(memoutbox.tick(conn)) == 1
    assert memoutbox.tick(conn) == []
    assert len(_notes()) == 1


def test_same_slug_gets_a_number(conn):
    _start(conn)
    _say(conn, "Remember the fox.")
    _say(conn, "Remember the fox.")
    memoutbox.tick(conn)
    names = [p.name for p in _notes()]
    assert len(names) == 2 and any(n.endswith("remember-the-fox-2.md") for n in names)


def test_first_pass_does_not_backfill(conn):
    _say(conn, "Remember that old thing.")
    assert memoutbox.tick(conn) == []
    assert memoutbox.tick(conn) == []
    assert _notes() == []
    assert syncstate.status()["memory-out"]["count"] == 0


def test_housekeeping_runs_it(conn):
    """syncstate.tick finds eki/memoutbox.py through its MODULES list; no call of its own is needed."""
    assert "memoutbox" in syncstate.MODULES
    syncstate.tick(conn)                                 # the first pass sets the cursor
    assert "after" in syncstate.baseline("memory-out")
    _start(conn)
    _say(conn, "Remember the fox lives in the gallery.")
    said = syncstate.tick(conn)
    assert any(line.startswith("memory-out: ") for line in said)
    assert len(_notes()) == 1
