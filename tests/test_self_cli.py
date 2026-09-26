"""`eki self`: the board shows the queue, and apply / release / autonomy do what they say."""
import json

import pytest

from eki import asks, builds, db, paths, selfwork, store, train
from eki.cli import builds as builds_cmd, main
from eki.cli.selfboard import board
import eki.cli.self as self_cmd


@pytest.fixture(autouse=True)
def no_engine(monkeypatch):
    monkeypatch.setattr(self_cmd, "ensure_engine", lambda quiet=False: None)


def goal_with(conn, **fields):
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     (gid, "a goal", "ask", "you", "planned", db.now()))
        iid = selfwork.new_item(conn, gid, "the item", "do it", ["eki/a.py"], [], False)
        selfwork._set(conn, iid, **fields)
    return iid


def test_the_board_lists_a_queued_item_with_its_stage_and_head(conn, capsys):
    iid = goal_with(conn, state="queued", queued_at=db.now(), head="a" * 40, rebased="b" * 40,
                    gate2="green", gate2_on="b" * 40)
    other = goal_with(conn, state="queued", queued_at=db.now() + 1)
    assert main(["self"]) == 0
    out = capsys.readouterr().out
    assert "\nqueue\n" in out
    first = next(l for l in out.splitlines() if l.strip().startswith("1."))
    assert iid in first and "aaaaaaaa" in first and "gate 2 green — waiting for the ones ahead" in first
    second = next(l for l in out.splitlines() if l.strip().startswith("2."))
    assert other in second and "rebasing" in second


def test_the_board_shows_a_running_gate_2_and_a_resolving_item(conn, capsys):
    tid = store.create_thread(conn, "gate 2", "/tmp")
    rid = store.create_run(conn, tid, '["true"]', provider="command")
    iid = goal_with(conn, state="queued", queued_at=db.now(), head="c" * 40, rebased="d" * 40,
                    gate2_run=rid, gate2_on="d" * 40)
    goal_with(conn, state="resolving", run_id="r1", head="c" * 40)
    main(["self"])
    out = capsys.readouterr().out
    assert f"gate 2 (run {rid})" in out and iid in out
    assert "resolving (run r1)" in out


def test_a_locked_item_shows_its_files_and_the_yes_hint(conn, capsys):
    iid = goal_with(conn, state="locked", locked=json.dumps(["bin/check"]))
    main(["self"])
    out = capsys.readouterr().out
    assert "bin/check" in out and f"eki self apply {iid} --yes" in out


def test_landed_live_and_rolled_back_items_say_so(conn, capsys):
    goal_with(conn, state="landed", rebased="e" * 40)
    goal_with(conn, state="live", build="b-123")
    goal_with(conn, state="rolled back", error="rolled back from build b-9: exit 1 after 3s")
    main(["self"])
    out = capsys.readouterr().out
    assert "eeeeeeeeeeee" in out and "live in build b-123" in out and "rolled back from build b-9" in out


def test_apply_queues_a_proposed_item(conn, capsys):
    iid = goal_with(conn, state="proposed", touched=json.dumps(["eki/a.py"]))
    main(["self"])
    assert f"`eki self apply {iid}`" in capsys.readouterr().out
    assert main(["self", "apply", iid]) == 0
    assert f"queued {iid}" in capsys.readouterr().out
    assert selfwork.store_item(conn, iid)["state"] == "queued"


def test_apply_on_a_locked_item_needs_yes(conn, capsys):
    iid = goal_with(conn, state="locked", locked=json.dumps(["eki/quota.py"]))
    assert main(["self", "apply", iid]) == 1
    assert "eki/quota.py" in capsys.readouterr().err
    assert selfwork.store_item(conn, iid)["state"] == "locked"
    assert main(["self", "apply", iid, "--yes"]) == 0
    assert selfwork.store_item(conn, iid)["state"] == "queued"


def test_autonomy_is_written_and_shown(conn, capsys):
    goal_with(conn, state="proposed")
    assert main(["self", "autonomy", "apply"]) == 0
    assert "autonomy apply" in capsys.readouterr().out
    data = json.loads(paths.config("routing").read_text())
    assert data["self"]["autonomy"] == "apply" and data["rows"]          # the rest is kept
    main(["self"])
    out = capsys.readouterr().out
    assert out.startswith("(autonomy apply") and "joins the queue" in out
    assert main(["self", "autonomy", "sometimes"]) == 1


def test_release_with_nothing_to_do(conn, capsys, monkeypatch):
    monkeypatch.setattr(train, "release", lambda c: [])
    assert main(["self", "release"]) == 0
    assert capsys.readouterr().out.strip() == "nothing to release"
    monkeypatch.setattr(train, "release", lambda c: ["train: build x is going live with 1 item(s)"])
    main(["self", "release"])
    assert "going live" in capsys.readouterr().out


def test_a_docs_only_item_says_so(conn, capsys):
    goal_with(conn, state="proposed", touched=json.dumps(["docs/self-build.md", "README.md"]))
    board(conn)
    line = next(l for l in capsys.readouterr().out.splitlines() if "files;" in l)
    assert "docs only" in line
    goal_with(conn, state="proposed", touched=json.dumps(["eki/a.py", "README.md"]))
    board(conn)
    assert capsys.readouterr().out.count("docs only") == 1


def test_a_judge_run_held_for_a_check_slot_says_so(conn, capsys):
    cfg = paths.config("routing")
    data = json.loads(cfg.read_text())
    data.setdefault("self", {})["check_slots"] = 1
    cfg.write_text(json.dumps(data))
    tid = store.create_thread(conn, "judge", "/tmp")
    busy = store.create_run(conn, tid, '["bin/check"]', provider="command")
    held = store.create_run(conn, tid, '["bin/check"]', provider="command")
    judge = store.create_run(conn, tid, '["bin/check"]', provider="command")
    with db.tx(conn):
        conn.execute("UPDATE runs SET state='running' WHERE id=?", (busy,))
    iid = goal_with(conn, state="queued", queued_at=db.now(), head="c" * 40, rebased="d" * 40,
                    gate2_run=held, gate2_on="d" * 40)
    jid = goal_with(conn, state="judging", run_id=judge)
    board(conn)
    out = capsys.readouterr().out.splitlines()
    queued = next(l for l in out if l.strip().startswith("1."))
    assert iid in queued and "waiting for a check slot" in queued and "gate 2 (run" not in queued
    at = next(n for n, l in enumerate(out) if l.strip().startswith(jid))
    assert "waiting for a check slot" in out[at + 1]


def test_builds_show_the_trains_check(capsys):
    for name, state in (("b-1", "green"), ("b-2", "red"), ("b-3", None)):
        d = builds.root() / name
        d.mkdir()
        (d / ".eki-build.json").write_text(json.dumps({"id": name, "commit": "f" * 40, "made_at": 1}))
        if state:
            (d / ".checked").write_text(json.dumps({"state": state, "run": "r", "tail": "", "at": 1}))
    assert builds_cmd.run(None) == 0
    out = capsys.readouterr().out.splitlines()
    line = {name: next(l for l in out if f" {name} " in l) for name in ("b-1", "b-2", "b-3")}
    assert "checked" in line["b-1"] and "check red" in line["b-2"]
    assert "check" not in line["b-3"].replace(str(builds.root()), "")


def drafting_goal(conn, question=None, answer=None, **fields):
    """A goal with a draft run (and maybe a question on it), put in directly: no real draft."""
    gid = store.new_id()
    tid = store.create_thread(conn, "draft", "/tmp")
    rid = store.create_run(conn, tid, "draft it", provider="fake")
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, wish, source, owner, state, draft_run, created_at)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     (gid, fields.get("text", "make it faster"), "make it faster", "ask", "you",
                      fields.get("state", "drafting"), rid, db.now()))
        aid = None
        if question:
            aid = asks.create(conn, rid, tid, "question", {"questions": [
                {"question": question, "header": "Pick", "options": [{"label": "a"}, {"label": "b"}]}]})
    if answer:
        asks.answer(conn, aid, {"allow": True, "answers": {question: answer}})
    return gid, rid, aid


def test_a_drafting_goal_shows_its_run(conn, capsys):
    gid, rid, _ = drafting_goal(conn)
    board(conn)
    out = capsys.readouterr().out
    assert f"drafting  (run {rid})" in out and "asked you" not in out


def test_a_drafting_goal_with_an_open_question_says_what_it_asked(conn, capsys):
    gid, rid, aid = drafting_goal(conn, question="Which cache?\nmore")
    board(conn)
    lines = capsys.readouterr().out.splitlines()
    at = next(n for n, l in enumerate(lines) if f"drafting  (run {rid})" in l)
    assert lines[at + 1].strip() == f"asked you: Which cache? — eki answer {aid}"


def test_show_a_goal_prints_wish_goal_questions_and_items(conn, capsys):
    gid, rid, _ = drafting_goal(conn, question="Which cache?", answer="b", state="planned",
                                text="What must be true when done: a cache.")
    with db.tx(conn):
        conn.execute("UPDATE goals SET drafted_at=? WHERE id=?", (db.now(), gid))
        iid = selfwork.new_item(conn, gid, "add the cache", "do it", [], [], False)
    assert main(["self", "show", gid[:6]]) == 0
    out = capsys.readouterr().out
    assert f"goal {gid}  planned" in out and "make it faster" in out
    assert "goal (drafted " in out and "What must be true when done: a cache." in out
    assert "? Which cache?" in out and "→ b" in out
    assert iid in out and "add the cache" in out


def test_show_a_goal_with_an_open_question_says_how_to_answer(conn, capsys):
    gid, rid, aid = drafting_goal(conn, question="Which cache?")
    main(["self", "show", gid])
    assert f"(open — eki answer {aid})" in capsys.readouterr().out


def test_show_an_item_is_unchanged(conn, capsys):
    iid = goal_with(conn, state="proposed")
    assert main(["self", "show", iid]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"item {iid}") and "title:    the item" in out


def test_the_parser_takes_as_is_and_skips_the_draft(conn, capsys, monkeypatch):
    got = {}
    monkeypatch.setattr(selfwork, "submit", lambda c, text, **kw: got.update(kw) or _planned(c))
    assert main(["self", "--as-is", "--bg", "a goal I wrote"]) == 0
    assert got["draft"] is False and got["plan"] is True
    main(["self", "--bg", "a wish"])
    assert got["draft"] is True
    main(["self", "--one", "one thing"])
    assert got["draft"] is False and got["plan"] is False


def test_a_wish_with_bg_says_it_is_drafting(conn, capsys, monkeypatch):
    gid, rid, _ = drafting_goal(conn)
    monkeypatch.setattr(selfwork, "submit", lambda c, text, **kw: gid)
    assert main(["self", "--bg", "make it faster"]) == 0
    assert capsys.readouterr().out.strip() == f"goal {gid}: drafting (run {rid})"


def _planned(conn):
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at) VALUES (?,?,?,?,?,?)",
                     (gid, "g", "ask", "you", "planning", db.now()))
    return gid
