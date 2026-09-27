"""Chores: local-model jobs as runs (eki/chores.py)."""
import json

from eki import chores, db, engine, store
from conftest import run_inline


def use_local(home, name="bare"):
    cfg = json.loads((home / "routing.json").read_text())
    cfg["self"] = {"local": name}
    (home / "routing.json").write_text(json.dumps(cfg))


def start(conn, kind="review", subject="it1", prompt="steps=1 look at this"):
    with db.tx(conn):
        return chores.start(conn, kind, subject, prompt, "background")


def test_no_local_model_skips_the_chore(conn):
    assert chores.local() is None
    assert start(conn) is None
    c = chores.latest(conn, "review", "it1")
    assert c["state"] == "skipped" and c["run_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert chores.finished(conn, "review") == []


def test_local_honours_off_and_falls_back_to_the_first_local_kind(home, conn):
    prov = json.loads((home / "providers.json").read_text())
    prov["mlx"] = {"kind": "local"}
    prov["mlx2"] = {"kind": "local"}
    (home / "providers.json").write_text(json.dumps(prov))
    assert chores.local() == "mlx"
    use_local(home, "off")
    assert chores.local() is None
    use_local(home, "nobody")
    assert chores.local() is None
    use_local(home, "mlx2")
    assert chores.local() == "mlx2"


def test_start_makes_a_pinned_run_on_the_chore_row(home, conn):
    use_local(home)
    rid = start(conn)
    r = store.run(conn, rid)
    assert r["provider"] == "bare" and r["pinned"] and r["row"] == "chore"
    assert r["priority"] == "background"
    t = store.thread(conn, r["thread_id"])
    assert t["title"] == "chore: review it1" and t["cwd"] is None
    c = chores.latest(conn, "review", "it1")
    assert c["state"] == "open" and c["run_id"] == rid and c["thread_id"] == r["thread_id"]


def test_finished_lists_it_once_the_run_ends_and_close_moves_it_once(home, conn):
    use_local(home)
    rid = start(conn)
    assert chores.finished(conn, "review") == []
    r = run_inline(conn, rid)
    assert r["state"] == "done" and r["row"] == "chore"      # routing keeps the row it was given
    assert chores.finished(conn, "digest") == []
    got = chores.finished(conn, "review")
    assert [c["run_id"] for c in got] == [rid] and got[0]["run_state"] == "done"
    with db.tx(conn):
        assert chores.close(conn, got[0]["id"], "done", "the text") is True
        assert chores.close(conn, got[0]["id"], "failed") is False
    c = chores.latest(conn, "review", "it1")
    assert c["state"] == "done" and c["result"] == "the text" and c["ended_at"]
    assert chores.finished(conn, "review") == []


def test_a_handoff_on_a_chore_fails_and_makes_no_run(home, conn):
    use_local(home)
    rid = start(conn, prompt="handoff please")
    r = run_inline(conn, rid)
    assert r["state"] == "failed" and r["error"].startswith("chore handed off")
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert chores.finished(conn, "review")[0]["run_state"] == "failed"


def test_the_local_model_sends_no_handoff_tool_on_a_chore(monkeypatch):
    import threading
    from eki.providers.base import Turn
    from eki.providers.local import Local, HANDOFF_TOOL, SYSTEM
    seen = {}

    def complete(self, messages, *, tools=None, **kw):
        seen["tools"], seen["system"] = tools, messages[0]["content"]
        return {"text": "<tool_call>handoff</tool_call>", "tool_calls": [{"name": "handoff", "arguments": "{}"}]}

    monkeypatch.setattr(Local, "complete", complete)
    p = Local("mlx", {"kind": "local"})
    turn = Turn(prompt="x", history=[], run_id="r", thread_id="t", cwd="/tmp", images=[])
    turn.stop = threading.Event()
    turn.extra["row"] = "chore"
    out = p.take(turn, lambda k, d: None)
    assert seen["tools"] is None and seen["system"] != SYSTEM and "handoff" not in seen["system"]
    assert out.state == "done"
    turn.extra["row"] = "general"
    assert p.take(turn, lambda k, d: None).state == "handed_off" and seen["tools"] == [HANDOFF_TOOL]


def test_a_restart_mid_chore_loses_nothing(home, conn):
    use_local(home)
    rid = start(conn)
    # the worker was running when everything stopped: its pid is gone, its heartbeat stale
    store.update_run(conn, rid, state="running", pid=999999, attempt=1, heartbeat=db.now() - 3600)
    fresh = db.connect()                     # the engine comes back with nothing in memory
    engine.reap(fresh)
    assert store.run(fresh, rid)["state"] == "queued"
    assert chores.finished(fresh, "review") == []
    assert run_inline(fresh, rid)["state"] == "done"
    assert [c["run_id"] for c in chores.finished(fresh, "review")] == [rid]
