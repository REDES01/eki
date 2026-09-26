"""A run's pictures travel on the Turn, survive a handoff, and show in a joiner's history."""
import base64
import json

from eki import db, store, worker
from conftest import run_inline

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                       "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def picture(tmp_path, name="a.png"):
    p = tmp_path / name
    p.write_bytes(PNG)
    return str(p)


def seeing(home):
    """The test providers, plus a fake that can see."""
    cfg = json.loads((home / "providers.json").read_text())
    cfg["seer"] = {"kind": "fake", "can": ["text", "tools", "vision"]}
    (home / "providers.json").write_text(json.dumps(cfg))


def ask(conn, prompt, tid=None, **kw):
    with db.tx(conn):
        tid = tid or store.create_thread(conn, prompt, None)
        rid = store.create_run(conn, tid, prompt, **kw)
    return tid, rid


def test_a_picture_reaches_the_program(conn, home, tmp_path):
    seeing(home)
    _, rid = ask(conn, "steps=1 what is this", provider="seer", attachments=[picture(tmp_path)])
    assert run_inline(conn, rid)["state"] == "done"
    assert "images=1" in store.answer(conn, rid)


def test_only_pictures_go_as_images(conn, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    _, rid = ask(conn, "look", attachments=[picture(tmp_path), str(notes)])
    assert worker.build_turn(conn, store.run(conn, rid), "fake").images == [picture(tmp_path)]


def test_a_carry_on_resume_sends_no_pictures(conn, tmp_path):
    tid, rid = ask(conn, "look", attachments=[picture(tmp_path)])
    with db.tx(conn):
        store.save_session(conn, tid, "fake", "s1", rid)
    turn = worker.build_turn(conn, store.run(conn, rid), "fake")
    assert turn.prompt == worker.CARRY_ON and turn.images == []


def test_a_handoff_keeps_the_pictures(conn, tmp_path):
    pics = [picture(tmp_path)]
    tid, rid = ask(conn, "handoff please", provider="bare", attachments=pics)
    assert run_inline(conn, rid)["state"] == "handed_off"
    nxt = [x for x in store.thread_runs(conn, tid) if x["parent"] == rid][0]
    assert store.attachments(nxt) == pics


def test_a_joiner_sees_past_pictures_as_text(conn, home, tmp_path):
    seeing(home)
    pic = picture(tmp_path)
    tid, first = ask(conn, "steps=1 what is this", provider="seer", attachments=[pic])
    run_inline(conn, first)
    _, second = ask(conn, "and now?", tid=tid)
    msgs = store.transcript_since(conn, tid, 0, store.run(conn, second)["seq"])
    assert msgs[0]["role"] == "user" and msgs[0]["content"].endswith(f"[picture: {pic}]")
    assert all("[picture:" not in m["content"] for m in msgs[1:])
