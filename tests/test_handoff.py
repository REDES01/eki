# SPDX-License-Identifier: Apache-2.0
"""A thread stays with the model that answers; a model with no tools hands it
over when it must — with a brief, so the harness starts from what was said."""
import asyncio
import json

import pytest

from eki import handoff, watch
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from tests.test_runs import echo_config, settle


# ---- the marker ------------------------------------------------------------------------

async def collect(chunks):
    async def gen():
        for c in chunks:
            yield c
    return [c async for c in handoff.watch(gen())]


def test_an_ordinary_answer_passes_through_whole():
    assert "".join(asyncio.run(collect(["Gray sky", " over Ginza"]))) == "Gray sky over Ginza"
    assert "".join(asyncio.run(collect(["[", "citation needed]"]))) == "[citation needed]"


def test_the_marker_is_caught_even_split_across_chunks():
    with pytest.raises(handoff.HandOff) as got:
        asyncio.run(collect(["[[hand", "off: claude_code | They want an RPG", " built around the haiku.]]"]))
    assert got.value.target == "claude_code" and got.value.brief == "They want an RPG built around the haiku."


def test_thinking_goes_by_and_the_marker_after_it_still_counts():
    with pytest.raises(handoff.HandOff):
        asyncio.run(collect(["<think>it needs files", "</think>", "[[handoff: codex | fix the tests]]"]))
    out = asyncio.run(collect(["<think>easy</think>", "Snow on the rails"]))
    assert "".join(out) == "<think>easy</think>Snow on the rails"


def test_a_marker_without_a_name_lets_eki_pick():
    h = handoff.parse("[[handoff: | needs the web]]")
    assert h.target == "" and h.brief == "needs the web"


def test_the_instructions_say_what_it_cant_do_and_who_can():
    text = handoff.instructions([("claude_code", "Claude Code — files, commands")])
    assert "cannot read or change files" in text and "- claude_code: Claude Code" in text
    assert "[[handoff:" in text
    as_tool = handoff.instructions([("claude_code", "Claude Code — files, commands")], as_tool=True)
    assert "call hand_off" in as_tool and "[[handoff:" not in as_tool


def test_a_sentence_before_the_marker_no_longer_hides_it():
    # what Qwen answered to the dancing-boys goal three times, 2026-09-24
    said = ["I'll inspect the existing project structure first, then build the animation.\n\n",
            "[[handoff: claude_code | Recurring background goal: build a 15-second JS ",
            "animation. Write a new .md first.]]"]
    out = []

    async def run():
        async def gen():
            for c in said:
                yield c
        async for c in handoff.watch(gen()):
            out.append(c)
    with pytest.raises(handoff.HandOff) as got:
        asyncio.run(run())
    assert got.value.target == "claude_code" and got.value.brief.startswith("Recurring background goal")
    assert "[[" not in "".join(out) and "".join(out).startswith("I'll inspect")


def test_brackets_that_arent_a_marker_pass_through_whole():
    text = ["See [[wiki links]] and [[hand", "le it]] — plus ", "[[handoff:", " this is prose"]
    assert "".join(asyncio.run(collect(text))) == "See [[wiki links]] and [[handle it]] — plus [[handoff: this is prose"


def test_the_hand_off_tool_call_hands_over_even_after_words():
    from eki.adapters.base import ToolCall
    call = ToolCall("hand_off", {"target": "claude_code", "brief": "They want  free disk space."})
    with pytest.raises(handoff.HandOff) as got:
        asyncio.run(collect(["I can't check that myself.", call]))
    assert got.value.target == "claude_code" and got.value.brief == "They want free disk space."
    other = ToolCall("something_else", {})
    assert asyncio.run(collect(["hi", other]))[-1] is other


def test_the_tool_offers_only_who_can_take_it():
    t = handoff.tool([("claude_code", "Claude Code"), ("codex", "Codex")])
    assert t["function"]["name"] == "hand_off"
    assert t["function"]["parameters"]["properties"]["target"]["enum"] == ["claude_code", "codex"]


def test_the_mlx_adapter_sends_tools_and_yields_the_whole_call(monkeypatch):
    import httpx
    from eki.adapters.base import ToolCall
    from eki.adapters.mlx import MLXBackend
    frames = [
        {"choices": [{"delta": {"content": "Handing over."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "hand_off",
                                                                         "arguments": '{"target": "clau'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'de_code", "brief": "disk"}'}}]}}]},
    ]
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    b = MLXBackend(BackendInfo(key="qwen", kind="mlx", label="Qwen"), {})

    async def run():
        return [c async for c in b.stream([], tools=[handoff.tool([("claude_code", "Claude Code")])])]
    out = asyncio.run(run())
    assert sent["tools"][0]["function"]["name"] == "hand_off"
    assert out[0] == "Handing over."
    assert out[-1] == ToolCall("hand_off", {"target": "claude_code", "brief": "disk"}, "")


# ---- in a thread -------------------------------------------------------------------------

class Local(Backend):
    """A local model: writes the haiku, rewrites it, hands the game over."""
    seen = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Local.seen.append(messages)
        ask = messages[-1].content.lower()
        if "game" in ask:
            yield "[[handoff: claude_code | They want a small RPG built around the snow haiku.]]"
        elif "snow" in ask:
            yield "Snow on the rails / …"
        else:
            yield "Rain on the rails / …"


class Harness(Backend):
    seen = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Harness.seen.append(messages[-1].content)
        yield "Building it."


@pytest.fixture
def eng(tmp_path, monkeypatch):
    from eki.engine import Engine
    from eki.adapters import base as adapters
    cfg = echo_config(tmp_path)
    cfg.backends, cfg.options = [], {}
    cfg.backends.append(BackendInfo(key="claude_code", kind="claude_code", label="Claude Code",
                                    capabilities=Capabilities(tools=True, repo=True, web=True), cost=Cost(tier=50)))
    cfg.options["claude_code"] = {"binary": "/bin/echo"}
    cfg.backends.append(BackendInfo(key="qwen", kind="mlx", label="Qwen 27B (local)",
                                    capabilities=Capabilities(), cost=Cost(tier=0)))
    cfg.options["qwen"] = {"model": "mlx-community/Qwen3.8-27B-4bit"}
    watch.save({"vendors": {"claude_code": {"vendor": "Anthropic",
                                            "ladder": {"default": "opus", "top": "fable", "fast": "haiku"}}}})
    monkeypatch.setattr(adapters, "build", lambda info, opts: (Harness if info.kind == "claude_code" else Local)(info, opts))
    e = Engine(cfg, owner=True)
    e.settings = {**e.settings, "skills_learn": "off", "notify_learned": False, "worktrees": False,
                  "skills_local": False}
    monkeypatch.setattr(e, "_lives", lambda b: False)
    e.router.is_up = lambda k: True
    Local.seen, Harness.seen = [], []
    return e


async def say(e, prompt, cid=""):
    s = await e.ask(prompt, conversation=cid)
    run = await settle(e.runs, s["run"], timeout=5)
    return s["conversation"], run, e.store.turns(s["conversation"])[-1]


@pytest.mark.asyncio
async def test_the_thread_stays_local_until_the_model_hands_it_over(eng):
    cid, run, turn = await say(eng, "write a haiku about rain")
    assert run["backend"] == "qwen"
    assert "cannot read or change files" in Local.seen[0][0].content       # it knows it can hand over
    _, run, turn = await say(eng, "no, make it about snow instead", cid)
    assert run["backend"] == "qwen" and turn["content"].startswith("Snow")   # stayed, with the context
    assert "stays with qwen" in run["reason"] or "qwen" in run["reason"]
    _, run, turn = await say(eng, "now make a game around it", cid)
    assert run["backend"] == "claude_code" and "(opus)" in run["reason"]
    assert "Qwen 27B (local) handed this over: They want a small RPG" in turn["content"]
    joined = Harness.seen[-1]
    assert "joining a conversation" in joined and "Snow on the rails" in joined     # it has the thread
    assert "They want a small RPG" in joined and "now make a game" in joined
    assert json.loads(turn["meta"])["handoff"]["from"] == "qwen"
    # and then it stays with Claude Code
    eng.store.set_session(cid, "claude_code", "sess-1")     # what Claude Code does on its first turn
    _, run, _ = await say(eng, "make the hero a fox", cid)
    assert run["backend"] == "claude_code" and "(opus)" in run["reason"]
    assert not Harness.seen[-1].startswith("[eki: you're joining")                  # it remembers now
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_server_that_takes_tools_is_given_hand_off_and_its_call_moves_the_thread(eng, monkeypatch):
    from eki.adapters.base import ToolCall
    offered = []

    class Calling(Local):
        accepts_tools = True

        async def stream(self, messages, **kw):
            offered.append((messages[0].content, kw.get("tools")))
            if "game" in messages[-1].content.lower():
                yield "I'll look at the project first."            # words, then the call
                yield ToolCall("hand_off", {"target": "claude_code", "brief": "They want an RPG."})
            else:
                yield "Rain on the rails / …"
    from eki.adapters import base as adapters
    monkeypatch.setattr(adapters, "build", lambda info, opts: (Harness if info.kind == "claude_code" else Calling)(info, opts))
    cid, run, _ = await say(eng, "write a haiku about rain")
    assert run["backend"] == "qwen"
    system, tools = offered[0]
    assert "call hand_off" in system and tools[0]["function"]["name"] == "hand_off"
    _, run, turn = await say(eng, "now make a game around it", cid)
    assert run["backend"] == "claude_code"
    assert "handed this over: They want an RPG." in turn["content"]
    assert json.loads(turn["meta"])["handoff"] == {"from": "qwen", "to": "claude_code", "brief": "They want an RPG."}
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_picking_another_or_a_failure_moves_the_thread(eng):
    cid, run, _ = await say(eng, "write a haiku about rain")
    assert run["backend"] == "qwen"
    s = await eng.ask("and one about snow", conversation=cid, backend_key="claude_code")
    run = await settle(eng.runs, s["run"], timeout=5)
    assert run["backend"] == "claude_code"
    _, run, _ = await say(eng, "and one about wind", cid)
    assert run["backend"] == "claude_code"                                   # stays where you moved it
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_explain_says_it_stays(eng):
    cid, _, _ = await say(eng, "write a haiku about rain")
    exp = await eng.routing_explain("no, make it about snow instead", thread=cid)
    assert exp["choice"] == "qwen" and "stays with qwen" in exp["row_why"]
    await eng.runner.stop()
