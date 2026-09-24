# SPDX-License-Identifier: Apache-2.0
"""A folder with `.eki/` in it is a project; calls made inside it belong to it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from eki import cli, produce, projects
from eki.engine import Engine
from eki.runs import RunStore

from test_runs import echo_config, settle


@pytest.fixture(autouse=True)
def private_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("EKI_POLICY", str(tmp_path / "policy.json"))
    monkeypatch.delenv("EKI_DEPTH", raising=False)
    monkeypatch.delenv("EKI_RUN", raising=False)


def test_the_nearest_marker_up_is_the_project_and_none_without_one(tmp_path):
    game = tmp_path / "game"
    deep = game / "src" / "npc"
    deep.mkdir(parents=True)
    assert projects.find(str(deep)) is None                 # a folder, not a project yet
    (game / ".eki").mkdir()
    assert projects.find(str(deep)) == game.resolve()
    assert projects.find(str(game)) == game.resolve()
    (game / "src" / "npc" / "a.txt").write_text("x")
    assert projects.find(str(deep / "a.txt")) == game.resolve()
    # a project inside a project: the inner one
    (deep / ".eki").mkdir()
    assert projects.find(str(deep)) == deep.resolve()
    assert projects.find("") is None


def test_home_is_never_a_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".eki").mkdir(parents=True)                     # eki's own store, not a marker
    (home / "code").mkdir()
    monkeypatch.setattr(projects, "_home", lambda: home.resolve())
    assert projects.find(str(home / "code")) is None
    assert projects.find(str(home)) is None
    with pytest.raises(ValueError):
        projects.init(str(home))


def test_init_makes_the_marker_names_it_and_keeps_the_name(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    made = projects.init(str(game))
    assert made["made"] and made["name"] == "game"
    assert json.loads((game / ".eki" / "project.json").read_text()) == {"name": "game"}
    again = projects.init(str(game), "Harbor RPG")
    assert not again["made"] and again["name"] == "Harbor RPG"
    assert projects.init(str(game))["name"] == "Harbor RPG"    # init again doesn't rename
    (game / "sub").mkdir()
    assert projects.describe(str(game / "sub")) == {"root": str(game.resolve()), "name": "Harbor RPG"}


def test_the_run_store_keeps_a_project_and_lists_one_projects_runs(tmp_path):
    store = RunStore(tmp_path / "runs.db", owner=True)
    a = store.create("in the game", project="/p/game")
    store.create("elsewhere")
    assert [r["id"] for r in store.recent(10, project="/p/game")] == [a]
    assert len(store.recent(10)) == 2
    assert store.get(a)["project"] == "/p/game"
    store.close()


@pytest.mark.asyncio
async def test_a_call_made_inside_a_project_belongs_to_it_and_its_agents_calls_too(tmp_path):
    game = tmp_path / "game"
    (game / "src").mkdir(parents=True)
    projects.init(str(game))
    root = str(game.resolve())
    eng = Engine(echo_config(tmp_path), owner=True)

    mine = await eng.ask("ping", where=str(game / "src"))
    assert eng.runs.get(mine["run"])["project"] == root
    await settle(eng.runs, mine["run"])

    # a repo it's given decides, not where the shell stood
    given = await eng.ask("ping", repo=str(game), where=str(tmp_path))
    assert eng.runs.get(given["run"])["project"] == root
    await settle(eng.runs, given["run"])

    # an agent's call from a folder of no project: the project of the run asking
    step = await eng.ask("a step", via="agent", depth=1, parent_run=mine["run"], where=str(tmp_path))
    assert eng.runs.get(step["run"])["project"] == root
    await settle(eng.runs, step["run"])

    # …even from a copy of the folder, whose .eki/ came along with it
    copy = tmp_path / "copy"
    (copy / ".eki").mkdir(parents=True)
    step = await eng.ask("a step", via="agent", depth=1, parent_run=mine["run"], where=str(copy))
    assert eng.runs.get(step["run"])["project"] == root
    await settle(eng.runs, step["run"])

    outside = await eng.ask("ping", where=str(tmp_path))
    assert eng.runs.get(outside["run"])["project"] == ""
    await settle(eng.runs, outside["run"])

    # a retry stays in the project
    eng.runs.update(mine["run"], state="failed")
    again = await eng.retry(mine["run"])
    assert eng.runs.get(again["run"])["project"] == root
    await settle(eng.runs, again["run"])
    await eng.runner.stop()


def test_the_command_line_says_where_it_was_called_from(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = {}
    monkeypatch.setattr(cli, "call", lambda method, path, service, **kw: sent.update(kw.get("json") or {})
                        or {"run": "r1", "conversation": "c1"})
    for k in ("EKI_PARENT", "EKI_INSIDE"):
        monkeypatch.delenv(k, raising=False)
    cli.main(["ask", "hello", "--detach", "--repo", "."])
    assert Path(sent["where"]).resolve() == tmp_path.resolve()
    assert Path(sent["repo"]).resolve() == tmp_path.resolve()   # `.` is the caller's folder
    assert produce.located({"where": "/x"})["where"] == "/x"    # a folder given stays


def test_eki_project_init_and_show(tmp_path, monkeypatch, capsys):
    game = tmp_path / "game"
    game.mkdir()
    asked = {}
    monkeypatch.setattr(cli, "call", lambda method, path, service, **kw: asked.update(kw.get("params") or {})
                        or [{"id": "r1", "state": "done", "backend": "echo", "prompt": "draw a map"}])
    assert cli.main(["project", "show", str(game)]) == 1           # not one yet
    assert cli.main(["project", "init", str(game), "--name", "Harbor"]) == 0
    assert cli.main(["project", "show", str(game)]) == 0
    out = capsys.readouterr().out
    assert "Harbor" in out and "draw a map" in out
    assert asked["project"] == str(game.resolve())
