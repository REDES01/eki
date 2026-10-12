"""Opening a folder that has no history: eki offers one (git init, .gitignore, a first commit),
or chat only where a history would fight the folder."""
import io
import json
import sys

import pytest

from eki import api_projects, gitstart, paths, projectchats, projects, workspace
from eki.cli import main


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def no_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


def tune(where):
    where.mkdir(parents=True)
    (where / "a.txt").write_text("words\n")
    with open(where / "big.wav", "wb") as f:
        f.truncate(30 * 1024 * 1024)                     # sparse: 30 MiB on paper, nothing on disk
    return where


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]


def test_yes_gives_a_plain_folder_a_history_and_leaves_the_wav_out(conn, src, tmp_path, capsys):
    where = tune(tmp_path / "tune")
    assert main(["project", "--new", "tune", "--folder", str(where), "--yes"]) == 0
    pid = projectchats.find(conn, "tune")
    assert projects.get(conn, pid)["branch"]
    files = workspace.git(where, "ls-files").split("\n")
    assert "a.txt" in files and ".gitignore" in files and "big.wav" not in files
    assert "big.wav" in (where / ".gitignore").read_text().split("\n")
    out = capsys.readouterr().out
    assert f"tune: project {pid} in " in out
    assert "1 left out of the history (offered as the project's media pool): big.wav" in out
    assert projectchats.left_out(conn, pid) == ["big.wav"]


def test_icloud_is_refused_with_chat_only_and_chat_only_works(conn, src, capsys):
    where = tune(paths.user_home() / "Library/Mobile Documents/com~apple~CloudDocs/song")
    assert main(["project", "--new", "song", "--folder", str(where), "--yes"]) == 1
    said = capsys.readouterr().err
    assert "iCloud Drive" in said and "Chat only" in said
    assert count(conn) == 0 and not (where / ".git").exists()
    assert main(["project", "--new", "song", "--folder", str(where), "--chat-only"]) == 0
    pid = projectchats.find(conn, "song")
    assert projects.get(conn, pid)["branch"] == "" and not (where / ".git").exists()


def test_a_git_folder_with_no_commit_gets_its_first(conn, src, tmp_path):
    where = tmp_path / "empty"
    where.mkdir()
    (where / "notes.md").write_text("hi\n")
    workspace.git(where, "init", "-q", "-b", "trunk")
    assert main(["project", "--new", "empty", "--folder", str(where), "--yes"]) == 0
    assert workspace.git(where, "log", "-1", "--format=%s") == gitstart.FIRST_COMMIT
    assert projects.get(conn, projectchats.find(conn, "empty"))["branch"] == "trunk"


def test_without_an_answer_and_no_terminal_nothing_is_made(conn, src, tmp_path, capsys, no_tty):
    where = tune(tmp_path / "tune")
    assert main(["project", "--new", "tune", "--folder", str(where)]) == 1
    got = capsys.readouterr()
    assert gitstart.OFFER in got.out and "big.wav" in got.out
    assert "run again with --yes, or --chat-only" in got.err
    assert count(conn) == 0 and not (where / ".git").exists()


def test_on_a_terminal_it_asks_once(conn, src, tmp_path, capsys, monkeypatch):
    where = tune(tmp_path / "tune")
    monkeypatch.setattr(sys, "stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})())
    asked = []
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or "y")
    assert main(["project", "--new", "tune", "--folder", str(where)]) == 0
    assert asked == ["Keep a history? [y/N] "] and (where / ".git").is_dir()


def test_yes_and_chat_only_together_is_an_error(conn, src, tmp_path, capsys):
    where = tune(tmp_path / "tune")
    assert main(["project", "--new", "tune", "--folder", str(where), "--yes", "--chat-only"]) == 1
    assert main(["project", "--chat-only"]) == 1
    assert count(conn) == 0


def test_the_api_asks_then_makes_it(conn, src):
    where = tune(paths.user_home() / "tune")
    got = api_projects.create(conn, {"name": "tune", "folder": "~/tune"})
    assert got["ask"]["state"] == "plain" and got["ask"]["fit"] and got["ask"]["left_out"] == ["big.wav"]
    json.dumps(got)
    assert count(conn) == 0 and not (where / ".git").exists()
    made = api_projects.create(conn, {"name": "tune", "folder": "~/tune", "history": "start"})
    assert made["left_out"] == ["big.wav"] and made["folder"] == projectchats.norm(where)
    assert projects.get(conn, made["id"])["branch"]


def test_the_api_refuses_an_unfit_folder_with_chat_only(conn, src):
    tune(paths.user_home() / "Library/Mobile Documents/com~apple~CloudDocs/song")
    body = {"name": "song", "folder": "~/Library/Mobile Documents/com~apple~CloudDocs/song"}
    with pytest.raises(ValueError, match="iCloud Drive"):
        api_projects.create(conn, {**body, "history": "start"})
    made = api_projects.create(conn, {**body, "history": "none"})
    assert made["left_out"] == [] and projects.get(conn, made["id"])["branch"] == ""


def test_add_says_how_to_give_a_folder_a_history(conn, src, tmp_path):
    where = tune(tmp_path / "tune")
    with pytest.raises(ValueError, match="has no history yet"):
        projects.add(conn, where)
    icloud = tune(paths.user_home() / "Library/Mobile Documents/com~apple~CloudDocs/song")
    with pytest.raises(ValueError, match="Chat only"):
        projects.add(conn, icloud)
