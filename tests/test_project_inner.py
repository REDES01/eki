"""A project folder inside a bigger git repo gets its own repo (one project, one repo): at once when the outer
repo tracks nothing there, after one warning when it does (projectchats.create, gitstart.outer)."""
import os

import pytest

from eki import gitstart, projectchats, projectstart, workspace
from eki.cli import main


@pytest.fixture
def src(tmp_path, monkeypatch):
    """eki's own source, so a project is never mistaken for it."""
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def outer(tmp_path):
    """A repo with one tracked folder `kept/` and one it ignores, `loose/`."""
    top = tmp_path / "outer"
    (top / "kept").mkdir(parents=True)
    (top / "kept" / "notes.txt").write_text("hi\n")
    (top / "loose").mkdir()
    (top / "loose" / "draft.txt").write_text("draft\n")
    (top / ".gitignore").write_text("loose/\n")
    workspace.git(top, "init", "-q", "-b", "main")
    workspace.git(top, "add", "-A")
    workspace.git(top, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "outer")
    return top


def own_top(where):
    return os.path.realpath(workspace.repo_of(where)) == os.path.realpath(where)


def commits(where):
    return workspace.git(where, "rev-list", "--count", "HEAD", check=False)


def test_outer_says_where_and_whether_it_tracks(outer, tmp_path):
    assert gitstart.outer(outer / "kept") == (workspace.repo_of(outer), True)
    assert gitstart.outer(outer / "loose") == (workspace.repo_of(outer), False)
    assert gitstart.outer(outer) is None and gitstart.outer(tmp_path / "nowhere") is None


def test_a_folder_the_outer_repo_tracks_nothing_of_becomes_its_own_repo_at_once(conn, src, outer):
    pid = projectchats.create(conn, "loose", outer / "loose")
    assert own_top(outer / "loose") and (outer / "loose" / ".git").is_dir()
    assert projectchats.folder_of(conn, pid) == projectchats.norm(outer / "loose")
    assert (outer / "loose" / "draft.txt").read_text() == "draft\n"


def test_a_tracked_folder_needs_the_warning_and_then_has_one_commit(conn, src, outer):
    with pytest.raises(gitstart.Embedded) as e:
        projectchats.create(conn, "kept", outer / "kept")
    assert str(e.value) == (f"This folder is part of the git repository at {workspace.repo_of(outer)}. eki will "
                            "start a separate history here; the outer repository will see this folder as changed "
                            "and git may call it an embedded repository.")
    assert not (outer / "kept" / ".git").exists()
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    pid = projectchats.create(conn, "kept", outer / "kept", anyway=True)
    assert own_top(outer / "kept") and commits(outer / "kept") == "1"
    assert workspace.git(outer / "kept", "ls-files") == "notes.txt"
    row = conn.execute("SELECT branch FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["branch"] == workspace.git(outer / "kept", "symbolic-ref", "--short", "HEAD")


def test_the_window_asks_and_cancel_leaves_no_git(conn, src, outer):
    got = projectstart.create(conn, {"name": "kept", "folder": str(outer / "kept")})
    assert got["id"] is None and got["ask"]["inner"] and "embedded repository" in got["ask"]["offer"]
    assert not (outer / "kept" / ".git").exists()                     # Cancel: nothing more is posted
    made = projectstart.create(conn, {"name": "kept", "folder": str(outer / "kept"), "history": "anyway"})
    assert made["id"] and commits(outer / "kept") == "1"


def test_cli_prints_the_warning_and_needs_yes(conn, src, outer, capsys):
    assert main(["project", "--new", "kept", "--folder", str(outer / "kept")]) == 1
    said = capsys.readouterr().err
    assert "This folder is part of the git repository at" in said and "--yes" in said
    assert not (outer / "kept" / ".git").exists()
    assert main(["project", "--new", "kept", "--folder", str(outer / "kept"), "--yes"]) == 0
    assert commits(outer / "kept") == "1"


def test_cli_yes_goes_only_with_rm_or_new(conn, src, capsys):
    assert main(["project", "--yes"]) == 1
