"""`eki use <name>`: a component call is a run through the engine; agents are told what they can reach."""
import json

import pytest

from eki import components, db, store, worker
from eki.cli import main, use
from eki.components import Result, service
from eki.providers.base import Turn

from conftest import run_inline


def put_pool(name="stub", about="does a stub thing"):
    d = components.pool_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps({"kind": "service", "about": about, "run": "echo hi"}))
    return d


@pytest.fixture
def calls(monkeypatch):
    """The service kind's call, stubbed: records each Call and answers with what's queued."""
    seen, answers = [], []

    def call(comp, c):
        seen.append((comp, c))
        a = answers.pop(0) if answers else Result(text="ok")
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(service, "call", call)
    return seen, answers


@pytest.fixture
def inline(monkeypatch, conn):
    """No engine: the newest component run is worked right here, as the engine would."""
    def start(quiet=False):
        r = conn.execute("SELECT id FROM runs WHERE provider='component' AND state='queued' "
                         "ORDER BY created_at DESC LIMIT 1").fetchone()
        if r:
            run_inline(conn, r["id"])
    monkeypatch.setattr(use.common, "ensure_engine", start)


def component_runs(conn):
    return conn.execute("SELECT * FROM runs WHERE provider='component'").fetchall()


def test_missing_component_fails_with_reason_and_no_run(proj, conn, monkeypatch, capsys):
    monkeypatch.chdir(proj)
    put_pool("other")
    assert main(["use", "nope", "x"]) == 1
    err = capsys.readouterr().err
    assert "no component nope" in err and "other" in err
    assert component_runs(conn) == []


def test_call_creates_the_run_as_specified(proj, conn, monkeypatch, calls, inline, capsys):
    put_pool()
    monkeypatch.chdir(proj)
    parent = store.create_run(conn, store.create_thread(conn, "caller", str(proj)), "hi", provider="fake")
    monkeypatch.setenv("EKI_RUN", parent)
    seen, answers = calls
    answers.append(Result(text="made it", paths=["/tmp/a.png", "/tmp/b.png"]))
    assert main(["use", "stub", "one", "--two"]) == 0
    [r] = component_runs(conn)
    assert r["pinned"] == 1 and r["priority"] == "now" and r["parent"] == parent and r["state"] == "done"
    t = store.thread(conn, r["thread_id"])
    assert t["title"] == "use stub" and t["cwd"] is None and t["by"] == "eki"
    assert json.loads(r["prompt"]) == {"component": "stub", "args": ["one", "--two"],
                                       "caller_dir": str(proj.resolve()), "caller_run": parent}
    comp, c = seen[0]
    assert comp.name == "stub" and c.args == ["one", "--two"] and c.run_id == r["id"]
    assert c.caller_run == parent and c.caller_dir == str(proj.resolve())
    assert capsys.readouterr().out.splitlines() == ["made it", "/tmp/a.png", "/tmp/b.png"]


def test_by_hand_has_no_parent(proj, conn, monkeypatch, calls, inline):
    put_pool()
    monkeypatch.chdir(proj)
    assert main(["use", "stub"]) == 0
    [r] = component_runs(conn)
    assert r["parent"] is None and json.loads(r["prompt"])["caller_run"] is None


def test_failed_call_exits_nonzero_with_reason(proj, conn, monkeypatch, calls, inline, capsys):
    put_pool()
    monkeypatch.chdir(proj)
    calls[1].append(Result(error="the model is not filled in"))
    assert main(["use", "stub"]) == 1
    assert "the model is not filled in" in capsys.readouterr().err
    assert component_runs(conn)[0]["state"] == "failed"


def test_wait_prints_each_reason_once(conn, monkeypatch, capsys):
    tid = store.create_thread(conn, "use x", None, by="eki")
    rid = store.create_run(conn, tid, "{}", provider="component")
    steps = iter([("waits for voice: held by abc", "queued"), ("waits for voice: held by abc", "queued"),
                  ("waits for voice: held by def", "queued"), (None, "done")])

    def tick(_):
        why, state = next(steps)
        fields = {"state": state} if why is None else {"why": why}
        store.update_run(conn, rid, **fields)
    monkeypatch.setattr(use.time, "sleep", tick)
    store.update_run(conn, rid, why="waits for voice: held by abc")
    assert use.wait(conn, rid, "x") == 0
    err = capsys.readouterr().err.splitlines()
    assert err == ["eki use x: waits for voice: held by abc", "eki use x: waits for voice: held by def"]


def _take(conn, prompt):
    from eki import providers
    events = []
    turn = Turn(prompt=prompt, history=[], run_id="r1", thread_id="t1", cwd="/tmp")
    out = providers.get("component").take(turn, lambda k, d: events.append((k, d)))
    return out, events


def test_provider_turns_a_result_into_events(proj, conn, calls):
    put_pool()
    calls[1].append(Result(text="hello", paths=["/x/1.wav"]))
    out, events = _take(conn, json.dumps({"component": "stub", "args": ["a"], "caller_dir": str(proj),
                                          "caller_run": None}))
    assert out.state == "done"
    assert events == [("text", {"text": "hello"}), ("tool", {"name": "eki use stub", "path": "/x/1.wav"})]


def test_provider_fails_on_error_exception_or_missing(proj, conn, calls):
    put_pool()
    prompt = json.dumps({"component": "stub", "args": [], "caller_dir": str(proj), "caller_run": None})
    calls[1].extend([Result(error="busy elsewhere"), RuntimeError("boom")])
    out, _ = _take(conn, prompt)
    assert out.state == "failed" and out.error == "busy elsewhere"
    out, _ = _take(conn, prompt)
    assert out.state == "failed" and "boom" in out.error
    out, _ = _take(conn, prompt.replace('"stub"', '"gone"'))
    assert out.state == "failed" and "no component gone" in out.error
    out, _ = _take(conn, "not json")
    assert out.state == "failed" and "JSON" in out.error


def test_component_is_built_in_never_routed():
    from eki import providers
    assert providers.config()["component"]["kind"] == "component"
    assert "component" in providers.BUILTIN and providers.capabilities("component", {"kind": "component"}) == []


def _turn_for(conn, cwd, row=None):
    tid = store.create_thread(conn, "t", str(cwd))
    rid = store.create_run(conn, tid, "hi", provider="fake", row=row)
    return worker.build_turn(conn, store.run(conn, rid), "fake")


def test_brief_line_when_reachable(proj, conn):
    put_pool("stub", "says hello")
    turn = _turn_for(conn, proj)
    assert "eki use" in turn.extra["note"] and "stub — says hello" in turn.extra["note"]
    assert "\n" not in turn.extra["note"]
    assert "note" not in _turn_for(conn, proj, row="chore").extra


def test_no_components_leaves_the_turn_as_today(proj, conn):
    turn = _turn_for(conn, proj)
    assert "note" not in turn.extra
