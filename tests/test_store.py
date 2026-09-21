"""The store's job: one conversation, whichever backend answered each turn."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.store import Store


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
