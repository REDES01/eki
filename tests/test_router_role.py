"""The prompt check and narrow run on the router role: its model, its fallback, its timings."""
import json

import pytest

from eki import capacity, providers, roles, routing, store


class Judge:
    """A checker that answers with scripted text and remembers how it was asked."""
    def __init__(self, text):
        self.text, self.asked, self.cfg = text, [], {}

    def available(self):
        return True, ""

    def complete(self, messages, **kw):
        self.asked.append(kw)
        return {"text": self.text}


@pytest.fixture
def setup(home, monkeypatch):
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"},
        "mlx": {"kind": "local", "base_url": "http://127.0.0.1:9"},
        "claude": {"kind": "claude_code", "models": {"fast": "claude-haiku-4-5"}}}))
    (home / "routing.json").write_text(json.dumps({
        "checker": "mlx", "roles": {"router": "claude:fast"},
        "rows": [{"key": "general", "title": "all", "targets": ["fake"]},
                 {"key": "answer", "title": "words", "targets": ["fake"]},
                 {"key": "code", "title": "tools", "targets": ["fake"]},
                 {"key": "code-easy", "title": "a small change", "targets": ["fake"]}]}))
    down: dict = {}
    real = capacity.status

    def status(conn, background=False):
        got = {**real(conn, background), "claude": (True, ""), "mlx": (True, "")}
        return {**got, **{n: (False, why) for n, why in down.items()}}

    monkeypatch.setattr(capacity, "status", status)
    judges = {"claude": Judge('{"row": "answer", "why": "words"}'),
              "mlx": Judge('{"row": "answer", "why": "words"}')}
    get = providers.get
    monkeypatch.setattr(providers, "get", lambda n: judges[n] if n in judges else get(n))
    return down, judges


def decide(conn, prompt, cwd=None):
    tid = store.create_thread(conn, prompt[:20], cwd)
    rid = store.create_run(conn, tid, prompt)
    return routing.decide(conn, store.run(conn, rid))


def calls(conn):
    return [tuple(r) for r in conn.execute("SELECT role, target, model, ok FROM role_calls ORDER BY id")]


def test_the_router_role_is_asked_with_its_model_and_timed(setup, conn):
    _, judges = setup
    d = decide(conn, "what's a good name for a cat")
    assert (d.provider, d.row) == ("fake", "answer")
    assert d.why == "prompt check: words → answer → fake"
    assert judges["claude"].asked[0]["model"] == "claude-haiku-4-5" and judges["mlx"].asked == []
    assert calls(conn) == [("router", "claude:fast", "claude-haiku-4-5", 1)]
    assert set(roles.timings(conn, "router")) == {"claude:fast"}


def test_a_router_that_cant_run_falls_back_to_local_and_says_why(setup, conn):
    down, judges = setup
    down["claude"] = "limit reached"
    d = decide(conn, "what's a good name for a cat")
    assert d.row == "answer" and judges["claude"].asked == []
    assert "model" not in judges["mlx"].asked[0]
    assert d.why == ("prompt check: words (router claude:fast can't run (claude: limit reached) → mlx)"
                     " → answer → fake")
    assert calls(conn) == [("router", "mlx", None, 1)]


def test_with_local_down_too_it_goes_to_general(setup, conn):
    down, judges = setup
    down.update(claude="limit reached", mlx="not running")
    d = decide(conn, "what's a good name for a cat")
    assert d.row == "general" and judges["claude"].asked == judges["mlx"].asked == []
    assert d.why.startswith("prompt check skipped: router claude:fast can't run (claude: limit reached)"
                            " → mlx, which can't run either (mlx: not running)")
    assert calls(conn) == []


def test_narrow_uses_the_same_pick(setup, conn, tmp_path):
    _, judges = setup
    judges["claude"].text = '{"row": "code-easy", "why": "a flag"}'
    d = decide(conn, "add a --verbose flag", cwd=str(tmp_path))
    assert d.row == "code-easy" and d.why == "rule: has a folder → code; check: easy → code-easy → fake"
    assert [a["model"] for a in judges["claude"].asked] == ["claude-haiku-4-5"]
    assert calls(conn) == [("router", "claude:fast", "claude-haiku-4-5", 1)]


def test_explain_records_the_call_too(setup, conn):
    e = routing.explain(conn, "what's a good name for a cat")
    assert e["row"] == "answer" and e["targets"][0]["name"] == "fake"
    assert calls(conn) == [("router", "claude:fast", "claude-haiku-4-5", 1)]


def test_a_failed_call_is_recorded_as_failed(setup, conn):
    _, judges = setup

    def boom(messages, **kw):
        raise OSError("refused")
    judges["claude"].complete = boom
    d = decide(conn, "what's a good name for a cat")
    assert d.row == "general" and d.why.startswith("prompt check failed: refused")
    assert calls(conn) == [("router", "claude:fast", "claude-haiku-4-5", 0)]


def test_checker_name_reads_the_role(setup):
    assert routing.checker_name() == "claude:fast"


def test_an_object_router_target_is_asked_with_its_effort(setup, conn, home):
    _, judges = setup
    data = json.loads((home / "routing.json").read_text())
    data["roles"] = {"router": {"provider": "claude", "model": "fast", "effort": "low"}}
    (home / "routing.json").write_text(json.dumps(data))
    d = decide(conn, "what's a good name for a cat")
    assert d.row == "answer"
    assert judges["claude"].asked[0] == {"max_tokens": 80, "timeout": 30,
                                         "model": "claude-haiku-4-5", "effort": "low"}
    assert calls(conn) == [("router", "claude:fast@low", "claude-haiku-4-5", 1)]
