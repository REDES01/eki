import pytest

from eki import projects, workspace


@pytest.fixture
def pid(conn, tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "a.txt").write_text("a\n")
    workspace.git(r, "init", "-q", "-b", "master")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return projects.add(conn, r)


def test_a_local_project_applies_by_default(conn, pid):
    p = projects.get(conn, pid)
    assert p["autonomy"] is None and projects.autonomy(p) == "apply"


def test_both_modes_are_written(conn, pid):
    projects.set_autonomy(conn, pid, "propose")
    assert projects.autonomy(projects.get(conn, pid)) == "propose"
    projects.set_autonomy(conn, pid, "apply")
    p = projects.get(conn, pid)
    assert p["autonomy"] == "apply" and projects.autonomy(p) == "apply"


def test_a_bad_mode_is_refused_with_the_modes(conn, pid):
    with pytest.raises(ValueError) as e:
        projects.set_autonomy(conn, pid, "yolo")
    assert "apply" in str(e.value) and "propose" in str(e.value)
    assert projects.get(conn, pid)["autonomy"] is None


def test_a_github_project_proposes_and_refuses_apply(conn, pid):
    conn.execute("UPDATE projects SET repo='me/proj' WHERE id=?", (pid,))
    p = projects.get(conn, pid)
    assert projects.autonomy(p) == "propose"
    conn.execute("UPDATE projects SET autonomy='apply' WHERE id=?", (pid,))
    assert projects.autonomy(projects.get(conn, pid)) == "propose"
    with pytest.raises(ValueError, match="me/proj is on GitHub: its work goes back as pull requests"):
        projects.set_autonomy(conn, pid, "apply")


def test_items_have_a_landed_on_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    assert "landed_on" in cols
