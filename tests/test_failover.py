# SPDX-License-Identifier: Apache-2.0
"""A subscription that runs out mid-run: the next choice carries on, told what
was done, in the same copy of the folder — and the thread says so."""
import json
from pathlib import Path

import pytest

from eki import failover, watch
from eki.adapters.base import Backend, BackendError, BackendInfo, Capabilities, Cost, Health
from tests.test_runs import echo_config, settle
from tests.test_workspace import repo  # noqa: F401  (fixture)


# ---- what counts as a limit ----------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "Claude AI usage limit reached|1790000000",
    "You've hit your usage limit for GPT-5.6. Try again in 3 days.",
    "You have reached your weekly usage limit",
    "API Error: 429 Too Many Requests",
    "Credit balance is too low",
])
def test_the_words_every_provider_uses_count(said):
    assert failover.is_limit(said)


@pytest.mark.parametrize("said", ["SyntaxError in app.py", "Claude Code stopped: exit 1", "timed out"])
def test_other_failures_dont(said):
    assert not failover.is_limit(said)


def test_claude_codes_own_rejected_event():
    assert failover.rejected({"status": "rejected", "rateLimitType": "seven_day"}).startswith(
        "usage limit reached (seven day")
    assert failover.rejected({"status": "allowed_warning"}) == ""
    assert failover.rejected({"status": "rejected", "overageStatus": "allowed"}) == ""   # paying on


def test_the_brief_carries_what_was_done():
    text = failover.brief("Claude Code", "usage limit reached", "Made rpg.py with the map.")
    assert "stopped at its usage limit" in text and "Made rpg.py with the map." in text
    assert "already there" in text


# ---- in a run ----------------------------------------------------------------------------

class Claude(Backend):
    seen = []
    say = "Claude AI usage limit reached|1790000000"

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Claude.seen.append(messages[-1].content)
        if kw.get("cwd"):
            Path(kw["cwd"], "map.py").write_text("MAP = 1\n")
        yield "Started the game: map.py has the map."
        raise BackendError(Claude.say)


class Codex(Backend):
    seen = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Codex.seen.append(messages[-1].content)
        if kw.get("cwd"):
            Path(kw["cwd"], "hero.py").write_text("HERO = 'fox'\n")
        yield "Added the hero; done."


def desk(tmp_path, monkeypatch, **settings):
    from eki.engine import Engine
    from eki.adapters import base as adapters
    cfg = echo_config(tmp_path)
    cfg.backends, cfg.options = [], {}
    cfg.backends.append(BackendInfo(key="claude_code", kind="claude_code", label="Claude Code",
                                    capabilities=Capabilities(tools=True, repo=True, web=True),
                                    cost=Cost(tier=50), quota_source="claude"))
    cfg.options["claude_code"] = {"binary": "/bin/echo"}
    cfg.backends.append(BackendInfo(key="codex", kind="codex", label="Codex",
                                    capabilities=Capabilities(tools=True, repo=True, web=True),
                                    cost=Cost(tier=50), quota_source="codex"))
    cfg.options["codex"] = {"binary": "/bin/echo"}
    watch.save({"vendors": {"claude_code": {"vendor": "Anthropic", "ladder": {"default": "opus"}}}})
    monkeypatch.setattr(adapters, "build",
                        lambda info, opts: (Claude if info.kind == "claude_code" else Codex)(info, opts))
    e = Engine(cfg, owner=True)
    e.settings = {**e.settings, "skills_learn": "off", "notify_learned": False, "skills_local": False,
                  **settings}
    monkeypatch.setattr(e, "_lives", lambda b: False)
    e.router.is_up = lambda k: True
    Claude.seen, Codex.seen = [], []
    Claude.say = "Claude AI usage limit reached|1790000000"
    return e


@pytest.mark.asyncio
async def test_a_limit_mid_run_carries_on_in_the_next_choice(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch, worktrees=False)
    s = await eng.ask("build a small rpg game in python with a map and a hero")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert run["state"] == "done" and run["backend"] == "codex"
    assert Claude.seen                                                     # it started there
    told = Codex.seen[-1]
    assert "stopped at its usage limit" in told and "map.py has the map" in told
    assert "build a small rpg game" in told
    turn = eng.store.turns(s["conversation"])[-1]
    assert turn["content"].startswith("Started the game")
    assert "Claude Code hit its usage limit" in turn["content"] and "Codex carries on" in turn["content"]
    assert turn["content"].endswith("Added the hero; done.")
    assert json.loads(turn["meta"])["failover"]["from"] == "claude_code"
    assert "claude" in eng.quota.exhausted()                               # the next run knows
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_next_one_works_in_the_same_copy_and_both_land(tmp_path, monkeypatch, repo):  # noqa: F811
    eng = desk(tmp_path, monkeypatch)
    s = await eng.ask("add a hero and a map to the game", repo=str(repo))
    run = await settle(eng.runs, s["run"], timeout=15)
    assert run["state"] == "done" and run["backend"] == "codex"
    assert (repo / "map.py").read_text() == "MAP = 1\n"                    # Claude's part
    assert (repo / "hero.py").exists()                                     # Codex's part
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_any_other_failure_still_fails(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch, worktrees=False)
    Claude.say = "SyntaxError in rpg.py"
    s = await eng.ask("build a small rpg game in python with a map and a hero")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert run["state"] == "failed" and not Codex.seen
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_model_you_picked_reports_its_limit(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch, worktrees=False)
    s = await eng.ask("build a small rpg game in python", backend_key="claude_code")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert run["state"] == "failed" and not Codex.seen
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_off_means_off(tmp_path, monkeypatch):
    eng = desk(tmp_path, monkeypatch, worktrees=False, failover=False)
    s = await eng.ask("build a small rpg game in python with a map and a hero")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert run["state"] == "failed" and not Codex.seen
    await eng.runner.stop()
