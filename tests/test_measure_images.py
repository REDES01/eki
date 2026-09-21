# SPDX-License-Identifier: Apache-2.0
"""An image model is scored by a model that can see, on facts, not taste."""
import asyncio

import pytest

from eki import measure_images
from eki.adapters.base import Backend, BackendError, BackendInfo, Capabilities, Health, Message


class Painter(Backend):
    def __init__(self, tmp_path, fail_on=""):
        super().__init__(BackendInfo(key="p", kind="comfyui", label="P"), {})
        self.tmp, self.fail_on, self.asked = tmp_path, fail_on, []

    async def health(self):
        return Health(True, "")

    async def stream(self, messages, **kw):
        prompt = messages[-1].content
        self.asked.append(prompt)
        if self.fail_on and self.fail_on in prompt:
            raise BackendError("ComfyUI rejected the graph")
        f = self.tmp / f"{len(self.asked)}.png"
        f.write_bytes(b"\x89PNG")
        yield f"P · 1024×1024 · seed 1\n\n![{prompt[:10]}]({f})\n"


class Seer(Backend):
    def __init__(self, answers):
        super().__init__(BackendInfo(key="claude_code", kind="claude_code", label="Claude Code",
                                     capabilities=Capabilities(vision=True)), {})
        self.answers, self.prompts = answers, []

    async def health(self):
        return Health(True, "")

    async def stream(self, messages, **kw):
        self.prompts.append(messages[-1].content)
        yield self.answers.pop(0)


def _collect(gen):
    async def go():
        out = []
        async for piece in gen:
            out.append(piece)
        return out
    return asyncio.run(go())


def test_facts_are_asked_and_the_share_that_hold_is_the_score(tmp_path):
    items = measure_images.ITEMS[:3]
    seer = Seer(["[true, true, true]", "Here you go: [true, false, true, true]", "[false, false, false, false]"])
    painter = Painter(tmp_path)
    got = _collect(measure_images.run(painter, lambda: seer, items))
    assert painter.asked == [it["prompt"] for it in items]
    assert str(tmp_path / "1.png") in seer.prompts[0] and "1. There is a bicycle" in seer.prompts[0]
    lines = [p for p in got if isinstance(p, str)]
    assert lines[0].startswith("  ✓") and "missed: There are exactly three apples" in lines[1]
    final = got[-1]
    assert final == {"slot": "image/medium", "score": round(6 / 11, 3), "n": 3}


def test_a_failed_draw_counts_against_the_model_and_no_judge_stops_it(tmp_path):
    items = measure_images.ITEMS[:2]
    seer = Seer(["[true, true, true, true]"])
    got = _collect(measure_images.run(Painter(tmp_path, fail_on="bicycle"), lambda: seer, items))
    assert any("✗" in p and "rejected" in p for p in got if isinstance(p, str))
    assert got[-1]["score"] == round(4 / 7, 3) and got[-1]["n"] == 2
    with pytest.raises(BackendError, match="vision"):
        _collect(measure_images.run(Painter(tmp_path), lambda: None, items))


def test_a_judge_that_wont_answer_in_booleans_is_skipped(tmp_path):
    seer = Seer(["I cannot see the image.", "[true, true, true]"])
    got = _collect(measure_images.run(Painter(tmp_path), lambda: seer, measure_images.ITEMS[:2]))
    assert any("judge:" in p for p in got if isinstance(p, str))
    assert got[-1]["n"] == 1                                    # only the judged one counts


def test_every_item_has_checkable_facts():
    for it in measure_images.ITEMS:
        assert 3 <= len(it["facts"]) <= 5 and it["prompt"]
    assert len(measure_images.ITEMS) >= 10                      # enough to count as solid
