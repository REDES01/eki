"""Chores: eki's own text jobs as runs, on their role (eki/chores.py)."""
import json

import pytest

from eki import capacity, chores, db, engine, providers, store
from conftest import run_inline

#: the fake stands in for the local model on chores
pytestmark = pytest.mark.usefixtures("text_fake")


def use_local(home, name="bare"):
    cfg = json.loads((home / "routing.json").read_text())
    cfg["self"] = {"local": name}
    (home / "routing.json").write_text(json.dumps(cfg))


def start(conn, kind="review", subject="it1", prompt="steps=1 look at this"):
    with db.tx(conn):
        return chores.start(conn, kind, subject, prompt, "background")


def test_no_local_model_skips_the_chore(conn):
    assert start(conn) is None
    c = chores.latest(conn, "review", "it1")
    assert c["state"] == "skipped" and c["run_id"] is None and c["result"] == "no local model"
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert chores.finished(conn, "review") == []


def test_off_or_nobody_skips_with_the_why_and_the_first_local_kind_is_the_default(home, conn, monkeypatch):
    monkeypatch.setattr(capacity, "status", lambda conn, background=False: {
        n: (True, "") for n in providers.config()})
    prov = json.loads((home / "providers.json").read_text())
    prov["mlx"] = {"kind": "local"}
    prov["mlx2"] = {"kind": "local"}
    (home / "providers.json").write_text(json.dumps(prov))
    assert store.run(conn, start(conn))["provider"] == "mlx"
    use_local(home, "off")
    assert start(conn, subject="it2") is None
    assert chores.latest(conn, "review", "it2")["result"] == "the chores role is off"
    use_local(home, "nobody")
    assert start(conn, subject="it3") is None
    assert "nobody: no such provider" in chores.latest(conn, "review", "it3")["result"]
    use_local(home, "mlx2")
    assert store.run(conn, start(conn, subject="it4"))["provider"] == "mlx2"


def set_roles(home, **want):
    cfg = json.loads((home / "routing.json").read_text())
    cfg["roles"] = want
    (home / "routing.json").write_text(json.dumps(cfg))


def test_a_chore_on_a_role_target_gets_its_provider_model_and_why(home, conn):
    use_local(home)
    prov = json.loads((home / "providers.json").read_text())
    prov["fake2"]["models"] = {"fast": "small-1"}
    (home / "providers.json").write_text(json.dumps(prov))
    set_roles(home, chores="fake2:fast")
    r = store.run(conn, start(conn))
    assert (r["provider"], r["model"], r["row"], r["pinned"]) == ("fake2", "small-1", "chore", 1)
    assert r["why"] == "chores → fake2:fast"
    set_roles(home, chores="fake2:top")                   # an unpicked tier: its default, and why
    r = store.run(conn, start(conn, subject="it3"))
    assert r["model"] is None and r["why"] == "chores → fake2:top; fake2 has no top model picked; its default"
    with db.tx(conn):                                     # a named role, not `chores`
        rid = chores.start(conn, "review", "it2", "look", "now", role="reviewer")
    assert store.run(conn, rid)["provider"] == "bare"


def test_over_the_background_budget_a_chore_falls_back_to_local_with_the_why(home, conn, monkeypatch):
    use_local(home)
    set_roles(home, chores="fake2")

    def status(conn, background=False):
        out = {n: (True, "") for n in providers.config()}
        if background:
            out["fake2"] = (False, "kept for you: day share used")
        return out
    monkeypatch.setattr(capacity, "status", status)
    r = store.run(conn, start(conn))                       # a background chore
    assert r["provider"] == "bare" and r["model"] is None
    assert r["why"] == "chores fake2 can't run (fake2: kept for you: day share used) → bare"
    with db.tx(conn):
        rid = chores.start(conn, "digest", "p", "look", "now")
    assert store.run(conn, rid)["provider"] == "fake2"


def test_a_role_that_cant_run_with_no_default_skips_with_the_why(home, conn, monkeypatch):
    use_local(home)
    set_roles(home, chores="fake2")
    monkeypatch.setattr(capacity, "status", lambda conn, background=False: {
        "fake2": (False, "limit reached"), "bare": (False, "not running")})
    assert start(conn) is None
    why = chores.latest(conn, "review", "it1")["result"]
    assert "fake2: limit reached" in why and "bare: not running" in why


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
