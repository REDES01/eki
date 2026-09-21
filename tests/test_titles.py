# SPDX-License-Identifier: Apache-2.0
"""Naming a thread from what's in it."""
import asyncio

from eki import titles
from eki.adapters.base import Backend, BackendInfo, Health, Message, register


def run(coro):
    return asyncio.run(coro)


def test_clean_takes_the_first_line_and_strips_dressing():
    assert titles.clean('  "Harbor seals explained"  ') == "Harbor seals explained"
    assert titles.clean("Title: Trip to Kyoto.\nmore words") == "Trip to Kyoto"
    assert titles.clean("<think>hmm</think>\n**Docstring for app.py**") == "Docstring for app.py"
    assert len(titles.clean("word " * 40)) <= titles.MAX_CHARS


def test_transcript_keeps_both_ends_of_a_long_thread():
    turns = [{"role": "user", "content": "start " * 200},
             {"role": "assistant", "content": "middle " * 200},
             {"role": "user", "content": "end " * 200}]
    text = titles.transcript(turns, limit=400)
    assert text.startswith("user: start") and text.rstrip().endswith("end") and "…" in text
    assert len(text) < 520


@register("titler")
class Titler(Backend):
    replies: list = []
    seen: list = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Titler.seen.append((messages, kw))
        for piece in Titler.replies:
            yield piece


def _backend():
    return Titler(BackendInfo(key="t", kind="titler", label="t"), {})


def test_suggest_asks_briefly_and_returns_a_clean_title():
    Titler.replies = ["Eki, ", "the word\n"]
    Titler.seen = []
    got = run(titles.suggest(_backend(), [
        {"role": "user", "content": "what does eki mean"},
        {"role": "assistant", "content": "Station."}]))
    assert got == "Eki, the word"
    messages, kw = Titler.seen[0]
    assert messages[0].role == "system" and "3 to 6 words" in messages[0].content
    assert kw["max_tokens"] <= 32                    # a title, not an essay


def test_nothing_usable_means_no_title():
    Titler.replies = ["\n\n"]
    assert run(titles.suggest(_backend(), [{"role": "user", "content": "hi"}])) is None
