"""A run's effort reaches the program, and routing writes it and says why."""
import json

from eki import api, db, routing, store, worker
from eki.providers import oneshot
from eki.providers.base import Turn
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex


def turn(effort=None):
    return Turn(prompt="hi", history=[], extra={"effort": effort} if effort else {})


def after(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def test_claude_argv_has_the_effort_only_when_set():
    assert after(ClaudeCode("claude", {}).argv(turn("high")), "--effort") == ["high"]
    assert "--effort" not in ClaudeCode("claude", {}).argv(turn())


def test_codex_turn_params_carry_the_effort():
    assert Codex("codex", {})._turn_params(turn("high"), "t1")["effort"] == "high"
    assert "effort" not in Codex("codex", {})._turn_params(turn(), "t1")
    assert "effort" not in Codex("codex", {})._thread_params(turn("high"))   # per turn, not per thread


def test_oneshot_argv_carries_the_effort():
    assert after(oneshot.claude_argv("claude", "opus", "sys", "low"), "--effort") == ["low"]
    assert "--effort" not in oneshot.claude_argv("claude", "opus", "sys")
    argv = oneshot.codex_argv("codex", "gpt-5", "sys", "high")
    assert 'model_reasoning_effort="high"' in after(argv, "-c") and argv[-1] == "-"
    assert not any("reasoning" in a for a in oneshot.codex_argv("codex", None, "sys"))


def test_chore_passes_the_effort_to_complete():
    seen = {}

    class P:
        name = "p"

        def complete(self, messages, **kw):
            seen.update(kw)
            return {"text": "ok", "tool_calls": []}

    t = Turn(prompt="hi", history=[], extra={"model": "opus", "effort": "high"})
    assert oneshot.chore(P(), t, lambda *a: None).state == "done"
    assert seen["model"] == "opus" and seen["effort"] == "high"


def _seeded(home, conn, target):
    (home / "providers.json").write_text(json.dumps({"claude": {"kind": "fake"}, "fake": {"kind": "fake"}}))
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "general", "title": "all", "targets": [target, "fake"]}]}))
    for model, label, efforts, default in (("default", "Default", ["low", "high"], 1),
                                           ("opus", "Opus 5.5", ["low", "medium", "high"], 0),
                                           ("haiku", "Haiku 4.5", None, 0)):
        conn.execute("INSERT INTO provider_models (provider, model, label, is_default, read_at, efforts)"
                     " VALUES (?,?,?,?,?,?)", ("claude", model, label, default, db.now(),
                                               json.dumps(efforts) if efforts else None))


def test_a_routed_run_records_model_and_effort(home, conn):
    _seeded(home, conn, {"provider": "claude", "model": "opus", "effort": "high"})
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", row="general")
    assert worker.route(conn, store.run(conn, rid)) == "claude"
    r = store.run(conn, rid)
    assert (r["model"], r["effort"]) == ("opus", "high")
    assert r["why"].endswith("→ general → claude · Opus 5.5 · high")
    ev = [json.loads(e["data"]) for e in store.events_after(conn, rid) if e["kind"] == "route"]
    assert ev[-1]["effort"] == "high"
    assert worker.build_turn(conn, r, "claude").extra["effort"] == "high"
    view = api._run_view(conn, r, full=False)
    assert (view["model"], view["effort"], view["shown"]) == ("opus", "high", "claude · Opus 5.5 · high")


def test_an_effort_the_model_doesnt_take_falls_back_and_says_so(home, conn):
    _seeded(home, conn, {"provider": "claude", "model": "haiku", "effort": "high"})
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", row="general")
    worker.route(conn, store.run(conn, rid))
    r = store.run(conn, rid)
    assert (r["model"], r["effort"]) == ("haiku", None)
    assert "claude · Haiku 4.5" in r["why"] and "haiku takes no effort" in r["why"]


def test_a_run_keeps_its_own_effort_and_a_reroute_drops_routings(home, conn):
    _seeded(home, conn, {"provider": "claude", "model": "opus", "effort": "high"})
    tid = store.create_thread(conn, "t", None)
    own = store.create_run(conn, tid, "hi", row="general", effort="low")
    worker.route(conn, store.run(conn, own))
    assert store.run(conn, own)["effort"] == "low"
    rid = store.create_run(conn, tid, "hi", row="general")
    worker.route(conn, store.run(conn, rid))
    store.update_run(conn, rid, provider=None, exclude='["claude"]')   # claude hit its limit
    assert worker.route(conn, store.run(conn, rid)) == "fake"
    assert (store.run(conn, rid)["model"], store.run(conn, rid)["effort"]) == (None, None)


def test_explain_names_effort_and_label(home, conn):
    _seeded(home, conn, {"provider": "claude", "model": "opus", "effort": "high"})
    t = routing.explain(conn, "hi")["targets"][0]
    assert (t["name"], t["effort"], t["label"]) == ("claude:opus@high", "high", "claude · Opus 5.5 · high")
