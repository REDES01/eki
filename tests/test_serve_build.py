"""A serve card's `build`: built before the first serve, again only after a source change; a failure serves nothing."""
import json
import os
import time
from pathlib import Path

import pytest

from eki import components, workspace
from eki.components import Call, agent, ports, serve, servebuild, servers


@pytest.fixture
def game(proj, home, tmp_path):
    """`proj` with an index and a source file in git, js/ ignored (built output), and a counter outside it."""
    (proj / "index.html").write_text("<h1>game</h1>\n")
    (proj / "main.ts").write_text("export const x = 1;\n")
    (proj / ".gitignore").write_text("js/\n")
    workspace.git(proj, "add", "index.html", "main.ts", ".gitignore")
    workspace.git(proj, "commit", "-q", "-m", "web")
    yield proj
    for st in servers.states():                   # never leave a real server behind
        servers.stop_pid(st.get("pid"), Path(st["state"]), st.get("name", ""))


def card(proj, name, spec, prompt=None):
    d = proj / "components" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps(spec))
    if prompt is not None:
        (d / "prompt.md").write_text(prompt)
    return components.get(name, proj)


def web(proj, tmp_path, build=None):
    count = tmp_path / "builds.txt"
    cmd = build or f"mkdir -p js && echo built > js/main.js && echo x >> {count}"
    return card(proj, "web", {"kind": "serve", "about": "the game", "path": "/g/", "build": cmd}), count


def use(comp, folder):
    return serve.call(comp, Call([], str(folder), "r0", "c1", lambda *x: None, {}))


def builds(count):
    return len(count.read_text().splitlines()) if count.exists() else 0


def test_builds_before_the_first_serve_and_only_after_a_change(game, tmp_path):
    comp, count = web(game, tmp_path)
    got = use(comp, game)
    assert not got.error and got.text.endswith("/g/")
    assert builds(count) == 1 and (game / "js" / "main.js").exists()
    assert not servebuild.changed(comp, game)                 # built output is ignored: not a change
    assert not use(comp, game).error and builds(count) == 1   # up, nothing changed: no build
    later = time.time() + 5
    os.utime(game / "main.ts", (later, later))
    assert servebuild.changed(comp, game)
    assert not use(comp, game).error and builds(count) == 2   # a source changed: built again
    assert not use(comp, game).error and builds(count) == 2


def test_the_last_build_outlives_a_restart(game, tmp_path):
    comp, count = web(game, tmp_path)
    assert not use(comp, game).error
    assert servebuild.last(components.get("web", game), game)["command"] == comp.spec["build"]
    assert not servebuild.changed(components.get("web", game), game)


def test_a_failed_build_fails_the_call_and_serves_nothing(game, tmp_path):
    comp, _ = web(game, tmp_path, build="echo broken; exit 2")
    got = use(comp, game)
    assert got.error and "broken" in got.error and "web build failed" in got.error
    assert not servers.states() and not serve.is_up(comp)
    assert serve.for_run(str(game)) == ""                    # a run never fails for its link
    url, why = serve.link_for(str(game))
    assert url == "" and "broken" in why


def test_no_build_means_none(game, tmp_path):
    comp = card(game, "web", {"kind": "serve", "about": "the game", "path": "/g/"})
    assert not servebuild.changed(comp, game) and servebuild.build(comp, game) is None
    assert not use(comp, game).error


def test_the_playtester_never_plays_a_broken_build(game, tmp_path, home):
    web(game, tmp_path, build="echo broken; exit 2")
    tester = card(game, "playtester", {"kind": "agent", "about": "plays", "bare": True,
                                       "url": "http://127.0.0.1:4200/g/"}, prompt="Play {url}.")
    c = Call(args=[], caller_dir=str(game), caller_run="r0", run_id="r1", emit=lambda k, d: None, extra={})
    with pytest.raises(ValueError, match="broken"):
        agent.play_url(tester, c)
    got = agent.call(tester, c)
    assert got.error and "broken" in got.error and "4200" not in got.error


def test_the_playtester_plays_the_built_link(game, tmp_path):
    web(game, tmp_path)
    tester = card(game, "playtester", {"kind": "agent", "about": "plays", "bare": True,
                                       "url": "http://127.0.0.1:4200/g/"}, prompt="Play {url}.")
    c = Call(args=[], caller_dir=str(game), caller_run="r0", run_id="r1", emit=lambda k, d: None, extra={})
    assert agent.play_url(tester, c) == f"http://127.0.0.1:{ports.get(game)}/g/"


@pytest.mark.parametrize("bad", [3, ["npm", "run", "build"], {"cmd": "x"}])
def test_validate_rejects_a_non_text_build(tmp_path, bad):
    errs = serve.validate({"kind": "serve", "about": "x", "build": bad}, tmp_path)
    assert errs == ['"build" must be a shell command, e.g. "npm run build"']
    assert serve.validate({"kind": "serve", "about": "x", "build": ""}, tmp_path) == []
    assert "build" in serve.FIELD_HELP
