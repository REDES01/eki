# SPDX-License-Identifier: Apache-2.0
"""A model with no tools asks for a picture mid-answer; the image model
draws it and the answer carries on after it."""
import asyncio
import json

import pytest

from eki import illustrate, watch
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health, ToolCall
from tests.test_runs import echo_config, settle


# ---- the marker ------------------------------------------------------------------------

async def collect(chunks, drew=None):
    drew = [] if drew is None else drew

    async def gen():
        for c in chunks:
            yield c

    async def draw(words):
        drew.append(words)
        return f"![{words}](/tmp/p{len(drew)}.png)"
    return [c async for c in illustrate.watch(gen(), draw)]


def test_a_marker_split_across_chunks_becomes_the_picture_in_place():
    drew = []
    out = asyncio.run(collect(["Here is the fox:\n[[ima", "ge: a red fox ", "in snow]]\nIt waits."], drew))
    assert drew == ["a red fox in snow"]
    assert "".join(out) == "Here is the fox:\n![a red fox in snow](/tmp/p1.png)\nIt waits."


def test_text_without_a_marker_passes_through_and_lookalikes_stay():
    text = ["See [[wiki links]] and [[im", "portant]] — plus [image: not one]"]
    assert "".join(asyncio.run(collect(text))) == "See [[wiki links]] and [[important]] — plus [image: not one]"


def test_markers_in_thinking_are_not_drawn_and_there_is_a_limit():
    drew = []
    many = "".join(f"[[image: p{i}]]" for i in range(illustrate.MOST + 2))
    out = asyncio.run(collect(["<think>maybe [[image: x]]</think>", many], drew))
    assert drew == [f"p{i}" for i in range(illustrate.MOST)]
    assert "[[image: x]]" in "".join(out) and "p5" not in "".join(out)


def test_an_unclosed_marker_at_the_end_is_still_drawn_and_other_chunks_pass():
    drew = []
    call = ToolCall("something", {})
    out = asyncio.run(collect(["Look: ", call, "[[image: a lighthouse"], drew))
    assert drew == ["a lighthouse"] and call in out and "[[image" not in "".join(o for o in out if isinstance(o, str))


# ---- in a thread -------------------------------------------------------------------------

class Local(Backend):
    seen = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Local.seen.append(messages)
        yield "A lighthouse:\n[[image: a lighthouse at dusk]]\nThe end."


class Painter(Backend):
    PRODUCES = ("image",)
    asked = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Painter.asked.append(kw.get("prompt") or messages[-1].content)
        yield "![a lighthouse at dusk](/tmp/eki-lighthouse.png)"


@pytest.fixture
def eng(tmp_path, monkeypatch):
    from eki.engine import Engine
    from eki.adapters import base as adapters
    cfg = echo_config(tmp_path)
    cfg.backends, cfg.options = [], {}
    cfg.backends.append(BackendInfo(key="qwen", kind="mlx", label="Qwen (local)",
                                    capabilities=Capabilities(), cost=Cost(tier=0)))
    cfg.backends.append(BackendInfo(key="comfy", kind="comfyui", label="ComfyUI",
                                    capabilities=Capabilities(text=False, images_out=True),
                                    cost=Cost(tier=0)))
    watch.save({"vendors": {}})
    monkeypatch.setattr(adapters, "build",
                        lambda info, opts: (Painter if info.kind == "comfyui" else Local)(info, opts))
    e = Engine(cfg, owner=True)
    e.settings = {**e.settings, "skills_learn": "off", "notify_learned": False, "worktrees": False,
                  "skills_local": False}
    monkeypatch.setattr(e, "_lives", lambda b: False)
    e.router.is_up = lambda k: True
    Local.seen, Painter.asked = [], []
    return e


@pytest.mark.asyncio
async def test_a_local_model_asks_for_a_picture_and_it_lands_in_its_answer(eng):
    s = await eng.ask("write a short poem about a lighthouse, with a picture", conversation="")
    run = await settle(eng.runs, s["run"], timeout=10)
    assert run["backend"] == "qwen"
    assert any("[[image:" in m.content for m in Local.seen[0] if m.role == "system")
    assert Painter.asked == ["a lighthouse at dusk"]
    turn = eng.store.turns(s["conversation"])[-1]
    assert turn["content"] == "A lighthouse:\n![a lighthouse at dusk](/tmp/eki-lighthouse.png)\nThe end."
    # the picture was its own run, under this one, in a thread of its own
    child = next(r for r in eng.runs.recent() if r["id"] != run["id"] and r["backend"] == "comfy")
    assert child["conversation_id"] != s["conversation"]
    assert json.loads(eng.runs.get(child["id"])["payload"] or "{}").get("via") == "agent"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_with_the_setting_off_nothing_is_offered(eng):
    eng.settings = {**eng.settings, "pictures_in_answers": False}
    s = await eng.ask("write a short poem about a lighthouse", conversation="")
    await settle(eng.runs, s["run"], timeout=10)
    assert not any("[[image:" in m.content for m in Local.seen[0] if m.role == "system")
    assert Painter.asked == []
    await eng.runner.stop()
