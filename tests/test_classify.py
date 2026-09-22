# SPDX-License-Identifier: Apache-2.0
"""Labelling a request, and what the label does to routing."""
import asyncio
import json

import httpx
import pytest

from eki import classify, priors
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health, register
from eki.router import Need, Router


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("prompt,task", [
    ("say hi in three words", "chat"),
    ("draw a cat wearing a spacesuit", "image"),
    ("translate to French: I'll be ten minutes late", "translate"),
    ("prove that the square root of 2 is irrational", "math"),
    ("write a python function that reverses a string", "code"),
    ("bump the version number in package.json to 1.4.0", "repo"),
    ("what's the latest version of python", "research"),
    ("write a polite email declining a meeting invite", "writing"),
])
def test_rules_read_the_obvious_cases(prompt, task):
    assert classify.rules(prompt).task == task


def test_a_folder_settles_the_question():
    # whatever it reads like, work with a folder is work on a repo
    assert classify.rules("say hi", has_folder=True).task == "repo"


def test_difficulty_follows_wording_before_length():
    assert classify.rules("hi").difficulty == "easy"
    assert classify.rules("prove the Cauchy-Schwarz inequality").difficulty == "hard"
    assert classify.rules("what is 17 times 23").difficulty == "easy"


def test_parse_only_accepts_known_labels():
    assert classify.parse('{"task": "code", "difficulty": "hard"}').task == "code"
    assert classify.parse('here you go: {"task":"math","difficulty":"easy"} ok').task == "math"
    assert classify.parse('{"task": "banana", "difficulty": "easy"}') is None
    assert classify.parse("no json here") is None


def test_model_label_falls_back_and_stands_down(monkeypatch):
    real = httpx.AsyncClient

    def broken(*a, **kw):
        kw["transport"] = httpx.MockTransport(lambda r: httpx.Response(500, text="nope"))
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", broken)
    model = classify.ModelClassifier("http://127.0.0.1:1")
    clf = classify.Classifier(model)
    label = run(clf.label("write a haiku about autumn"))
    assert label.source == "rules" and label.task == "writing"
    assert model.misses == 1
    model.misses = 3                      # after three misses it isn't asked again
    called = []
    monkeypatch.setattr(model, "label", lambda p: called.append(p))
    assert run(clf.label("hello")).source == "rules"
    assert called == []


def test_model_label_is_used_when_it_answers(monkeypatch):
    real = httpx.AsyncClient

    def ok(*a, **kw):
        def handler(request):
            body = json.loads(request.content)
            assert body["temperature"] == 0.0
            return httpx.Response(200, json={"choices": [{"message": {
                "content": '{"task": "research", "difficulty": "hard"}'}}]})
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", ok)
    clf = classify.Classifier(classify.ModelClassifier("http://127.0.0.1:1"))
    label = run(clf.label("what happened this week"))
    assert (label.task, label.difficulty, label.source) == ("research", "hard", "model")


# ---- what the label does to routing ---------------------------------------

@register("stub")
class Stub(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):        # pragma: no cover - never run
        yield ""


def backend(key, kind, tier, options=None, **caps):
    b = Stub(BackendInfo(key=key, kind=kind, label=key,
                         capabilities=Capabilities(**caps), cost=Cost(tier=tier)),
             options or {})
    return b


def test_easy_work_stays_local_and_hard_work_does_not():
    local = backend("qwen", "mlx", 0, {"model": "Qwen3.8-27B-4bit"})
    cli = backend("claude", "claude_code", 50, {}, tools=True, repo=True)
    router = Router([local, cli])
    assert router.choose(Need(task="chat", difficulty="easy")).backend is local
    assert router.choose(Need(task="chat", difficulty="hard")).backend is cli
    # a 27B model is believed to be fine at everyday writing
    assert router.choose(Need(task="writing", difficulty="medium")).backend is local
    # but not at hard maths
    assert router.choose(Need(task="math", difficulty="hard")).backend is cli


def test_with_nothing_good_enough_the_best_available_wins():
    small = backend("tiny", "mlx", 0, {"model": "Qwen3.5-2B-4bit"})
    router = Router([small])
    choice = router.choose(Need(task="math", difficulty="hard"))
    assert choice.backend is small and "best eki has" in choice.reason


def test_the_router_model_is_not_used_for_answers():
    small = backend("router", "mlx", 0, {"model": "Qwen3.5-2B-4bit"})
    big = backend("qwen", "mlx", 0, {"model": "Qwen3.8-27B-4bit"})
    router = Router([small, big], reserved={"router"})
    assert router.choose(Need(task="chat", difficulty="easy")).backend is big
    # still reachable when asked for by name
    assert router.choose(Need(backend="router")).backend is small


def test_class_of_reads_size_and_address():
    assert priors.class_of("claude_code", {}) == "frontier_agent"
    assert priors.class_of("anthropic_api", {}) == "frontier_api"
    assert priors.class_of("openai_compat",
                           {"base_url": "https://api.openai.com/v1"}) == "frontier_api"
    assert priors.class_of("mlx", {"model": "mlx-community/Qwen3.8-27B-4bit"}) == "large_open"
    assert priors.class_of("mlx", {"model": "mlx-community/Qwen3.5-2B-MLX-4bit"}) == "small_open"
    assert priors.class_of("openai_compat",
                           {"base_url": "http://127.0.0.1:1234/v1",
                            "model": "gemma-3-12b-it"}) == "mid_open"


def test_seed_set_labels_are_all_known():
    from eki.evals.label_eval import load
    rows = load()
    assert len(rows) > 150
    assert {r["task"] for r in rows} <= set(classify.TASKS)
    assert {r["difficulty"] for r in rows} <= set(classify.DIFFICULTIES)


def test_rules_beat_chance_on_the_seed_set():
    from eki.evals.label_eval import load, score
    rows = load()
    got = [classify.rules(r["p"]) for r in rows]
    # tuned on this set, so this is a regression guard, not a claim of accuracy
    assert score(rows, got)["task"] >= 0.85


# ---- saying "generate an image" is enough --------------------------------

@pytest.mark.parametrize("prompt", [
    "generate an image of a cat",
    "generation image: a red fox in snow",
    "generate image of a lighthouse at dusk",
    "make me an image of tokyo at night",
    "create a picture with a dragon",
    "can you generate a pic of my dog as an astronaut",
    "i want a image, a cyberpunk station",
    "image: brass compass on a map",
    "生成一张图片：东京车站",
    "画一只猫",
    "駅の画像を生成して",
])
def test_asking_for_a_picture_in_words_is_an_image_request(prompt):
    assert classify.asks_for_image(prompt)
    assert classify.rules(prompt).task == "image"


@pytest.mark.parametrize("prompt", [
    "generate a function that resizes an image in python",
    "what image formats support transparency?",
    "describe this image",
    "make this image smaller",
    "make the picture clearer in my essay intro",
    "create a plan to resize the image",
])
def test_a_picture_as_the_subject_is_not_a_request_to_draw(prompt):
    assert not classify.asks_for_image(prompt)
    assert classify.rules(prompt).task != "image"


def test_the_model_label_does_not_overrule_an_outright_ask():
    class Wrong:
        misses = 0

        async def label(self, prompt):
            return classify.Label(task="chat", difficulty="easy", source="model")

    c = classify.Classifier(Wrong(), use_model=True)
    assert run(c.label("generate an image of a fox")).task == "image"
    assert run(c.label("say hi")).task == "chat"


def test_the_model_cannot_talk_a_picture_edit_into_chat():
    class Wrong:
        misses = 0

        async def label(self, prompt):
            return classify.Label(task="chat", difficulty="easy", source="model")
    c = classify.Classifier(Wrong(), use_model=True)
    assert run(c.label("make it bluer", after_image=True)).task == "image"
    assert run(c.label("make it bluer")).task == "chat"


def test_the_screen_is_a_task_of_its_own_that_needs_a_program_with_tools():
    from eki.classify import rules, wants_screen
    for text in ("take screenshot", "Take a screenshot of my screen", "what's on my screen right now",
                 "click the Save button in Xcode", "open Finder", "截图", "control my mac and open Safari"):
        assert rules(text).task == "screen", text
    for text in ("screenshot tools in Playwright, how?", "take a picture of a fox", "open the file config.yaml",
                 "type hints in python"):
        assert not wants_screen(text), text


def test_anything_that_has_to_be_done_goes_to_a_harness():
    from eki.classify import needs_hands
    doing = ("run the tests", "install ffmpeg with brew", "list the files in my downloads folder",
             "what's taking up disk space on my mac", "fetch https://example.com and summarise it",
             "open Safari", "delete the old logs", "check config.yaml for the port", "git status",
             "take screenshot", "restart the engine", "运行测试", "ファイルを消して")
    asking = ("how do I run the tests in pytest", "what does git rebase do", "explain docker layers",
              "write a script that lists files", "should I install ffmpeg via brew or conda",
              "a haiku about autumn", "translate hello into japanese", "what is 17 * 23")
    for text in doing:
        assert needs_hands(text), text
    for text in asking:
        assert not needs_hands(text), text


def test_the_model_can_say_hands_and_the_words_can_too():
    from eki import classify
    class Says:
        misses = 0
        async def label(self, prompt):
            return classify.Label(task="chat", difficulty="easy", source="model", hands=True)
    c = classify.Classifier(Says(), use_model=True)
    got = run(c.label("tidy up whatever is cluttering things"))     # the model saw hands; the rules didn't
    assert got.hands and got.task == "chat"
    class Silent:
        misses = 0
        async def label(self, prompt):
            return classify.Label(task="chat", difficulty="easy", source="model")
    got = run(classify.Classifier(Silent(), use_model=True).label("run the tests"))
    assert got.hands                                                  # the words alone are enough
    assert classify.rules("what is 17 * 23").hands is False
    assert classify.rules("run the tests").to_json()["hands"] is True
