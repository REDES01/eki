# SPDX-License-Identifier: Apache-2.0
"""How big and how many, read off the words — and only the words that say so."""
from pathlib import Path

import pytest

from eki import classify
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health, register
from eki.engine import Engine
from eki.imagespec import read
from tests.test_runs import echo_config, settle


@register("easel")
class Easel(Backend):
    """Draws nothing, but says what it was handed. Its own kind, registered
    here: a class borrowed from another test file is a twin of the one the
    engine builds once pytest has imported that file under its other name."""

    calls = []
    out = ""

    async def health(self):
        return Health(True, "easel")

    async def stream(self, messages, **kw):
        Easel.calls.append(dict(kw))
        path = Path(Easel.out) / f"pic{len(Easel.calls)}.png"
        path.write_bytes(b"png")
        yield f"\n![pic]({path})\n"


def studio(tmp_path) -> Engine:
    cfg = echo_config(tmp_path)
    cfg.backends.append(BackendInfo(
        key="flux", kind="easel", label="flux",
        capabilities=Capabilities(text=False, images_out=True), cost=Cost(tier=0)))
    cfg.options["flux"] = {}
    Easel.calls, Easel.out = [], str(tmp_path)
    return Engine(cfg, owner=True)


async def say(eng, prompt, cid=""):
    started = await eng.ask(prompt, conversation=cid)
    await settle(eng.runs, started["run"])
    return started["conversation"], eng.store.turns(started["conversation"])[-1]


def test_a_size_and_a_count_come_out_of_the_request():
    s = read("draw 4 images of a red fox at 1536x1024")
    assert s.as_dict() == {"width": 1536, "height": 1024, "batch": 4}
    # the model is asked for the one fox; four of them is eki's to honour
    assert s.prompt == "draw an image of a red fox"


@pytest.mark.parametrize("said, paper, left", [
    ("four pictures of a lighthouse, 16:9", {"aspect": 1.7778, "batch": 4}, "a picture of a lighthouse"),
    ("a mountain at dawn in landscape format, 2048 x 1152 px", {"width": 2048, "height": 1152}, "a mountain at dawn"),
    ("generate an image of a cat in portrait orientation", {"aspect": 0.75}, "generate an image of a cat"),
    ("a robot in the rain, 3 variations please", {"batch": 3}, "a robot in the rain"),
    ("a robot, x4", {"batch": 4}, "a robot"),
    ("give me a batch of 6 logos for a coffee shop", {"batch": 6}, "give me a logo for a coffee shop"),
    ("make me 2 different versions of a neon sign", {"batch": 2}, "make me an image of a neon sign"),
    ("resolution: 768*768 a pixel art knight", {"width": 768, "height": 768}, "a pixel art knight"),
    ("画4张雪中的狐狸 1024x1536", {"width": 1024, "height": 1536, "batch": 4}, "画一张雪中的狐狸"),
    ("雪の中の狐を3枚、横長で", {"aspect": 1.3333, "batch": 3}, "雪の中の狐を1枚"),
    ("4 more", {"batch": 4}, ""),
    ("another two", {"batch": 2}, ""),
])
def test_the_ways_of_saying_it(said, paper, left):
    s = read(said)
    assert (s.as_dict(), s.prompt) == (paper, left)


@pytest.mark.parametrize("subject", [
    "a cozy ramen shop at night, cinematic, 4k, highly detailed",   # a quality tag, not a size
    "a portrait of an old sailor",                                  # a subject, not an orientation
    "a wide mountain landscape at dawn",
    "a busy town square at noon",
    "meet me at 9:16 pm under the clock, film still",               # a time, not a shape
    "a wall with 3 cats sitting on it",                             # three cats is one picture
    "a poster that says SALE 50% OFF",
])
def test_a_subject_is_left_alone(subject):
    s = read(subject)
    assert s.as_dict() == {} and s.prompt == subject


def test_paper_words_after_a_picture_mean_the_same_again():
    for said in ("4 more", "try again at 1280x720", "same but 16:9", "now in portrait orientation"):
        assert classify.image_followup(said) == "redo", said
        assert classify.rules(said, after_image=True).task == "image", said
    # with a change asked for as well, it is still a change
    assert classify.image_followup("make it bluer, 4 versions") == "edit"
    # and none of it means anything when no picture was just shown
    assert classify.rules("4 more").task != "image"
    assert classify.rules("what is 1280x720 in megapixels?", after_image=True).task != "image"


@pytest.mark.asyncio
async def test_the_image_model_gets_the_numbers_not_the_words(tmp_path):
    eng = studio(tmp_path)
    cid, first = await say(eng, "generate 4 images of a cat at 1536x1024")
    assert first["backend"] == "flux"
    assert Easel.calls[0] == {"prompt": "generate an image of a cat",
                              "width": 1536, "height": 1024, "batch": 4}

    # the same again is the same paper…
    await say(eng, "try again", cid)
    assert Easel.calls[1] == Easel.calls[0]
    # …unless this one names another: a shape replaces the size, the count stays
    await say(eng, "same but 16:9", cid)
    assert Easel.calls[2] == {"prompt": "generate an image of a cat", "aspect": 1.7778, "batch": 4}
    await say(eng, "2 more", cid)
    assert Easel.calls[3] == {"prompt": "generate an image of a cat", "aspect": 1.7778, "batch": 2}
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_size_given_outright_wins_over_the_words(tmp_path):
    eng = studio(tmp_path)
    started = await eng.ask("generate an image of a cat at 512x512",
                            image={"width": 1024, "height": 768, "batch": 3})
    await settle(eng.runs, started["run"])
    assert Easel.calls[0] == {"prompt": "generate an image of a cat",
                              "width": 1024, "height": 768, "batch": 3}
    await eng.runner.stop()
