"""A folder with nowhere to land work (a detached HEAD, no commit yet) gets an offer, not a refusal."""
import io

import pytest

from eki import projects, workspace
from eki.cli import main


@pytest.fixture
def detached(tmp_path):
    r = tmp_path / "detached"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    workspace.git(r, "checkout", "-q", "--detach")
    return r.resolve()


@pytest.fixture
def unborn(tmp_path):
    r = tmp_path / "unborn"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    (r / "notes.md").write_text("notes\n")
    workspace.git(r, "init", "-q", "-b", "start")
    return r.resolve()


def branch_of(where):
    return workspace.git(where, "symbolic-ref", "--short", "-q", "HEAD", check=False)


def test_a_detached_head_gets_the_branch_offer(conn, detached):
    with pytest.raises(projects.NeedsBranch) as e:
        projects.add(conn, detached)
    assert e.value.offer == "branch" and e.value.folder == str(detached)
    assert str(e.value) == (f"{detached} isn't on a branch (a detached HEAD), so eki has nowhere to land work. "
                            "eki can make a branch 'main' here at this commit.")
    assert projects.all(conn) == [] and branch_of(detached) == ""


def test_saying_yes_makes_main_at_the_same_commit(conn, detached):
    sha = workspace.head(detached)
    pid = projects.add(conn, detached, fix=True)
    assert branch_of(detached) == "main" and workspace.head(detached) == sha
    assert projects.get(conn, pid)["branch"] == "main"
    assert workspace.git(detached, "status", "--porcelain") == ""


def test_main_taken_makes_main_2(conn, detached):
    workspace.git(detached, "branch", "main")
    pid = projects.add(conn, detached, fix=True)
    assert branch_of(detached) == "main-2" and projects.get(conn, pid)["branch"] == "main-2"


def test_an_explicit_branch_on_a_detached_head_needs_no_offer(conn, detached):
    pid = projects.add(conn, detached, branch="trunk")
    assert projects.get(conn, pid)["branch"] == "trunk" and branch_of(detached) == ""


def test_no_commit_yet_gets_the_commit_offer(conn, unborn):
    with pytest.raises(projects.NeedsBranch) as e:
        projects.add(conn, unborn)
    assert e.value.offer == "commit"
    assert str(e.value) == f"{unborn} has no commit yet. eki can commit the folder as it is, so work has a starting point."
    assert projects.all(conn) == []


def test_saying_yes_commits_the_folder_as_it_was(conn, unborn):
    pid = projects.add(conn, unborn, fix=True)
    assert workspace.git(unborn, "rev-list", "--count", "HEAD") == "1"
    assert workspace.git(unborn, "log", "-1", "--format=%s") == projects.FIRST
    assert workspace.git(unborn, "ls-files").split() == ["app.py", "notes.md"]
    assert projects.get(conn, pid)["branch"] == "start" and branch_of(unborn) == "start"


def test_cli_yes_takes_the_offer(conn, capsys, detached):
    assert main(["goal", "add", str(detached), "keep it small", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "branch main" in out and branch_of(detached) == "main"


def test_cli_without_yes_and_no_tty_prints_the_offer(conn, capsys, monkeypatch, unborn):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["goal", "add", str(unborn), "keep it small"]) == 1
    said = capsys.readouterr().err
    assert "has no commit yet" in said
    assert f'Say yes: eki goal add {unborn} "keep it small" --yes' in said
    assert projects.all(conn) == [] and workspace.git(unborn, "rev-parse", "-q", "--verify", "HEAD", check=False) == ""


def test_cli_at_a_terminal_asks_and_goes_on_with_y(conn, capsys, monkeypatch, detached):
    tty = io.StringIO("y\n")
    tty.isatty = lambda: True
    monkeypatch.setattr("sys.stdin", tty)
    assert main(["goal", "add", str(detached), "keep it small"]) == 0
    assert "Do it? [y/N]" in capsys.readouterr().out and branch_of(detached) == "main"
