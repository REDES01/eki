import json
import time

import pytest

from eki import capacity, roles, store


def _routing(home, **data):
    (home / "routing.json").write_text(json.dumps(data))


def _providers(home, **extra):
    got = json.loads((home / "providers.json").read_text())
    got.update(extra)
    (home / "providers.json").write_text(json.dumps(got))


def _status(monkeypatch, down=None, kept=None):
    """capacity.status: everything up but `down` {name: why}; `kept` {name: why} only in the background."""
    def status(conn, background=False):
        from eki import providers
        out = {n: (True, "") for n in providers.config()}
        out.update({n: (False, w) for n, w in (down or {}).items()})
        if background:
            out.update({n: (False, "kept for you: " + w) for n, w in (kept or {}).items()})
        return out
    monkeypatch.setattr(capacity, "status", status)


ROWS = [{"key": "code", "title": "tools", "targets": ["fake2", "fake"]}]


# -- defaults: exactly today's settings ---------------------------------------

def test_router_default_is_the_checker_else_local(home):
    assert roles.default("router") == "none"                 # the fixture's checker
    _routing(home, rows=ROWS)
    assert roles.default("router") == "local"
    assert roles.get("router") == "local"


def test_planner_default_reads_self_planner_else_the_code_rows_first(home):
    _routing(home, rows=ROWS)
    assert roles.default("planner") == "fake2"
    _routing(home, rows=ROWS, self={"planner": {"provider": "fake"}})
    assert roles.default("planner") == "fake"
    _routing(home, rows=ROWS, self={"planner": {"provider": "fake", "model": "opus"}})
    assert roles.default("planner") == "fake:opus"
    _routing(home)
    assert roles.default("planner") == "claude"              # the default table's code row


def test_reviewer_and_chores_default_to_self_local_else_the_first_local(home):
    assert roles.default("chores") == ""
    _providers(home, mlx={"kind": "local", "base_url": "http://127.0.0.1:9"})
    assert roles.default("chores") == "mlx"
    assert roles.default("reviewer") == "mlx"
    _routing(home, self={"local": "off"})
    assert roles.default("reviewer") == "off"
    assert roles.pick("reviewer") == roles.Pick(None, None, "off", "off")
    _routing(home, self={"local": "fake"})
    assert roles.get("chores") == "fake"


def test_a_role_set_wins_and_off_only_where_allowed(home):
    _routing(home, roles={"chores": "off", "router": "fake"})
    assert roles.get("chores") == "off"
    assert roles.pick("chores").provider is None
    assert roles.get("router") == "fake"
    known = {"fake", "claude"}
    assert roles.problems({"chores": "off", "router": "claude:fast"}, known) == []
    assert roles.problems({"router": "off"}, known) == ["roles: router can't be off"]
    assert any("unknown role" in p for p in roles.problems({"boss": "claude"}, known))
    assert any("unknown provider" in p for p in roles.problems({"router": "nope:fast"}, known))
    assert roles.problems({"router": "claude:"}, known)
    assert roles.problems(["x"], known)


def test_set_role_keeps_every_other_key(home):
    _routing(home, checker="local", rows=ROWS, self={"planner": {"provider": "fake"}}, quota={"stop_at": 0.9})
    before = json.loads((home / "routing.json").read_text())
    roles.set_role("router", "claude:fast")
    after = json.loads((home / "routing.json").read_text())
    assert after == {**before, "roles": {"router": "claude:fast"}}
    roles.set_role("planner", "fake:top")
    assert json.loads((home / "routing.json").read_text())["roles"] == \
        {"router": "claude:fast", "planner": "fake:top"}
    roles.set_role("router", "default")
    roles.set_role("planner", None)
    assert json.loads((home / "routing.json").read_text()) == before
    with pytest.raises(KeyError):
        roles.set_role("boss", "fake")


# -- picks and their fallbacks ------------------------------------------------

def test_a_pick_resolves_the_tier_against_the_read_list(home, conn, monkeypatch):
    _status(monkeypatch)
    _providers(home, claude={"kind": "claude_code", "models": {"fast": "claude-haiku-4-5",
                                                                "top": "claude-opus-4-1"}})
    _routing(home, roles={"router": "claude:fast", "planner": "claude:top"})
    assert roles.pick("router", conn) == roles.Pick("claude", "claude-haiku-4-5", "claude:fast", "")
    conn.execute("INSERT INTO provider_models VALUES ('claude','claude-haiku-4-5','Haiku',0,?)", (time.time(),))
    got = roles.pick("planner", conn)
    assert (got.provider, got.model) == ("claude", None)
    assert "claude-opus-4-1 isn't in claude's list any more" in got.why


def test_a_provider_not_installed_falls_back_to_the_default(home, conn, monkeypatch):
    _status(monkeypatch, down={"claude": "claude is not installed"})
    _providers(home, claude={"kind": "claude_code"}, mlx={"kind": "local"})
    _routing(home, checker="mlx", roles={"router": "claude:fast"})
    got = roles.pick("router", conn)
    assert (got.provider, got.target) == ("mlx", "mlx")
    assert got.why == "router claude:fast can't run (claude: claude is not installed) → mlx"


def test_a_provider_at_a_limit_falls_back(home, conn):
    _routing(home, rows=ROWS, roles={"planner": "fake"})
    store.cool(conn, "fake", time.time() + 600, "limit reached")
    got = roles.pick("planner", conn)
    assert got.provider == "fake2"
    assert got.why.startswith("planner fake can't run (fake: limit reached until ")
    assert got.why.endswith(" → fake2")


def test_a_text_role_needs_complete(home, conn, monkeypatch):
    _status(monkeypatch)
    _providers(home, mlx={"kind": "local"})
    _routing(home, self={"local": "mlx"}, roles={"chores": "fake"})
    got = roles.pick("chores", conn)
    assert got.provider == "mlx"
    assert got.why == "chores fake can't run (fake: can't answer a one-shot text call) → mlx"


def test_a_planner_needs_tools(home, conn, monkeypatch):
    _status(monkeypatch)
    _providers(home, mlx={"kind": "local"})
    _routing(home, rows=ROWS, roles={"planner": "mlx"})
    got = roles.pick("planner", conn)
    assert got.provider == "fake2"
    assert got.why == "planner mlx can't run (mlx: has no tools) → fake2"


def test_a_background_pick_over_budget_falls_back_at_once(home, conn, monkeypatch):
    _status(monkeypatch, kept={"claude": "day share used"})
    _providers(home, claude={"kind": "claude_code"}, mlx={"kind": "local"})
    _routing(home, self={"local": "mlx"}, roles={"chores": "claude:fast"})
    assert roles.pick("chores", conn).provider == "claude"
    got = roles.pick("chores", conn, background=True)
    assert got.provider == "mlx"
    assert got.why == "chores claude:fast can't run (claude: kept for you: day share used) → mlx"


def test_no_default_either_gives_no_provider(home, conn, monkeypatch):
    _status(monkeypatch, down={"mlx": "not running", "claude": "limit reached"})
    _providers(home, claude={"kind": "claude_code"}, mlx={"kind": "local"})
    _routing(home, self={"local": "mlx"}, roles={"reviewer": "claude"})
    got = roles.pick("reviewer", conn)
    assert got.provider is None
    assert "claude: limit reached" in got.why and "mlx: not running" in got.why
    _routing(home, self={"local": "mlx"})
    got = roles.pick("reviewer", conn)
    assert got.provider is None and got.why == "reviewer mlx can't run (mlx: not running) → nobody"


def test_a_checker_of_none_gives_no_router(home, conn):
    got = roles.pick("router", conn)                         # the fixture: {"checker": "none"}
    assert got.provider is None
    assert "none: no such provider" in got.why
    assert roles.pick("router").provider is None             # without a connection too


def test_a_switched_off_provider_falls_back(home, monkeypatch):
    _providers(home, claude={"kind": "claude_code", "off": True}, mlx={"kind": "local"})
    _routing(home, checker="mlx", roles={"router": "claude"})
    got = roles.pick("router")
    assert got.provider == "mlx" and "claude: switched off" in got.why


# -- timings ------------------------------------------------------------------

def test_record_and_timings_take_the_median_of_the_last_n(conn):
    for ms in (100, 200, 300, 9000):
        roles.record(conn, "router", "claude:fast", "claude-haiku-4-5", ms, True)
    for ms in (13000, 15000):
        roles.record(conn, "router", "local", None, ms, False)
    roles.record(conn, "chores", "local", None, 1, True)
    got = roles.timings(conn, "router")
    assert got == {"claude:fast": (250.0, 4), "local": (14000.0, 2)}
    assert roles.timings(conn, "router", n=2)["claude:fast"] == (4650.0, 2)
    assert roles.timings(conn, "planner") == {}
