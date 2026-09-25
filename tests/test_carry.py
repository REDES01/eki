# SPDX-License-Identifier: Apache-2.0
"""A long thread moving between models: a program with its own session
resumes it and hears only what it missed; anything else reads the thread,
summarized once it no longer fits — and the summary stays in the thread."""
import asyncio

import pytest

from eki import carry
from eki.adapters.base import Backend, BackendInfo, Capabilities, Health
from eki.store import Store
from tests.test_handoff import Harness, Local, eng, say  # noqa: F401  (eng is a fixture)


def turn(i, role, text, backend=""):
    return {"id": i, "role": role, "content": text, "backend": backend}


def thread(n, width=1000):
    out = []
    for i in range(n):
        out.append(turn(2 * i + 1, "user", f"q{i} " + "x" * width))
        out.append(turn(2 * i + 2, "assistant", f"a{i} " + "y" * width, "qwen"))
    return out


# ---- the rule ---------------------------------------------------------------------------

def test_a_thread_that_fits_is_read_whole():
    turns = thread(3, 100)
    assert carry.plan(turns, None, 10_000) == (False, [], turns)


def test_a_long_thread_folds_the_older_part_and_keeps_the_latest_word_for_word():
    turns = thread(20)
    use, fold, recent = carry.plan(turns, None, 8000)
    assert not use and fold and recent
    assert fold + recent == turns and recent[-1] == turns[-1]
    assert carry.size(recent) <= 4000


def test_a_kept_summary_is_read_instead_of_summarizing_again():
    turns = thread(20)
    kept = {"upto": 34, "text": "notes", "author": "Qwen"}
    use, fold, recent = carry.plan(turns, kept, 8000)
    assert use and not fold and [t["id"] for t in recent] == [35, 36, 37, 38, 39, 40]
    # and once what came after it outgrows the room too, it's folded in on top
    use, fold, recent = carry.plan(turns, {**kept, "upto": 4}, 8000)
    assert use and fold[0]["id"] == 5


def test_a_resumed_session_hears_only_what_others_said_since():
    turns = [turn(1, "user", "haiku"), turn(2, "assistant", "Rain", "qwen"),
             turn(3, "user", "a game"), turn(4, "assistant", "Building it.", "claude_code"),
             turn(5, "user", "about snow"), turn(6, "assistant", "Snow", "qwen"),
             turn(7, "user", "a fox hero")]
    assert [t["id"] for t in carry.missed(turns, "claude_code", 7)] == [5, 6]
    assert carry.missed(turns[:5], "claude_code", 5) == []       # it answered last: it remembers


def test_the_summary_opens_the_first_message_so_roles_still_alternate():
    out = carry.as_messages("notes", "Qwen", [turn(9, "assistant", "a"), turn(10, "user", "b")])
    assert [m.role for m in out] == ["user", "assistant", "user"] and "notes" in out[0].content
    out = carry.as_messages("notes", "Qwen", [turn(10, "user", "b")])
    assert len(out) == 1 and out[0].content.endswith("b") and "by Qwen" in out[0].content


def test_with_no_writer_an_excerpt_stands_in_and_says_so():
    text = carry.excerpt("", thread(2), {"qwen": "Qwen"})
    assert text.startswith("(An excerpt, not a summary") and "Qwen: a0" in text


class Writer(Backend):
    asked = []

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Writer.asked.append(messages[-1].content)
        yield "<think>hmm</think>They want haiku about rain; agreed on five-seven-five."


def test_the_writer_folds_a_long_stretch_a_piece_at_a_time():
    Writer.asked = []
    w = Writer(BackendInfo(key="q", kind="mlx", label="Q", capabilities=Capabilities(context_tokens=4000)), {})
    got = asyncio.run(carry.write(w, "before", thread(20), {"qwen": "Qwen"}))
    assert got == "They want haiku about rain; agreed on five-seven-five."
    assert len(Writer.asked) > 1 and Writer.asked[0].startswith("Notes so far:\nbefore")
    assert "five-seven-five" in Writer.asked[1]                     # the next pass builds on it


def test_the_store_keeps_summaries_per_thread(tmp_path):
    s = Store(tmp_path / "eki.db")
    cid = s.new_conversation("t")
    s.add_summary(cid, 10, "first", "Qwen")
    s.add_summary(cid, 30, "second", "Qwen")
    assert s.summary(cid)["text"] == "second"
    assert s.summary(cid, before=20)["text"] == "first"          # a replay reads what it had then
    assert [x["upto"] for x in s.summaries(cid)] == [10, 30]
    s.delete_conversation(cid)
    assert s.summaries(cid) == []


# ---- in a thread ------------------------------------------------------------------------

def grow(e, cid, n):
    for i in range(n):
        e.store.add_turn(cid, "user", f"q{i} " + "x" * 1400)
        e.store.add_turn(cid, "assistant", f"a{i} " + "y" * 1400, backend="qwen")


@pytest.mark.asyncio
async def test_a_small_model_reads_a_summary_once_the_thread_outgrows_it(eng):
    Writer.asked = []
    w = Writer(BackendInfo(key="qwen", kind="mlx", label="Qwen 27B (local)"), {})
    eng._titler = lambda exclude="": w
    cid, run, _ = await say(eng, "write a haiku about rain")
    grow(eng, cid, 20)
    _, run, turn = await say(eng, "no, make it about snow instead", cid)
    assert run["backend"] == "qwen" and turn["content"].startswith("Snow")
    read = Local.seen[-1]
    assert sum(len(m.content) for m in read) < 20_000
    assert any("five-seven-five" in m.content for m in read)
    assert read[-1].content == "no, make it about snow instead"
    kept = eng.store.summaries(cid)
    assert len(kept) == 1 and kept[0]["author"] == "Qwen 27B (local)"
    assert eng.conversation(cid)["summaries"] == kept                # readable, beside the turns
    # the next turn starts from it instead of writing another
    asked = len(Writer.asked)
    await say(eng, "and one about wind", cid)
    assert len(Writer.asked) == asked and len(eng.store.summaries(cid)) == 1
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_program_joining_a_long_thread_is_given_the_summary(eng):
    eng._titler = lambda exclude="": None                             # nothing local up
    cid, _, _ = await say(eng, "write a haiku about rain")
    grow(eng, cid, 20)
    _, run, _ = await say(eng, "now make a game around it", cid)
    assert run["backend"] == "claude_code"
    joined = Harness.seen[-1]
    assert "joining a conversation" in joined and "a summary of the earlier part, by eki (excerpt)" in joined
    assert len(joined) < 25_000
    assert eng.store.summaries(cid)[0]["text"].startswith("(An excerpt")
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_resumed_session_is_told_only_what_it_missed(eng):
    cid, _, _ = await say(eng, "write a haiku about rain")
    await say(eng, "now make a game around it", cid)
    eng.store.set_session(cid, "claude_code", "sess-1")
    from tests.test_runs import settle
    s = await eng.ask("no, make it about snow instead", conversation=cid, backend_key="qwen")
    await settle(eng.runs, s["run"], timeout=5)
    s = await eng.ask("make the hero a fox", conversation=cid, backend_key="claude_code")
    await settle(eng.runs, s["run"], timeout=5)
    told = Harness.seen[-1]
    assert told.startswith("[eki: since you last answered") and "Snow on the rails" in told
    assert "Building it." not in told and "Rain on the rails" not in told
    assert told.rstrip().endswith("make the hero a fox")
    await eng.runner.stop()
