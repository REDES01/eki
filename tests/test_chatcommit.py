"""A project's history covers its chats: each chat turn's changes, committed (eki/chatcommit.py)."""
import json

import pytest

from eki import chatcommit, db, projects, providers, store, workspace
from eki.providers.base import Outcome
from tests.conftest import run_inline


@pytest.fixture
def folder(tmp_path):
    """A person's git folder on branch trunk, with no identity configured and one ignored pattern."""
    r = tmp_path / "work"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    (r / ".gitignore").write_text("secret.txt\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


@pytest.fixture
def pid(conn, folder):
    return projects.add(conn, folder)


def git(folder, *args):
    return workspace.git(folder, *args)


def subjects(folder):
    return git(folder, "log", "--format=%s").splitlines()


def chat_run(conn, pid, cwd, prompt="Make it say hello\nand more", attempt=1):
    with db.tx(conn):
        tid = store.create_thread(conn, "hi", str(cwd))
        conn.execute("UPDATE threads SET project=? WHERE id=?", (pid, tid))
        rid = store.create_run(conn, tid, prompt)
        store.update_run(conn, rid, attempt=attempt)
    return store.run(conn, rid)


def commits(conn, rid):
    return [json.loads(e["data"]) for e in store.events_after(conn, rid) if e["kind"] == "commit"]


def test_a_chat_turn_that_edits_two_files_is_one_commit(conn, folder, pid):
    r = chat_run(conn, pid, folder)
    assert chatcommit.before_turn(conn, r) is None            # nothing changed by hand
    (folder / "app.py").write_text("print('hello')\n")
    (folder / "new.py").write_text("x = 1\n")
    sha = chatcommit.after_turn(conn, r)
    assert sha == git(folder, "rev-parse", "HEAD")
    assert subjects(folder) == ["eki: chat — Make it say hello", "first"]
    assert set(git(folder, "show", "--name-only", "--format=", sha).split()) == {"app.py", "new.py"}
    assert git(folder, "status", "--porcelain") == ""
    assert commits(conn, r["id"]) == [{"sha": sha, "title": "Make it say hello", "kind": "chat",
                                      "folder": chatcommit.projectchats.norm(folder)}]


def test_a_hand_edit_before_the_turn_is_its_own_commit(conn, folder, pid):
    (folder / "app.py").write_text("print('mine')\n")
    r = chat_run(conn, pid, folder)
    hand = chatcommit.before_turn(conn, r)
    assert hand
    (folder / "new.py").write_text("x = 1\n")
    chat = chatcommit.after_turn(conn, r)
    assert subjects(folder) == ["eki: chat — Make it say hello", "eki: your edits", "first"]
    assert git(folder, "rev-parse", f"{chat}^") == hand
    assert git(folder, "show", "--name-only", "--format=", hand).split() == ["app.py"]
    assert [c["kind"] for c in commits(conn, r["id"])] == ["hand", "chat"]


def test_a_turn_that_changes_nothing_adds_no_commit(conn, folder, pid):
    r = chat_run(conn, pid, folder)
    assert chatcommit.before_turn(conn, r) is None
    assert chatcommit.after_turn(conn, r) is None
    assert subjects(folder) == ["first"]
    assert commits(conn, r["id"]) == []


def test_the_setting_off_commits_nothing(conn, folder, pid):
    projects.set_chat_history(conn, pid, False)
    assert projects.chat_history(projects.get(conn, pid)) is False
    (folder / "app.py").write_text("print('mine')\n")
    r = chat_run(conn, pid, folder)
    assert chatcommit.before_turn(conn, r) is None
    assert chatcommit.after_turn(conn, r) is None
    assert subjects(folder) == ["first"]
    with pytest.raises(KeyError):
        projects.set_chat_history(conn, "nope", True)


def test_a_worktree_is_left_alone(conn, folder, pid, tmp_path):
    wt = tmp_path / "wt"
    git(folder, "worktree", "add", "-q", "-b", "side", str(wt))
    r = chat_run(conn, pid, wt)
    assert chatcommit.target(conn, r) is None
    (wt / "app.py").write_text("print('wt')\n")
    assert chatcommit.after_turn(conn, r) is None
    assert git(wt, "status", "--porcelain")                  # still changed, not recorded
    assert subjects(folder) == ["first"]


def test_a_resumed_attempt_records_no_hand_edits(conn, folder, pid):
    r = chat_run(conn, pid, folder, attempt=2)
    (folder / "app.py").write_text("print('half done')\n")
    assert chatcommit.before_turn(conn, r) is None
    assert subjects(folder) == ["first"]
    assert chatcommit.after_turn(conn, r)
    assert subjects(folder) == ["eki: chat — Make it say hello", "first"]


def test_ignored_files_are_never_committed(conn, folder, pid):
    (folder / "secret.txt").write_text("hush\n")
    (folder / "app.py").write_text("print('x')\n")
    r = chat_run(conn, pid, folder)
    sha = chatcommit.after_turn(conn, r)
    assert git(folder, "show", "--name-only", "--format=", sha).split() == ["app.py"]
    assert not git(folder, "ls-files", "secret.txt")


def test_a_git_failure_is_a_note_and_the_next_turn_picks_it_up(conn, folder, pid):
    (folder / "app.py").write_text("print('x')\n")
    lock = folder / ".git" / "index.lock"
    lock.write_text("")
    r = chat_run(conn, pid, folder)
    assert chatcommit.after_turn(conn, r) is None
    notes = [json.loads(e["data"])["text"] for e in store.events_after(conn, r["id"]) if e["kind"] == "note"]
    assert notes and notes[0].startswith("chat history: not recorded — ")
    lock.unlink()
    nxt = chat_run(conn, pid, folder, prompt="next")
    assert chatcommit.after_turn(conn, nxt)
    assert subjects(folder) == ["eki: chat — next", "first"]


def test_eki_itself_and_chores_are_left_alone(conn, folder, pid):
    r = chat_run(conn, pid, folder)
    with db.tx(conn):
        store.update_run(conn, r["id"], row="chore")
    assert chatcommit.target(conn, store.run(conn, r["id"])) is None
    with db.tx(conn):
        conn.execute("UPDATE threads SET project='eki' WHERE id=?", (r["thread_id"],))
    assert chatcommit.target(conn, r) is None


def test_the_title_is_the_first_line_cut_short():
    assert chatcommit.title("\n\n  fix   the   bug \nmore") == "fix the bug"
    assert len(chatcommit.title("a" * 200)) == chatcommit.TITLE


def test_the_worker_records_the_turn(conn, folder, pid, monkeypatch):
    (folder / "app.py").write_text("print('mine')\n")

    class Editing:
        def take(self, turn, emit):
            (folder / "a.txt").write_text("a\n")
            (folder / "b.txt").write_text("b\n")
            emit("text", {"text": "done"})
            return Outcome(state="done")

    monkeypatch.setattr(providers, "get", lambda name: Editing())
    with db.tx(conn):
        tid = store.create_thread(conn, "hi", str(folder))
        conn.execute("UPDATE threads SET project=? WHERE id=?", (pid, tid))
        rid = store.create_run(conn, tid, "add a and b", provider="fake")
    assert run_inline(conn, rid)["state"] == "done"
    assert subjects(folder) == ["eki: chat — add a and b", "eki: your edits", "first"]
    assert set(git(folder, "show", "--name-only", "--format=", "HEAD").split()) == {"a.txt", "b.txt"}


def test_record_works_with_no_identity(folder, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    (folder / "app.py").write_text("print('x')\n")
    sha = chatcommit.record(str(folder), "eki: chat — x")
    assert git(folder, "log", "-1", "--format=%an <%ae>", sha) == "eki <eki@localhost>"
    assert chatcommit.record(str(folder), "again") is None
