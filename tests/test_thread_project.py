"""Moving a chat into a project: its folder, and a fresh session with the whole transcript."""
import pytest

from eki import asking, db, projectchats, projects, store, threadacts, worker, workspace


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def proj(conn, src, tmp_path):
    return projectchats.create(conn, "tokenbar", tmp_path / "user" / "tokenbar")


def row(conn, tid):
    return conn.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()


@pytest.fixture
def chat(conn, tmp_path):
    """A chat in a folder of its own, one done run, a session that took part in it."""
    here = tmp_path / "here"
    here.mkdir()
    tid, rid = asking.submit(conn, "first question", cwd=str(here))
    with db.tx(conn):
        store.update_run(conn, rid, state="done", ended_at=db.now())
        store.add_event(conn, rid, 1, "text", {"text": "first answer"})
        store.save_session(conn, tid, "claude", "sess-1", rid)
    return tid


def test_move_to_another_folder_starts_fresh(conn, proj, chat):
    threadacts.update(conn, chat, {"project": proj})
    t = row(conn, chat)
    assert t["project"] == proj and t["cwd"] == projectchats.folder_of(conn, proj)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE thread_id=?", (chat,)).fetchone()[0] == 0
    _, rid = asking.submit(conn, "second question", thread=chat)
    turn = worker.build_turn(conn, store.run(conn, rid), "claude")
    assert turn.resume is None
    assert turn.cwd == t["cwd"]
    assert [m["content"] for m in turn.history] == ["first question", "first answer"]


def test_move_to_the_same_folder_keeps_sessions(conn, proj, chat):
    folder = projectchats.folder_of(conn, proj)
    with db.tx(conn):
        conn.execute("UPDATE threads SET cwd=? WHERE id=?", (folder + "/", chat))
    threadacts.update(conn, chat, {"project": proj})
    assert store.session(conn, chat, "claude") is not None


def test_project_without_folder(conn, src, chat):
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo, state, setup) "
                     "VALUES (?,?,?,'',?,?,'on','ready')",
                     (pid, "me/proj", str(projects.clone_path("me/proj")), db.now(), "me/proj"))
    with pytest.raises(ValueError, match="me/proj"):
        threadacts.update(conn, chat, {"project": pid})
    with pytest.raises(ValueError, match="nothere"):
        threadacts.update(conn, chat, {"project": "nothere"})
    assert row(conn, chat)["project"] is None


def test_eki_itself_is_allowed(conn, src, chat):
    threadacts.update(conn, chat, {"project": projectchats.SELF})
    assert row(conn, chat)["cwd"] == projectchats.norm(src)


def test_no_project_keeps_cwd_and_sessions(conn, proj, chat):
    threadacts.update(conn, chat, {"project": proj})
    cwd = row(conn, chat)["cwd"]
    with db.tx(conn):
        store.save_session(conn, chat, "claude", "sess-2", None)
    threadacts.update(conn, chat, {"project": None})
    t = row(conn, chat)
    assert t["project"] is None and t["cwd"] == cwd
    assert store.session(conn, chat, "claude") is not None
