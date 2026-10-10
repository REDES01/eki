"""Chats inside a project: a new chat runs in the project's folder, and keeps it."""
import pytest

from eki import api, asking, db, projectchats, projects, store, workspace
from eki.cli import ask as ask_cli
from eki.cli import main


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


def test_new_chat_in_project_runs_in_its_folder(conn, proj):
    tid, rid = asking.submit(conn, "hello", project=proj)
    t = row(conn, tid)
    assert t["project"] == proj
    assert t["cwd"] == projectchats.folder_of(conn, proj)


def test_eki_itself_is_a_project(conn, src):
    tid, _ = asking.submit(conn, "hello", project=projectchats.SELF)
    assert row(conn, tid)["project"] == "eki"
    assert row(conn, tid)["cwd"] == projectchats.norm(src)


def test_project_without_folder_says_how(conn, src):
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo, state, setup) "
                     "VALUES (?,?,?,'',?,?,'on','ready')",
                     (pid, "me/proj", str(projects.clone_path("me/proj")), db.now(), "me/proj"))
    with pytest.raises(ValueError, match=r"me/proj has no folder yet: set one on its page or "
                                         r"eki project me/proj --folder <path>"):
        asking.submit(conn, "hello", project=pid)
    assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 0


def test_project_thread_keeps_its_folder(conn, proj, tmp_path):
    tid, _ = asking.submit(conn, "hello", project=proj)
    folder = row(conn, tid)["cwd"]
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(ValueError, match=r"this chat lives in tokenbar \("):
        asking.submit(conn, "again", thread=tid, cwd=str(other))
    asking.submit(conn, "again", thread=tid, cwd=folder + "/")
    asking.submit(conn, "and again", thread=tid)
    assert row(conn, tid)["cwd"] == folder
    assert len(store.thread_runs(conn, tid)) == 3


def test_new_project_chat_refuses_another_cwd(conn, proj, tmp_path):
    with pytest.raises(ValueError, match="this chat lives in tokenbar"):
        asking.submit(conn, "hello", project=proj, cwd=str(tmp_path))


def test_chats_without_project_unchanged(conn, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    tid, _ = asking.submit(conn, "hello", cwd=str(a))
    assert row(conn, tid)["project"] is None and row(conn, tid)["cwd"] == str(a)
    asking.submit(conn, "again", thread=tid, cwd=str(b))
    assert row(conn, tid)["cwd"] == str(b)
    tid2, _ = asking.submit(conn, "plain")
    assert row(conn, tid2)["cwd"] is None and row(conn, tid2)["project"] is None


def test_api_carries_project(conn, proj):
    got = api.ask(conn, {"prompt": "hello", "project": proj})
    plain = api.ask(conn, {"prompt": "plain"})
    listed = {t["id"]: t for t in api.threads(conn)}
    assert listed[got["thread"]]["project"] == {"id": proj, "name": "tokenbar"}
    assert listed[plain["thread"]]["project"] is None
    assert api.thread(conn, got["thread"])["project"] == {"id": proj, "name": "tokenbar"}
    assert api.thread(conn, plain["thread"])["project"] is None


def test_cli_ask_resolves_project_by_name(conn, proj, monkeypatch, capsys):
    monkeypatch.setattr(ask_cli, "ensure_engine", lambda quiet=False: None)
    assert main(["ask", "--bg", "-p", "tokenbar", "hello"]) == 0
    rid = capsys.readouterr().out.strip()
    t = conn.execute("SELECT t.* FROM threads t JOIN runs r ON r.thread_id=t.id WHERE r.id=?",
                     (rid,)).fetchone()
    assert t["project"] == proj
    assert main(["ask", "--bg", "-p", "nosuch", "hello"]) == 1
    assert "no project nosuch" in capsys.readouterr().err
