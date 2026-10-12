"""The project page and `eki project <name>` say how a local project's work lands."""
import pytest

from eki import api_projects, projectchats, projects, workspace
from eki.cli import main


@pytest.fixture
def folder(tmp_path):
    r = tmp_path / "chapter1"
    r.mkdir()
    (r / "a.txt").write_text("a\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def test_a_local_project_lands_on_its_branch_and_never_pushes(conn, folder):
    pid = projects.add(conn, folder)
    line = api_projects.show(conn, pid)["landing"]
    assert line == (f"eki lands finished work on main in your folder ({folder.resolve()}), "
                    "fast-forward only, and never pushes a local project.")


def test_a_propose_project_leaves_branches_to_merge(conn, folder):
    pid = projects.add(conn, folder)
    projects.set_autonomy(conn, pid, "propose")
    assert api_projects.show(conn, pid)["landing"] == (
        "eki leaves each change on an eki/<id> branch for you to merge into main; "
        "it never pushes a local project.")


def test_a_github_remote_stays_local(conn, folder):
    workspace.git(folder, "remote", "add", "origin", "git@github.com:o/r.git")
    pid = projects.add(conn, folder)
    line = api_projects.show(conn, pid)["landing"]
    assert "never pushes a local project." in line
    assert line.endswith(" It has a GitHub remote (o/r), but stays local: eki opens pull requests "
                         "only if you choose them for this project.")


def test_no_branch_yet_says_when_it_will_land(conn, folder):
    pid = projects.add(conn, folder)
    conn.execute("UPDATE projects SET branch='' WHERE id=?", (pid,))
    assert api_projects.landing_line(projects.get(conn, pid)).startswith("No background work yet:")


def test_a_github_row_and_eki_itself_have_none(conn, folder):
    pid = projects.add(conn, folder)
    conn.execute("UPDATE projects SET repo='o/r' WHERE id=?", (pid,))
    assert api_projects.landing_line(projects.get(conn, pid)) is None
    assert api_projects.show(conn, projectchats.SELF)["landing"] is None


def test_the_cli_says_it(conn, capsys, folder):
    projects.add(conn, folder)
    assert main(["project", "chapter1"]) == 0
    out = capsys.readouterr().out
    assert "  lands: eki lands finished work on main" in out
    assert "never pushes a local project." in out


def test_the_cli_says_nothing_of_it_for_a_github_row_or_eki(conn, capsys, folder):
    pid = projects.add(conn, folder)
    conn.execute("UPDATE projects SET repo='o/r' WHERE id=?", (pid,))
    assert main(["project", "o/r"]) == 0
    assert "lands:" not in capsys.readouterr().out
    assert main(["project", "eki"]) == 0
    assert "lands:" not in capsys.readouterr().out
