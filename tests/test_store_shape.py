"""The shape of what the store hands a client.

These look pedantic until a field goes missing: the Mac app decodes turns into
a typed list, so a turn without an id made every existing conversation render
as empty — no error, just nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from hub.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "hub.db")
    yield s
    s.close()


def test_turns_carry_a_stable_id(store):
    cid = store.new_conversation()
    store.add_turn(cid, "user", "hello")
    store.add_turn(cid, "assistant", "hi", "qwen", "cheapest fit")
    rows = store.turns(cid)
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    assert all(isinstance(r["id"], int) for r in rows)
    assert {"id", "role", "content", "backend", "reason", "meta", "created_at"} <= \
        set(rows[0])


def test_search_finds_a_turn_the_title_does_not_show(store):
    cid = store.new_conversation()
    store.add_turn(cid, "user", "what is a page fault")
    store.add_turn(cid, "assistant", "a trap the MMU raises", "qwen")
    store.add_turn(cid, "user", "and mmap?")

    assert [r["id"] for r in store.search("mmap")] == [cid]
    assert "mmap" in store.search("mmap")[0]["hit"]
    assert store.search("nothing like this") == []


def test_search_matches_the_title_too(store):
    cid = store.new_conversation()
    store.add_turn(cid, "user", "brass compass on a sea chart")
    assert [r["id"] for r in store.search("compass")] == [cid]


def test_cost_counts_turns_per_backend(store):
    cid = store.new_conversation()
    store.add_turn(cid, "user", "q1")
    store.add_turn(cid, "assistant", "a1", "claude", "",
                   meta={"usage": {"input_tokens": 100, "output_tokens": 20}})
    store.add_turn(cid, "user", "q2")
    store.add_turn(cid, "assistant", "a2", "qwen", "",
                   meta={"usage": {"input_tokens": 7, "output_tokens": 3}})
    store.add_turn(cid, "assistant", "a3", "qwen")          # no usage reported

    report = store.cost(cid)
    assert report["turns"] == 3
    assert report["by_backend"]["qwen"]["turns"] == 2
    assert report["by_backend"]["qwen"]["output_tokens"] == 3
    assert report["by_backend"]["claude"]["input_tokens"] == 100


def test_cost_of_an_unanswered_conversation_is_empty(store):
    cid = store.new_conversation()
    store.add_turn(cid, "user", "hello?")
    assert store.cost(cid)["by_backend"] == {}
