# SPDX-License-Identifier: Apache-2.0
"""The store's job: one conversation, whichever backend answered each turn."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eki.store import Store


def test_thread_can_mix_backends(tmp_path):
    s = Store(tmp_path / "t.db")
    cid = s.new_conversation()
    s.add_turn(cid, "user", "what is a page fault?")
    s.add_turn(cid, "assistant", "an interrupt…", "qwen", "tier 0")
    s.add_turn(cid, "user", "now fix it in my repo")
    s.add_turn(cid, "assistant", "patched", "claude", "needs repo")

    turns = s.turns(cid)
    assert [t["backend"] for t in turns] == ["", "qwen", "", "claude"]
    assert len(s.conversations()) == 1
    assert s.conversations()[0]["n"] == 4
    s.close()


def test_title_comes_from_the_first_user_turn(tmp_path):
    s = Store(tmp_path / "t.db")
    cid = s.new_conversation()
    s.add_turn(cid, "user", "explain mmap")
    s.add_turn(cid, "assistant", "…", "qwen")
    s.add_turn(cid, "user", "and munmap?")
    assert s.conversations()[0]["title"] == "explain mmap"
    s.close()


def test_backend_sessions_are_per_backend(tmp_path):
    s = Store(tmp_path / "t.db")
    cid = s.new_conversation()
    s.set_session(cid, "claude", "sess-1")
    s.set_session(cid, "codex", "sess-2")
    assert s.session(cid, "claude") == "sess-1"
    assert s.session(cid, "codex") == "sess-2"
    s.set_session(cid, "claude", "sess-3")          # newest wins
    assert s.session(cid, "claude") == "sess-3"
    assert s.session(cid, "qwen") is None
    s.close()


def test_latest_conversation_tracks_activity(tmp_path):
    s = Store(tmp_path / "t.db")
    first = s.new_conversation()
    second = s.new_conversation()
    s.add_turn(first, "user", "older thread, but just touched")
    assert s.latest_conversation() == first
    s.close()


def test_pin_rename_archive_delete(tmp_path):
    from eki.store import Store
    s = Store(tmp_path / "hub.db")
    a, b = s.new_conversation(), s.new_conversation()
    s.add_turn(a, "user", "older question")
    s.add_turn(b, "user", "newer question")
    assert [c["id"] for c in s.conversations()] == [b, a]

    assert s.set_conversation(a, pinned=True, title="  Keep this one  ")
    rows = s.conversations()
    assert rows[0]["id"] == a and rows[0]["pinned"] == 1 and rows[0]["title"] == "Keep this one"

    assert s.set_conversation(b, archived=True)
    assert [c["id"] for c in s.conversations()] == [a]
    assert [c["id"] for c in s.conversations(archived=True)] == [b]
    # archived threads are still searchable — they left the list, not the history
    assert [c["id"] for c in s.search("newer")] == [b]

    assert s.delete_conversation(b)
    assert s.search("newer") == [] and s.turns(b) == []
    assert not s.set_conversation("nope", pinned=True)


def test_made_lists_answers_that_hold_something_across_threads(tmp_path):
    s = Store(tmp_path / "t.db")
    a = s.new_conversation()
    s.add_turn(a, "user", "a page please ```html")            # a question is not an answer
    s.add_turn(a, "assistant", "here:\n```html\n<html><body>hi</body></html>\n```", "claude")
    s.add_turn(a, "assistant", "just words", "claude")
    b = s.new_conversation()
    s.add_turn(b, "user", "draw a fox")
    s.add_turn(b, "assistant", "\n![a fox](/pics/fox.png)\n", "flux")

    made = s.made()
    assert [(m["conversation_id"], m["backend"]) for m in made] == [(b, "flux"), (a, "claude")]
    assert made[0]["title"] == "draw a fox"
    assert len(s.made(limit=1)) == 1
    s.close()
