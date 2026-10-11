"""Adding a workflow card writes today's settings into it, once; a second card is refused."""
import json
import re
from pathlib import Path

import pytest

from eki import api_cardadd, github, projects, workflow
from eki.cli import main


def add(conn, pid, template="workflow"):
    return api_cardadd.add_card(conn, re.match(r"(.+)", pid), {}, {"template": template})


def spec(root: Path, name="workflow") -> dict:
    return json.loads((root / "components" / name / "component.json").read_text())


def test_a_local_project_carries_its_check_and_autonomy(conn, proj):
    pid = projects.add(conn, proj, check="make test")
    projects.set_autonomy(conn, pid, "propose")
    got = add(conn, pid)
    s = spec(proj)
    assert s["checks"] == ["self", "review", "command", "batch"] and s["command"] == "make test"
    assert s["merge"] == "person" and s["kind"] == "workflow"
    assert "from the project: check `make test`, review on, autonomy propose → merge person" in got["said"]
    assert got["card"]["name"] == "workflow"
    p = projects.get(conn, pid)
    assert projects.autonomy(p) == "propose"
    assert json.loads(projects.check_argv(p))[2] == "make test"
    conn.execute("UPDATE projects SET check_cmd='old', autonomy='apply' WHERE id=?", (pid,))
    p = projects.get(conn, pid)                         # the old columns stay, unread while the card exists
    assert projects.autonomy(p) == "propose" and json.loads(projects.check_argv(p))[2] == "make test"


def test_a_project_with_no_check_merges_auto(conn, proj):
    pid = projects.add(conn, proj)
    add(conn, pid)
    s = spec(proj)
    assert s["checks"][0] == "self" and set(s["checks"]) <= {"self", "review"}
    assert s["merge"] == "auto" and "command" not in s
    assert projects.check_argv(projects.get(conn, pid)) is None


def test_a_github_row_merges_by_pull_request(conn, proj, monkeypatch):
    pid = projects.add(conn, proj)
    conn.execute("UPDATE projects SET repo='o/r', folder=? WHERE id=?", (str(proj), pid))
    monkeypatch.setattr(github, "path_of", lambda path: ("o/r", "GitHub o/r"))
    got = add(conn, pid)
    assert spec(proj)["merge"] == "pr"
    assert any("GitHub o/r → merge pr" in line for line in got["said"])


def test_a_second_workflow_card_is_refused_and_writes_nothing(conn, proj):
    pid = projects.add(conn, proj)
    add(conn, pid)
    before = sorted(p.name for p in (proj / "components").iterdir())
    with pytest.raises(ValueError, match="a project has one workflow card: workflow is it"):
        add(conn, pid)
    assert sorted(p.name for p in (proj / "components").iterdir()) == before


def test_a_card_that_does_not_validate_is_taken_back(conn, proj, monkeypatch):
    pid = projects.add(conn, proj)
    conn.execute("UPDATE projects SET repo='o/r', folder=? WHERE id=?", (str(proj), pid))   # merge pr, but no GitHub origin
    monkeypatch.setattr(github, "path_of", lambda path: (None, "origin isn't on GitHub"))
    with pytest.raises(ValueError, match="pr needs a GitHub repo"):
        add(conn, pid)
    assert not (proj / "components" / "workflow").exists()


def test_the_cli_does_the_same(conn, proj, capsys):
    pid = projects.add(conn, proj, check="make test")
    assert main(["component", "add", "workflow", "-C", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "from the project: check `make test`" in out and "merge auto" in out
    s = spec(proj)
    assert s["command"] == "make test" and s["merge"] == "auto" and "batch" in s["checks"]
    assert workflow.of_project(conn, projects.get(conn, pid)).command == "make test"
    assert main(["component", "add", "workflow", "--name", "flow", "-C", str(proj)]) == 1
    assert "a project has one workflow card: workflow is it" in capsys.readouterr().err
    assert not (proj / "components" / "flow").exists()
