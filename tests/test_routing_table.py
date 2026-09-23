# SPDX-License-Identifier: Apache-2.0
"""The routing table: which row a request is, where each row goes, what you
and eki changed, what a request costs on each subscription — and checking
all of it."""
import asyncio
import json
import time
from types import SimpleNamespace as NS

import pytest

from eki import capacity, prefs, table
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health, register
from eki.router import Need, Router


# ---- the prompt check -----------------------------------------------------------------

@pytest.mark.parametrize("task,difficulty,escalate,row", [
    ("chat", "easy", False, "quick"), ("math", "easy", False, "quick"),
    ("writing", "medium", False, "writing"), ("translate", "easy", False, "writing"),
    ("chat", "medium", False, "explain"), ("math", "hard", False, "explain"),
    ("repo", "medium", False, "code"), ("code", "easy", False, "code"), ("repo", "hard", False, "code_hard"),
    ("research", "easy", False, "research"), ("image", "easy", False, "picture"),
    ("repo", "easy", True, "retry"),
])
def test_a_label_maps_to_a_row(task, difficulty, escalate, row):
    assert table.row_for(task, difficulty, escalate)[0] == row


def test_a_request_you_filed_under_a_row_goes_there():
    ex = {"writing": ["draft release notes for version two of the app"]}
    assert table.row_for("repo", "medium", prompt="draft release notes for version three of the app",
                         examples=ex) == ("writing", "like a request you filed under it")


def test_translate_said_outright_beats_a_word_like_today():
    from eki import classify
    assert classify.rules("translate this sentence to Japanese: the station is closed today").task == "translate"


# ---- the default table ---------------------------------------------------------------------

def test_the_default_table_for_a_claude_first_mac():
    t = table.defaults(["claude_code", "codex"], "codex-qwen", ["flux"], ["claude_code", "codex"], [])
    assert t["quick"] == ["codex-qwen", "claude_code@fast", "codex@fast"]
    assert t["code"] == ["claude_code@default", "codex@default", "codex-qwen!easy"]
    assert t["retry"][0] == "claude_code@top" and t["picture"] == ["flux"]
    assert t["research"] == ["claude_code@default", "codex@default"]


def test_whoever_has_more_room_comes_first():
    t = table.defaults(["codex", "claude_code"], None, [], [], ["anthropic"])
    assert t["explain"] == ["codex@default", "claude_code@default", "anthropic@default"]


# ---- your cells, learned ones ----------------------------------------------------------------

BASE = table.defaults(["claude_code", "codex"], "codex-qwen", ["flux"], ["claude_code", "codex"], [])


def test_a_row_you_set_replaces_the_default_and_says_so():
    rules = {}
    table.set_row(rules, "code", ["codex@default", "claude_code@default"], "you", "use Codex for code")
    cell = table.effective(BASE, rules)["code"]
    assert cell["targets"][0] == "codex@default" and cell["source"] == "you" and "Codex" in cell["said"]


def test_never_and_backup_apply_to_every_row():
    rules = {}
    table.add_rule(rules, "backup", "codex", "you", "only use Codex if Claude runs out")
    eff = table.effective(BASE, rules)
    assert eff["explain"]["targets"] == ["claude_code@default", "codex-qwen", "codex@default"]
    table.add_rule(rules, "never", "codex", "you", "never use Codex")
    assert all(not t.startswith("codex@") for c in table.effective(BASE, rules).values() for t in c["targets"])


def test_a_thread_rule_goes_first_in_that_thread_only_and_expires():
    rules = {"threads": {"c1": {"targets": ["codex@default"], "until": None, "said": "for this thread use Codex"}}}
    assert table.effective(BASE, rules, "c1")["code"]["targets"][0] == "codex@default"
    assert table.effective(BASE, rules, "c2")["code"]["targets"][0] == "claude_code@default"
    rules["threads"]["c1"]["until"] = time.time() - 1
    assert table.effective(BASE, rules, "c1")["code"]["targets"][0] == "claude_code@default"


def test_picking_the_same_model_in_three_threads_is_learned_and_undo_sticks():
    now = time.time()
    rules = {"overrides": [{"row": "code", "to": "codex", "conversation": f"c{i}", "at": now} for i in range(2)]}
    assert not table.learnable(rules["overrides"], rules, "code", "codex", now)
    rules["overrides"].append({"row": "code", "to": "codex", "conversation": "c1", "at": now})
    assert not table.learnable(rules["overrides"], rules, "code", "codex", now)      # same thread twice
    rules["overrides"].append({"row": "code", "to": "codex", "conversation": "c9", "at": now})
    assert table.learnable(rules["overrides"], rules, "code", "codex", now)
    table.set_row(rules, "code", ["codex@default"], "learned", "you picked codex")
    assert table.forget(rules, "code") == ["code"]
    assert not table.learnable(rules["overrides"], rules, "code", "codex", now)      # you undid it
    table.set_row(rules, "code", ["claude_code@default"], "you", "use Claude for code")
    assert not table.learnable(rules["overrides"], rules, "code", "codex", now + 40 * 86400)  # yours wins


# ---- routing by the row ----------------------------------------------------------------------

class B(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        yield ""


def backend(key, kind, tier, **caps):
    b = B(BackendInfo(key=key, kind=kind, label=key,
                      capabilities=Capabilities(**{"tools": True, "repo": True, **caps}),
                      cost=Cost(tier=tier)), {})
    return b


def router(spent=(), up=None):
    claude, codex, local = backend("claude_code", "claude_code", 50), backend("codex", "codex", 50), \
        backend("codex-qwen", "codex", 0)
    claude.info.quota_source, codex.info.quota_source = "claude", "codex"

    class Q:
        def exhausted(self):
            return {k: "WEEK at 100%" for k in spent}

        def pace(self):
            return {}
    ladders = {"claude_code": {"default": "opus", "top": "fable", "fast": "haiku", "vendor": "Anthropic"}}
    return Router([claude, codex, local], quota=Q(), is_up=up or (lambda k: None),
                  ladder_for=lambda k: ladders.get(k, {}))


def need(row, difficulty="medium", **kw):
    return Need(tools=True, task="repo", difficulty=difficulty, row=row, row_title=table.TITLES[row],
                targets=BASE[row], **kw)


def test_the_first_choice_that_can_take_it_gets_it():
    c = router().choose(need("code"))
    assert (c.backend.key, c.model) == ("claude_code", "opus") and "Code change → 1st choice" in c.reason


def test_a_spent_first_choice_moves_down_the_row_and_says_why():
    c = router(spent=("claude",)).choose(need("code"))
    assert c.backend.key == "codex" and "2nd choice" in c.reason and "passed over claude_code: WEEK at 100%" in c.reason
    c = router(spent=("claude", "codex")).choose(need("code", difficulty="medium"))
    # the local model is "small changes only" in this row; with everything
    # else spent, the old way still gives an answer — and says the row couldn't
    assert any("none of Code change's choices" in r for r in c.rejected)
    c = router(spent=("claude", "codex")).choose(need("code", difficulty="easy"))
    assert c.backend.key == "codex-qwen" and "3rd choice" in c.reason


def test_a_row_with_nothing_available_falls_back_to_the_old_way_and_says_so():
    c = router(spent=("claude", "codex")).choose(Need(tools=True, task="repo", difficulty="hard", row="code_hard",
                                                      row_title="Big or hard code change", targets=BASE["code_hard"]))
    assert c.backend.key == "codex-qwen" and any("none of Big or hard code change" in r for r in c.rejected)


# ---- capacity -----------------------------------------------------------------------------------

def W(key, used, resets, label="WEEK", primary=True):
    return NS(key=key, label=label, used=used, resets_at=resets, kind="window", primary=primary)


def test_what_a_request_costs_is_learned_from_the_window_moving():
    data = {}
    r = time.time() + 5 * 86400
    capacity.observe(data, "claude", [W("seven_day", 0.10, r)], runs=10)
    capacity.observe(data, "claude", [W("seven_day", 0.12, r)], runs=14)       # 4 requests, 2%
    capacity.observe(data, "claude", [W("seven_day", 0.15, r)], runs=20)       # 6 requests, 3%
    cost = data["claude"]["seven_day"]["cost"]
    assert 0.004 < cost < 0.006 and data["claude"]["seven_day"]["n"] == 2
    room = capacity.room(data, "claude", [W("seven_day", 0.15, r)])
    assert 150 < room["left"] < 220 and room["per_hour"] > 1


def test_a_reset_is_not_a_measurement_and_usage_without_requests_waits():
    data = {}
    capacity.observe(data, "codex", [W("month", 0.50, 100)], runs=5)
    capacity.observe(data, "codex", [W("month", 0.02, 200)], runs=6)           # reset
    assert data["codex"]["month"]["cost"] is None
    capacity.observe(data, "codex", [W("month", 0.05, 200)], runs=6)           # used elsewhere, no run of ours
    capacity.observe(data, "codex", [W("month", 0.09, 200)], runs=8)           # now 2 runs: 7% over 2
    assert abs(data["codex"]["month"]["cost"] - 0.035) < 1e-9


def test_a_bigger_plan_has_more_room_for_the_same_percentage():
    r = time.time() + 86400
    small = {"p": {"week": {"cost": 0.02, "n": 5}}}
    big = {"p": {"week": {"cost": 0.002, "n": 5}}}
    same = [W("week", 0.5, r)]
    assert capacity.room(big, "p", same)["left"] > 9 * capacity.room(small, "p", same)["left"]


# ---- talking to eki about routing ------------------------------------------------------------------

NAMES = ["claude", "codex", "local", "opus", "fable", "api", "api keys"]


@pytest.mark.parametrize("text,mode", [
    ("use Claude for code", "edit"), ("only use Codex if Claude runs out", "edit"),
    ("never use API keys for anything", "edit"), ("for this thread use Codex for everything", "edit"),
    ("things like this are writing", "edit"), ("why did that go to Codex?", "why"),
    ("/routing", "show"), ("show me the routing table", "show"), ("forget that rule", "edit"),
    ("use Claude's API in this script to summarise the logs", ""),
    ("fix the failing test", ""), ("never use global variables in this code", ""),
])
def test_what_is_for_eki_and_what_is_for_a_model(text, mode):
    assert prefs.addressed(text, NAMES) == mode


def test_the_plain_forms_are_read_without_a_model():
    words = {"claude_code": ["claude"], "codex": ["codex", "gpt"], "anthropic": ["api", "api keys"]}
    assert prefs.plain("never use API keys", words) == {"action": "never", "target": "anthropic"}
    assert prefs.plain("only use Codex if Claude runs out", words) == {"action": "backup", "target": "codex"}
    assert prefs.plain("for this thread use Codex", words)["action"] == "thread"
    assert prefs.plain("use Claude for code", words) is None               # a model reads that one
    assert prefs.plain("forget the rule for this thread", words) == {"action": "forget", "forget": "thread"}


# ---- in the engine ---------------------------------------------------------------------------------

@register("desk")
class Desk(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        yield f"answered by {self.info.key}"


def desk(tmp_path, monkeypatch):
    from eki.engine import Engine
    from tests.test_runs import echo_config
    cfg = echo_config(tmp_path)
    cfg.backends = []
    cfg.options = {}
    for key, kind, tier in (("claude_code", "claude_code", 50), ("codex", "codex", 50)):
        cfg.backends.append(BackendInfo(key=key, kind=kind, label={"claude_code": "Claude Code"}.get(key, "Codex"),
                                        capabilities=Capabilities(tools=True, repo=True, web=True),
                                        cost=Cost(tier=tier)))
        cfg.options[key] = {"binary": "/bin/echo"}
    eng = Engine(cfg, owner=True)
    eng.settings = {**eng.settings, "skills_learn": "off", "notify_learned": False, "worktrees": False}
    # the adapters are stand-ins: answer, don't run a CLI
    from eki.adapters import base as adapters
    real = adapters.build
    monkeypatch.setattr(adapters, "build", lambda info, opts: Desk(info, opts) if info.kind in ("claude_code", "codex")
                        else real(info, opts))
    monkeypatch.setattr(eng, "_lives", lambda b: False)
    return eng


async def ask(eng, prompt, cid="", backend_key=""):
    from tests.test_runs import settle
    s = await eng.ask(prompt, conversation=cid, backend_key=backend_key)
    await settle(eng.runs, s["run"], timeout=5)
    return s["conversation"], eng.store.turns(s["conversation"])[-1]


@pytest.mark.asyncio
async def test_saying_it_changes_the_table_and_eki_answers_itself(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    cid, turn = await ask(eng, "only use Codex if Claude runs out")
    assert turn["backend"] == "eki" and "Codex" in turn["content"] or "codex" in turn["content"]
    assert table.load()["backup"][0]["target"] == "codex"
    _, turn = await ask(eng, "explain how a heat pump works in some detail please", cid)
    assert turn["backend"] == "claude_code"
    _, turn = await ask(eng, "why did that go to Claude?", cid)
    assert turn["backend"] == "eki" and "Explain, reason" in turn["content"]
    _, turn = await ask(eng, "/routing", cid)
    assert "| Kind of work |" in turn["content"] and "only as a backup: codex" in turn["content"]
    _, turn = await ask(eng, "forget that rule about codex, the routing one", cid)
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_model_reads_the_free_forms(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)

    async def read(reader, prompt):
        assert "use Codex for code" in prompt and "codex@" in prompt or "codex" in prompt
        return {"action": "first", "row": "code", "target": "codex", "without": ["claude_code"],
                "reply": "Code goes to Codex first now."}
    monkeypatch.setattr(eng, "_read", read)
    monkeypatch.setattr(eng, "_watch_reader", lambda: object())
    cid, turn = await ask(eng, "use Codex for code")
    assert turn["backend"] == "eki" and "Code goes to Codex first" in turn["content"]
    cell = eng.routing_table()["code"]
    assert cell["targets"][0].startswith("codex") and cell["source"] == "you"
    assert not any(t.startswith("claude_code") for t in cell["targets"])          # "…, not Claude"
    exp = await eng.routing_explain("fix the failing test in this repo", folder=str(tmp_path))
    assert exp["row"] == "code" and exp["choice"].startswith("codex") and "1st choice" in exp["reason"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_picking_by_name_in_three_threads_is_learned(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    for i in range(3):
        await ask(eng, "explain how a heat pump works in some detail please", backend_key="codex")
    cell = eng.routing_table()["explain"]
    assert cell["targets"][0] == "codex" and cell["source"] == "learned"
    view = eng.routing_text()
    assert "learned" in view
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_replay_shows_where_recent_requests_would_go(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch)
    await ask(eng, "explain how a heat pump works in some detail please")
    rows = await eng.routing_replay(10)
    assert rows and rows[0]["then"] == rows[0]["now"].split(" ")[0] and not rows[0]["differs"]
    await eng.runner.stop()


def test_putting_a_provider_first_never_says_it_twice_and_keeps_its_roles():
    row = ["claude_code@top", "claude_code@default", "codex@default"]
    assert table.to_front(row, "codex") == ["codex@default", "claude_code@top", "claude_code@default"]
    assert table.to_front(row, "claude_code") == row
    assert table.to_front(["codex-qwen", "claude_code@fast"], "claude_code@default") == \
        ["claude_code@default", "claude_code@fast", "codex-qwen"]
    rules = {"threads": {"c1": {"targets": ["claude_code"], "until": None, "said": "use Claude"}}}
    eff = table.effective(BASE, rules, "c1")
    assert eff["quick"]["targets"] == ["claude_code@fast", "codex-qwen", "codex@fast"]
    assert eff["retry"]["targets"][:2] == ["claude_code@top", "claude_code@default"]
