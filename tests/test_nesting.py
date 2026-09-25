# SPDX-License-Identifier: Apache-2.0
"""The depth guard: an agent asking eki asking an agent stops somewhere.

Before this, `EKI_DEPTH` was read but never set in the programs eki started,
so every nested call arrived at depth 1 and the guard could never trip; and
`eki ask` from an agent's shell wasn't guarded at all.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from eki import mcpbridge, nesting
from eki.cli import ask as cli
from eki.engine import Engine
from eki.runs import Runner, RunStore

from test_runs import echo_config, settle


@pytest.fixture(autouse=True)
def private_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("EKI_POLICY", str(tmp_path / "policy.json"))
    monkeypatch.delenv("EKI_DEPTH", raising=False)
    monkeypatch.delenv("EKI_RUN", raising=False)


@pytest.mark.asyncio
async def test_a_program_started_for_a_run_is_one_level_deeper_and_names_the_run(tmp_path):
    store = RunStore(tmp_path / "runs.db", owner=True)
    seen = {}

    async def dispatch(run):
        seen[run["id"]] = nesting.child_env()
        yield "ok"

    runner = Runner(store, dispatch)
    yours = store.create("hi")
    nested = store.create("hi", payload=json.dumps({"depth": 2}))
    await runner.submit(yours)
    await runner.submit(nested)
    await settle(store, yours)
    await settle(store, nested)
    # each run is its own task: one's depth doesn't leak into the other's
    assert seen[yours]["EKI_DEPTH"] == "1" and seen[yours]["EKI_RUN"] == yours
    assert seen[nested]["EKI_DEPTH"] == "3" and seen[nested]["EKI_RUN"] == nested
    assert nesting.current() == (0, "")                       # outside a run: yours
    await runner.stop()
    store.close()


@pytest.mark.asyncio
async def test_the_engine_refuses_at_the_limit_and_a_nested_run_takes_its_parents_budget(tmp_path):
    eng = Engine(echo_config(tmp_path), owner=True)
    with pytest.raises(nesting.TooDeep):
        await eng.ask("too deep", depth=nesting.MAX_DEPTH, via="agent")
    # a goal's turn, allowed only the local line…
    goal_turn = eng.runs.create("goal turn", payload=json.dumps({"goal": "g1", "allowed": ["echo"]}))
    started = await eng.ask("a step", depth=1, parent_run=goal_turn, via="agent")
    payload = json.loads(eng.runs.get(started["run"])["payload"])
    assert payload["depth"] == 1 and payload["parent"] == goal_turn
    assert payload["allowed"] == ["echo"]                       # …and so is what its agent asks
    assert "goal" not in payload                                # a step, not a turn of the goal
    await settle(eng.runs, started["run"])
    # yours carries none of it
    mine = await eng.ask("ping")
    assert not eng.runs.get(mine["run"])["payload"]
    await settle(eng.runs, mine["run"])
    await eng.runner.stop()


def test_the_bridge_refuses_pictures_too_and_passes_its_depth_on():
    asked = []

    class Fake:
        async def ask(self, prompt, **kw):
            asked.append(kw)
            raise RuntimeError("stop here")

    async def go():
        deep = mcpbridge.Bridge(Fake(), "", depth=nesting.MAX_DEPTH)
        said = await deep.call("eki_image", {"prompt": "a cat"})
        assert "refused" in json.dumps(said)
        shallow = mcpbridge.Bridge(Fake(), "", depth=1, parent="r1")
        await shallow.call("eki_ask", {"prompt": "hi"})
        assert asked and asked[0]["depth"] == 1 and asked[0]["parent_run"] == "r1"
    asyncio.run(go())


def test_eki_ask_from_an_agents_shell_says_how_deep_it_is(monkeypatch, tmp_path):
    sent = {}

    def call(method, path, service, json=None, **kw):
        sent.update(json or {})
        return {"run": "r2", "conversation": "c2"}

    monkeypatch.setattr(cli, "call", call)
    args = type("A", (), {"continue_": False, "prompt": "hi", "backend": "", "repo": "",
                          "image": False, "service": "http://x", "detach": True})()
    cli.cmd_ask(None, args)
    assert "depth" not in sent                                  # a person's shell
    monkeypatch.setenv("EKI_DEPTH", "2")
    monkeypatch.setenv("EKI_RUN", "r1")
    cli.cmd_ask(None, args)
    assert sent["depth"] == 2 and sent["parent_run"] == "r1"
