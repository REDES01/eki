"""`eki project <name> --autonomy apply|propose`: a local project lands alone or waits for the person."""
import pytest

from eki import projects, workspace
from eki.cli import main


@pytest.fixture
def pid(conn, tmp_path):
    r = tmp_path / "chapter1"
    r.mkdir()
    (r / "a.txt").write_text("a\n")
    workspace.git(r, "init", "-q", "-b", "master")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return projects.add(conn, r)


def test_the_block_shows_apply_by_default(conn, capsys, pid):
    assert main(["project", "chapter1"]) == 0
    assert "  autonomy: apply" in capsys.readouterr().out.splitlines()


def test_the_flag_sets_and_shows_the_mode(conn, capsys, pid):
    assert main(["project", "chapter1", "--autonomy", "propose"]) == 0
    assert capsys.readouterr().out.strip() == "chapter1: autonomy propose"
    assert projects.get(conn, pid)["autonomy"] == "propose"
    assert main(["project", "chapter1"]) == 0
    assert "  autonomy: propose" in capsys.readouterr().out.splitlines()
    assert main(["project", "chapter1", "--autonomy", "apply"]) == 0
    assert projects.autonomy(projects.get(conn, pid)) == "apply"


def test_a_bad_mode_is_refused_by_argparse(conn, capsys, pid):
    with pytest.raises(SystemExit) as e:
        main(["project", "chapter1", "--autonomy", "yolo"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert projects.get(conn, pid)["autonomy"] is None


def test_a_github_row_is_refused_with_the_message(conn, capsys, pid):
    conn.execute("UPDATE projects SET repo='me/chapter1' WHERE id=?", (pid,))
    assert main(["project", "chapter1", "--autonomy", "apply"]) == 1
    assert capsys.readouterr().err.strip() == \
        "eki: me/chapter1 is on GitHub: its work goes back as pull requests"
    assert projects.get(conn, pid)["autonomy"] is None


def test_eki_itself_is_refused(conn, capsys):
    assert main(["project", "eki", "--autonomy", "propose"]) == 1
    assert "--autonomy is for your projects" in capsys.readouterr().err
