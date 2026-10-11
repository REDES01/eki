"""The workflow card: the kind's checks, the reader everything goes through, and the migration."""
import json
import shutil
from pathlib import Path

import pytest

from eki import api_cardedit, builds, components, projects, workflow
from eki.api_settings import Invalid


def card(root: Path, name="workflow", **spec) -> Path:
    folder = root / "components" / name
    folder.mkdir(parents=True, exist_ok=True)
    base = json.loads((components.templates_dir() / "workflow" / "component.json").read_text())
    (folder / "component.json").write_text(json.dumps({**base, **spec}))
    return folder


def why(folder: Path) -> str:
    with pytest.raises(ValueError) as e:
        components.load(folder, "project")
    return str(e.value)


def test_the_template_loads_and_validates(proj):
    comp = components.load(card(proj), "project")
    assert comp.kind == "workflow" and "workflow" in components.PASSIVE
    assert components.choices("workflow")["merge"] == ["auto", "pr", "person"]
    assert components.choice_help("workflow")["router"]["off"].startswith("Everything goes to target")
    assert components.entries({"workflow": comp}, [])[0]["notes"] == ["checks: self only"]
    from eki.components import workflow as kind
    assert "engine" in kind.call(comp, None).error


@pytest.mark.parametrize("spec, reason", [
    ({"parallel": 0}, "parallel must be a whole number"),
    ({"router": "maybe"}, "router must be one of on, off"),
    ({"checks": []}, "self is always on"),
    ({"checks": ["self", "self"]}, "each check once"),
    ({"checks": ["review", "self"]}, "checks run in this order"),
    ({"checks": ["self", "lint"]}, "lint is not a check"),
    ({"checks": ["self", "command"]}, "command is required"),
    ({"checks": ["self", "playtester"], "playtester": "Bad Name"}, "playtester must be the name of a card"),
    ({"router": "off"}, "target is required when router is off"),
    ({"merge": "pr"}, "pr needs a GitHub repo"),
    ({"checks": ["self", "train"]}, "only eki's project has it"),
])
def test_bad_values_are_refused_with_the_reason(proj, spec, reason):
    assert reason in why(card(proj, **spec))


def test_good_values_pass(proj):
    comp = components.load(card(proj, router="off", target="fake:top", plan="single", parallel=2,
                                checks=["self", "command", "batch", "playtester"], command="make test"),
                           "project")
    wf = workflow._of(comp)
    assert wf.target == "fake:top" and wf.checks == ("self", "command", "batch", "playtester")
    assert workflow.said(wf) == "router off → fake:top; plan single; checks: self, command, batch, playtester"


def test_a_second_workflow_card_is_broken(proj):
    card(proj)
    card(proj, "flow")
    found, errors = components.own(proj)
    assert found["flow"].kind == "workflow" and "workflow" not in found
    assert "a project has one workflow card: flow is it" in dict(errors)[str(proj / "components" / "workflow")]
    assert workflow.of_root(proj).card == str(proj / "components" / "flow")


def test_eki_refuses_a_card_without_train_and_accepts_its_own(tmp_path, monkeypatch):
    committed = builds.source() / "components" / "workflow"
    comp = components.load(committed, "project")
    assert comp.spec["checks"] == ["self", "review", "batch", "train"] and comp.spec["merge"] == "auto"
    eki = tmp_path / "eki"
    shutil.copytree(committed, eki / "components" / "workflow")
    monkeypatch.setenv("EKI_SOURCE", str(eki))
    folder = eki / "components" / "workflow"
    before = (folder / "component.json").read_text()
    with pytest.raises(Invalid) as e:
        api_cardedit.save_settings(folder, {"checks": ["self", "review", "batch"]})
    assert "train protects eki itself: it can't be turned off here" in str(e.value)
    with pytest.raises(Invalid) as e:
        api_cardedit.save_settings(folder, {"checks": ["self", "train"]})
    assert "batch protects eki itself" in str(e.value)
    assert (folder / "component.json").read_text() == before
    api_cardedit.save_settings(folder, {"checks": ["self", "batch", "train"]})
    assert workflow.of_project(None, None).checks == ("self", "batch", "train")


def test_no_card_is_none(conn, proj):
    pid = projects.add(conn, proj)
    assert workflow.of_root(proj) is None and workflow.of_root(None) is None
    assert workflow.of_project(conn, projects.get(conn, pid)) is None
    assert workflow.of_folder(conn, str(proj)) is None and workflow.of_folder(conn, None) is None
    assert not workflow.has(None, "self")


def test_the_reader_finds_the_card(conn, proj):
    pid = projects.add(conn, proj)
    card(proj, checks=["self", "review"])
    wf = workflow.of_project(conn, projects.get(conn, pid))
    assert wf == workflow.of_folder(conn, str(proj)) == workflow.of_root(proj)
    assert workflow.has(wf, "review") and not workflow.has(wf, "batch")
    assert workflow.said(workflow.of_root(proj)) == "checks: self, review"


def test_said_names_self_only(proj):
    card(proj)
    assert workflow.said(workflow.of_root(proj)) == "checks: self only"


def test_a_broken_card_reads_as_none(proj):
    card(proj, parallel="lots")
    assert workflow.of_root(proj) is None


def test_from_settings_maps_todays_settings(conn, proj):
    pid = projects.add(conn, proj, check="make test")
    got = workflow.from_settings(conn, projects.get(conn, pid))
    assert got == {"router": "on", "plan": "split", "parallel": 4, "checks": ["self", "review", "command", "batch"],
                   "command": "make test", "merge": "auto"}
    projects.set_autonomy(conn, pid, "propose")
    conn.execute("UPDATE projects SET check_cmd=NULL WHERE id=?", (pid,))
    got = workflow.from_settings(conn, projects.get(conn, pid))
    assert got["merge"] == "person" and got["checks"] == ["self", "review"] and "command" not in got
    check = proj / "bin" / "check"
    check.parent.mkdir()
    check.write_text("#!/bin/sh\n")
    check.chmod(0o755)
    assert workflow.from_settings(conn, projects.get(conn, pid))["command"] == "bin/check"
    conn.execute("UPDATE projects SET repo='o/r' WHERE id=?", (pid,))
    got = workflow.from_settings(conn, projects.get(conn, pid))
    assert got["merge"] == "pr" and "command" not in got        # a clone: the person's word only
    eki = workflow.from_settings(conn, None)
    assert eki["checks"] == ["self", "review", "batch", "train"] and eki["merge"] == "person"
    folder = card(proj, **{k: v for k, v in workflow.from_settings(conn, projects.get(conn, pid)).items()
                           if k != "merge"})
    assert components.load(folder, "project")


def test_projects_follow_a_card_and_are_unchanged_without(conn, proj):
    pid = projects.add(conn, proj, check="make test")
    p = projects.get(conn, pid)
    assert projects.autonomy(p) == "apply"
    assert json.loads(projects.check_argv(p)) == ["/bin/sh", "-c", "make test", "eki-judge"]
    card(proj, merge="person")
    assert projects.autonomy(p) == "propose" and projects.check_argv(p) is None
    card(proj, merge="auto", checks=["self", "command"], command="npm test")
    assert projects.autonomy(p) == "apply"
    assert json.loads(projects.check_argv(p)) == ["/bin/sh", "-c", "npm test", "eki-judge"]
    conn.execute("UPDATE projects SET repo='o/r' WHERE id=?", (pid,))
    assert projects.autonomy(projects.get(conn, pid)) == "propose"
