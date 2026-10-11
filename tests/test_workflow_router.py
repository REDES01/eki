"""Router off in a project's workflow card: chats and goal runs go straight to its target."""
import importlib
import json

import pytest

from eki import capacity, components, routing, store

checkmod = importlib.import_module("eki.routing.check")   # routing.check is also the function


@pytest.fixture
def setup(home, monkeypatch):
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"},
        "claude": {"kind": "claude_code", "models": {"top": "claude-opus-4-1"}}}))
    (home / "routing.json").write_text(json.dumps({
        "rows": [{"key": "general", "title": "all", "targets": ["fake"]},
                 {"key": "answer", "title": "words", "targets": ["fake"]},
                 {"key": "code", "title": "tools", "targets": ["fake"]},
                 {"key": "code-easy", "title": "a small change", "targets": ["fake"]}]}))
    down: dict = {}
    real = capacity.status

    def status(conn, background=False):
        got = {**real(conn, background), "claude": (True, ""), "fake": (True, "")}
        return {**got, **{n: (False, why) for n, why in down.items()}}

    monkeypatch.setattr(capacity, "status", status)
    asked = []

    def check(prompt, **kw):
        asked.append("check")
        return "answer", "check: words"

    def narrow(prompt, **kw):
        asked.append("narrow")
        return None

    monkeypatch.setattr(routing, "check", check)
    monkeypatch.setattr(routing, "narrow", narrow)
    monkeypatch.setattr(checkmod, "check", check)
    monkeypatch.setattr(checkmod, "narrow", narrow)
    return down, asked


def card(root, **spec):
    folder = root / "components" / "workflow"
    folder.mkdir(parents=True, exist_ok=True)
    base = json.loads((components.templates_dir() / "workflow" / "component.json").read_text())
    (folder / "component.json").write_text(json.dumps({**base, **spec}))


def decide(conn, prompt, cwd, row=None):
    tid = store.create_thread(conn, prompt[:20], str(cwd))
    rid = store.create_run(conn, tid, prompt, row=row)
    return routing.decide(conn, store.run(conn, rid))


def test_router_off_goes_to_the_target_for_chats_and_goal_runs(setup, conn, proj):
    _, asked = setup
    card(proj, router="off", target="claude:opus")
    for row in (None, "code"):
        d = decide(conn, "explain the app", proj, row=row)
        assert d.provider == "claude" and d.row == "workflow"
        assert d.why.startswith("router off (workflow card) → claude")
    assert asked == []


def test_router_off_waits_for_its_target(setup, conn, proj):
    down, asked = setup
    card(proj, router="off", target="claude:opus")
    down["claude"] = "five_hour 100%"
    d = decide(conn, "explain the app", proj)
    assert d.provider is None
    assert d.why == "router off (workflow card) → claude:opus: waiting — five_hour 100%"
    assert asked == []


def test_router_on_and_no_card_route_as_before(setup, conn, proj, tmp_path):
    _, asked = setup
    card(proj, router="on")
    d = decide(conn, "hello there", proj)
    assert d.provider == "fake" and d.row == "answer" and asked == ["check"]
    bare = tmp_path / "bare"
    bare.mkdir()
    d = decide(conn, "hello there", bare)
    assert d.provider == "fake" and d.row == "answer" and asked == ["check", "check"]


def test_a_broken_card_is_no_card(setup, conn, proj):
    _, asked = setup
    card(proj, router="off")                    # no target: the card doesn't load
    d = decide(conn, "hello there", proj)
    assert d.row == "answer" and asked == ["check"]
