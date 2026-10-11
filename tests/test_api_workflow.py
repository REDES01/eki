"""GET /api/projects/<id>/workflow: a project's workflow as a graph — the stages in order from its
workflow card, the cards that feed them, and the project's goals and items at each stage now."""
import json
import shutil
import time

import pytest

from eki import apiroutes, builds, components, workflow

ORDER = ["request", "router", "plan", "build", "self", "review", "command", "batch", "playtester",
         "merge", "after_merge"]


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def _project(conn, pid, folder, name=None):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, name or pid, str(folder), "trunk", 0, str(folder)))
    conn.commit()


def _card(root, name="workflow", **spec):
    folder = root / "components" / name
    folder.mkdir(parents=True, exist_ok=True)
    base = json.loads((components.templates_dir() / "workflow" / "component.json").read_text())
    (folder / "component.json").write_text(json.dumps({**base, **spec}))


def _add(root, template, name=None):
    shutil.copytree(components.templates_dir() / template, root / "components" / (name or template))


def graph(conn, pid="game"):
    return call(conn, "GET", f"/api/projects/{pid}/workflow")


def stages(g):
    return {s["id"]: s for s in g["stages"]}


def test_stages_in_order_with_titles_from_the_card(conn, proj):
    _card(proj, checks=["self", "review", "command", "batch", "playtester"], command="make test",
          after_merge="npm run build")
    _project(conn, "game", proj, "holo-cards")
    g = graph(conn)
    assert [s["id"] for s in g["stages"]] == ORDER
    assert [s["title"] for s in g["stages"]] == [
        "Request in", "Router", "Planner", "Build", "Self check", "Review", "Check command", "Batch",
        "Playtester", "Merge", "After-merge"]
    assert g["project"] == "game" and g["name"] == "holo-cards" and g["card"] == "workflow"
    assert g["error"] == "" and g["editable"] is True and g["eki"] is False and g["locked"] == []
    assert all(s["on"] for s in g["stages"]) and not any(s["locked"] for s in g["stages"])
    by = stages(g)
    assert by["build"]["summary"] == "4 in parallel" and by["command"]["summary"] == "make test"
    assert by["merge"]["summary"].startswith("auto") and by["after_merge"]["summary"] == "npm run build"
    assert by["router"]["keys"] == ["router", "target"] and by["command"]["keys"] == ["checks", "command"]
    assert by["playtester"]["keys"] == ["checks", "playtester"] and by["self"]["keys"] == ["checks"]
    assert by["after_merge"]["keys"] == ["after_merge"] and by["request"]["keys"] == []
    assert all(len(s["what"].split("\n")) == 2 for s in g["stages"])
    assert g["spec"]["command"] == "make test"
    assert "access" not in g["choices"] and g["choices"]["merge"] == ["auto", "pr", "person"]
    assert g["choice_help"] == components.choice_help("workflow")


def test_off_stages_are_drawn_off(conn, proj):
    _card(proj, router="off", target="fake:top", plan="single", parallel=1, merge="person")
    _project(conn, "game", proj)
    by = stages(graph(conn))
    assert by["router"]["on"] is False and by["router"]["summary"] == "straight to fake:top"
    assert by["plan"]["title"] == "Single item" and by["plan"]["on"] is True
    assert by["build"]["summary"] == "1 at a time"
    for c in ("review", "command", "batch", "playtester", "after_merge"):
        assert by[c]["on"] is False, c
    assert by["self"]["on"] and by["merge"]["on"] and by["merge"]["summary"] == "person merges"
    assert "train" not in by


def test_the_line(conn, proj):
    _card(proj)
    _project(conn, "game", proj)
    assert graph(conn)["line"] == "plan split · 4 parallel · checks: self · merge auto"
    _card(proj, router="off", target="fake:top", checks=["self", "review"])
    assert graph(conn)["line"] == "router off → fake:top · plan split · 4 parallel · checks: self, review · merge auto"


def test_cards_feed_their_stages(conn, proj):
    _card(proj, checks=["self", "command", "playtester"], command="make test")
    for t in ("knowledge", "instructions", "skills", "mcp", "serve", "playtester"):
        _add(proj, t)
    _add(proj, "agent", "critic")                        # an agent that isn't the playtester feeds nothing
    _project(conn, "game", proj)
    feeds = {f["card"]: f for f in graph(conn)["feeds"]}
    assert feeds["knowledge"] == {"card": "knowledge", "kind": "knowledge", "label": "knowledge",
                                  "to": ["plan", "build"], "note": "picked per item"}
    assert feeds["instructions"]["to"] == ["plan", "build"] and feeds["instructions"]["note"] == ""
    assert feeds["skills"]["to"] == ["build"] and feeds["mcp"]["to"] == ["build"]
    assert feeds["serve"]["to"] == ["build", "playtester"]
    assert feeds["playtester"]["kind"] == "agent" and feeds["playtester"]["to"] == ["playtester"]
    assert "critic" not in feeds and "workflow" not in feeds
    assert feeds[""] == {"card": "", "kind": "command", "label": "make test", "to": ["command"], "note": ""}


def _goal(conn, gid, state, project="game", text="make the cards shine\nmore"):
    conn.execute("INSERT INTO goals(id, text, state, created_at, project, error) VALUES (?,?,?,?,?,?)",
                 (gid, text, state, time.time(), project, "it broke" if state == "failed" else None))


def _item(conn, iid, state, gid="g0", pr_state=None):
    now = time.time()
    conn.execute("INSERT INTO items(id, goal_id, title, state, created_at, updated_at, pr_state) "
                 "VALUES (?,?,?,?,?,?,?)", (iid, gid, f"item {iid}", state, now, now, pr_state))


def test_items_sit_at_their_stage(conn, proj):
    _card(proj, checks=["self", "review"])
    _project(conn, "game", proj)
    _goal(conn, "g0", "planned")
    _goal(conn, "g1", "planning")
    _goal(conn, "g2", "failed")
    _goal(conn, "gx", "planning", project="other")       # another project's goal isn't listed
    for iid, state in (("w", "waiting"), ("b", "building"), ("l", "left"), ("j", "judging"),
                       ("r", "reviewing"), ("u", "unfit"), ("q", "queued"), ("p", "proposed"),
                       ("a", "applied"), ("d", "dropped"), ("n", "landed")):
        _item(conn, iid, state)
    _item(conn, "pr", "applied", pr_state="open")
    conn.commit()
    by = stages(graph(conn))
    ids = {k: sorted(i["id"] for i in s["items"]) for k, s in by.items()}
    assert ids["plan"] == ["g1", "g2"] and ids["build"] == ["b", "l", "w"]
    assert ids["review"] == ["j", "r"] and ids["self"] == ["u"] and ids["command"] == []
    assert ids["merge"] == ["p", "pr", "q"]                 # batch is off: merge takes the queue
    assert sum(s["count"] for s in by.values()) == 11
    assert by["plan"]["count"] == 2 and by["plan"]["alert"] and by["build"]["alert"]
    assert by["self"]["alert"] and by["merge"]["alert"] and not by["review"]["alert"]
    goal = next(i for i in by["plan"]["items"] if i["id"] == "g2")
    assert goal["kind"] == "goal" and goal["title"] == "make the cards shine" and goal["note"] == "it broke"
    assert goal["goal"] == "g2" and goal["thread"] == "" and isinstance(goal["since"], float)
    it = next(i for i in by["build"]["items"] if i["id"] == "w")
    assert it["kind"] == "item" and it["title"] == "item w" and it["state"] == "waiting"
    assert it["where"] == "for room" and it["goal"] == "g0" and it["thread"] == ""


def test_command_and_batch_take_their_items(conn, proj):
    _card(proj, checks=["self", "command", "batch"], command="make test")
    _project(conn, "game", proj)
    _goal(conn, "g0", "planned")
    for iid, state in (("j", "judging"), ("u", "unfit"), ("q", "queued"), ("s", "resolving")):
        _item(conn, iid, state)
    conn.commit()
    by = stages(graph(conn))
    assert sorted(i["id"] for i in by["command"]["items"]) == ["j", "u"]
    assert sorted(i["id"] for i in by["batch"]["items"]) == ["q", "s"] and by["self"]["count"] == 0
    assert by["command"]["alert"] and not by["batch"]["alert"]


def test_no_card_shows_the_defaults(conn, proj):
    _project(conn, "game", proj)
    g = graph(conn)
    assert g["card"] == "" and g["editable"] is False and g["error"] == ""
    assert g["spec"]["router"] == "on" and g["spec"]["checks"][0] == "self"
    assert [s["id"] for s in g["stages"]] == ORDER


def test_a_broken_card_says_why(conn, proj):
    _card(proj, parallel=0)
    _project(conn, "game", proj)
    g = graph(conn)
    assert g["card"] == "" and "parallel must be" in g["error"] and g["editable"] is False


def test_unknown_project_is_a_keyerror(conn):
    with pytest.raises(KeyError):
        graph(conn, "nope")


def test_ekis_own_project_has_train_and_its_locked_stages(conn, tmp_path, monkeypatch):
    eki = tmp_path / "eki"
    shutil.copytree(builds.source() / "components" / "workflow", eki / "components" / "workflow")
    monkeypatch.setenv("EKI_SOURCE", str(eki))
    _goal(conn, "g0", "planned", project=None)
    _item(conn, "n", "landed")
    _item(conn, "rb", "rolled back")
    conn.commit()
    g = graph(conn, "eki")
    by = stages(g)
    assert g["eki"] is True and g["name"] == "eki" and g["locked"] == list(workflow.LOCKED)
    assert [s["id"] for s in g["stages"]] == ORDER[:9] + ["train"] + ORDER[9:]
    assert by["train"]["on"] and by["train"]["locked"] and by["batch"]["locked"]
    assert not by["review"]["locked"] and by["command"]["summary"] == "bin/check"
    assert sorted(i["id"] for i in by["train"]["items"]) == ["n", "rb"] and by["train"]["alert"]


def test_no_access_on_the_workflow_kind(conn, proj):
    assert "access" not in components.choices("workflow")
    assert "access" not in components.choice_defaults("workflow")
    assert "access" not in components.choice_help("workflow")
    assert "access" in components.choices("knowledge")
    _card(proj, access="exclusive")                    # an old card with access stays good
    _project(conn, "game", proj)
    cards = {c["name"]: c for c in call(conn, "GET", "/api/projects/game/cards")["cards"]}
    assert cards["workflow"]["error"] == "" and "access" not in cards["workflow"]["choices"]
