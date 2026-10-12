"""A project's history in plain words: changes landed today, and going back."""
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from eki import db, history, projectchats, server, store, workspace
from eki.workspace import git


def commit(repo, name, text, msg):
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    shas = [commit(r, "a.txt", "one\n", "the folder as it was"),
            commit(r, "a.txt", "two\n", "second"),
            commit(r, "b.txt", "three\n", "third")]
    return r, shas


def item(conn, sha, title="speed up"):
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, state, created_at) VALUES (?,?,?,?)",
                     (gid, "goal", "planned", db.now()))
        conn.execute("INSERT INTO items(id, goal_id, title, state, commit_sha, created_at, updated_at) "
                     "VALUES (?,?,?,'merged',?,?,?)", (store.new_id(), gid, title, sha, db.now(), db.now()))


def test_three_changes_today_newest_first(conn, repo):
    r, shas = repo
    got = history.changes(conn, r)
    assert got["today"] == 3 and got["line"] == "3 changes landed today"
    assert [c["id"] for c in got["changes"]] == shas[::-1]
    assert [c["by"] for c in got["changes"]] == ["you", "you", "eki"]
    assert history.line(1) == "1 change landed today" and history.line(0) == "No changes today"


def test_an_items_commit_shows_its_title(conn, repo):
    r, shas = repo
    item(conn, shas[1], "make it faster")
    second = history.changes(conn, r)["changes"][1]
    assert second == {"id": shas[1], "title": "make it faster", "when": second["when"], "by": "eki"}


def test_go_back_is_a_new_change_on_top(conn, repo):
    r, shas = repo
    done = history.go_back(conn, r, shas[1])
    assert done["title"] == "Go back to before second"
    assert (r / "a.txt").read_text() == "one\n" and not (r / "b.txt").exists()
    assert git(r, "rev-parse", "HEAD") == done["id"]
    assert git(r, "log", "-1", "--format=%s") == "Go back to before second"
    for old in shas:
        git(r, "merge-base", "--is-ancestor", old, "HEAD")
    assert git(r, "status", "--porcelain") == ""
    assert history.changes(conn, r)["changes"][0]["title"] == "Go back to before second"


def test_unsaved_edits_are_refused_and_nothing_changes(conn, repo):
    r, shas = repo
    (r / "a.txt").write_text("mine\n")
    with pytest.raises(ValueError, match="unsaved edits in a.txt: save or discard them first"):
        history.go_back(conn, r, shas[1])
    assert git(r, "rev-parse", "HEAD") == shas[2]
    assert (r / "a.txt").read_text() == "mine\n" and (r / "b.txt").exists()


def test_refusals(conn, repo, tmp_path):
    r, shas = repo
    with pytest.raises(ValueError, match="nothing before it"):
        history.go_back(conn, r, shas[0])
    with pytest.raises(ValueError, match="isn't in this project's history"):
        history.go_back(conn, r, "0" * 40)
    git(r, "checkout", "-q", "-b", "side", shas[1])
    side = commit(r, "c.txt", "side\n", "side")
    git(r, "checkout", "-q", "main")
    git(r, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    with pytest.raises(ValueError, match="merge"):
        history.go_back(conn, r, shas[2])
    git(r, "checkout", "-q", "-b", "other", shas[0])
    with pytest.raises(ValueError, match="isn't in this project's history"):
        history.go_back(conn, r, side)
    assert git(r, "status", "--porcelain") == ""


def test_no_history_is_chat_only(conn, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.txt").write_text("hi")
    chat_only = {"today": 0, "line": "No history kept: chat only", "changes": []}
    assert history.changes(conn, plain) == chat_only
    git(plain, "init", "-q")
    assert history.changes(conn, plain) == chat_only                    # a repo with no commit yet
    with pytest.raises(ValueError):
        history.go_back(conn, plain, "HEAD")


def test_routes(conn, tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(src))
    pid = projectchats.create(conn, "web")
    folder = projectchats.folder_of(conn, pid)
    if not workspace.repo_of(folder):
        git(folder, "init", "-q", "-b", "main")
    first = commit(Path(folder), "a.txt", "one\n", "the folder as it was")
    second = commit(Path(folder), "a.txt", "two\n", "second")
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Eki": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        code, got = call(f"/api/projects/{pid}/history")
        assert code == 200 and [c["id"] for c in got["changes"]][:2] == [second, first]
        assert call("/api/projects/eki/history")[1]["line"] == "No history kept: chat only"
        assert call(f"/api/projects/{pid}/goback", {"change": first})[0] == 400
        code, got = call(f"/api/projects/{pid}/goback", {"change": second})
        assert code == 200 and got["done"]["title"] == "Go back to before second"
        assert got["history"]["changes"][0]["id"] == got["done"]["id"]
        assert call("/api/projects/nope/history")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
