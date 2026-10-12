"""Go back to before a chat turn (eki/chatundo.py), and the chat-history switch, over the routes."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from eki import api, api_projects, chatcommit, chatundo, db, projects, server, store, workspace


@pytest.fixture
def folder(tmp_path):
    """A person's git folder on branch trunk, with one ignored pattern: the media pool."""
    r = tmp_path / "work"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    (r / "notes.md").write_text("old notes\n")
    (r / ".gitignore").write_text("assets/\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


@pytest.fixture
def pid(conn, folder):
    return projects.add(conn, folder)


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Eki": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def git(folder, *args):
    return workspace.git(folder, *args)


def subjects(folder):
    return git(folder, "log", "--format=%s").splitlines()


def chat_turn(conn, pid, folder, edit):
    """A chat run in the project's folder whose turn does `edit`, recorded as chatcommit does."""
    with db.tx(conn):
        tid = store.create_thread(conn, "hi", str(folder))
        conn.execute("UPDATE threads SET project=? WHERE id=?", (pid, tid))
        rid = store.create_run(conn, tid, "Rewrite both files\nplease")
        store.update_run(conn, rid, attempt=1)
    r = store.run(conn, rid)
    chatcommit.before_turn(conn, r)
    edit()
    return r, chatcommit.after_turn(conn, r)


def two_files(folder):
    def edit():
        (folder / "app.py").write_text("print('bye')\n")
        (folder / "notes.md").write_text("new notes\n")
    return edit


def test_going_back_restores_both_files_in_one_new_commit(conn, folder, pid):
    (folder / "assets").mkdir()
    (folder / "assets" / "clip.mov").write_text("media")
    r, sha = chat_turn(conn, pid, folder, two_files(folder))
    assert sha
    got = chatundo.back_to_before(conn, r["id"], sha)
    assert got == {"sha": git(folder, "rev-parse", "HEAD"), "title": "eki: back to before — Rewrite both files"}
    assert (folder / "app.py").read_text() == "print('hi')\n"
    assert (folder / "notes.md").read_text() == "old notes\n"
    assert subjects(folder) == ["eki: back to before — Rewrite both files", "eki: chat — Rewrite both files", "first"]
    assert git(folder, "status", "--porcelain") == ""
    assert (folder / "assets" / "clip.mov").read_text() == "media"          # the ignored pool is untouched
    steps = [s for s in api.thread(conn, r["thread_id"])["runs"][0]["steps"] if s["kind"] == "commit"]
    assert [(s["commit"], s["title"]) for s in steps] == [("chat", "Rewrite both files"), ("back", "Rewrite both files")]
    assert steps[1]["sha"] == got["sha"]


def test_a_hand_edit_present_is_recorded_first(conn, folder, pid):
    r, sha = chat_turn(conn, pid, folder, two_files(folder))
    (folder / "mine.txt").write_text("by hand\n")
    chatundo.back_to_before(conn, r["id"], sha)
    assert subjects(folder)[:2] == ["eki: back to before — Rewrite both files", "eki: your edits"]
    assert not (folder / "mine.txt").exists()                        # the folder is as before the turn ...
    assert git(folder, "show", "HEAD~1:mine.txt") == "by hand"         # ... and the hand edit is kept in the history
    assert (folder / "app.py").read_text() == "print('hi')\n"
    kinds = [json.loads(e["data"])["kind"] for e in store.events_after(conn, r["id"]) if e["kind"] == "commit"]
    assert kinds == ["chat", "hand", "back"]


def test_refuses_when_the_branch_is_not_checked_out(conn, folder, pid):
    r, sha = chat_turn(conn, pid, folder, two_files(folder))
    git(folder, "checkout", "-q", "-b", "other")
    with pytest.raises(ValueError, match="no longer has the project's branch"):
        chatundo.back_to_before(conn, r["id"], sha)


def test_an_unknown_sha_is_a_4xx_through_the_route(conn, folder, pid, web):
    r, sha = chat_turn(conn, pid, folder, two_files(folder))
    code, got = post(f"{web}/api/runs/{r['id']}/back", {"sha": "0" * 40})
    assert 400 <= code < 500 and "error" in got
    assert subjects(folder)[0] == "eki: chat — Rewrite both files"
    code, got = post(f"{web}/api/runs/{r['id']}/back", {"sha": sha})
    assert code == 200 and got["title"] == "eki: back to before — Rewrite both files"


def test_the_chat_history_route_flips_the_setting(conn, pid, web):
    assert api_projects.show(conn, pid)["chat_history"] is True
    code, got = post(f"{web}/api/projects/{pid}/chat-history", {"on": False})
    assert (code, got) == (200, {"chat_history": False})
    assert projects.chat_history(projects.get(conn, pid)) is False
    assert [e["chat_history"] for e in api_projects.projects_list(conn)["projects"] if e["id"] == pid] == [False]
    assert post(f"{web}/api/projects/{pid}/chat-history", {"on": True}) == (200, {"chat_history": True})
    assert post(f"{web}/api/projects/nope/chat-history", {"on": True})[0] == 404
