"""A card's overview: Used by (stages, recent runs, one line) and Right now, from runs and component state."""
import json
import shutil
import time

import pytest

from eki import cardusedby, cardview, components, db, store
from eki.components import servers


@pytest.fixture(autouse=True)
def noon(monkeypatch):
    """"today" starts 12 hours ago: the runs these tests make minutes ago are today even
    when the suite runs just after midnight (cardusedby._midnight is the wall clock's)."""
    monkeypatch.setattr(cardusedby, "_midnight", lambda now: now - 12 * 3600)


@pytest.fixture
def root(conn, proj):
    r = proj.resolve()
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 ("game", "game", str(r), "trunk", 0, str(r)))
    conn.commit()
    return r


def card(root, template, name=None, **spec):
    d = root / "components" / (name or template)
    shutil.copytree(components.templates_dir() / template, d)
    if spec:
        f = d / "component.json"
        f.write_text(json.dumps({**json.loads(f.read_text()), **spec}))
    found, errors = components.own(root)
    assert not errors, errors
    return found[name or template]


def build_run(conn, root, title, at, state="done", goal="g1"):
    """A build run on an item of the project's goal, `at` seconds ago."""
    if not conn.execute("SELECT 1 FROM goals WHERE id=?", (goal,)).fetchone():
        conn.execute("INSERT INTO goals(id, text, project, created_at) VALUES (?,?,?,?)",
                     (goal, "make the game", "game", 0))
    with db.tx(conn):
        tid = store.create_thread(conn, "", str(root), by="eki")
        rid = store.create_run(conn, tid, "build it", provider="fake", row="code")
    conn.execute("INSERT INTO items(id, goal_id, title, thread_id, run_id, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?)", (rid, goal, title, tid, rid, 0, 0))
    conn.execute("UPDATE runs SET state=?, created_at=? WHERE id=?", (state, time.time() - at, rid))
    conn.commit()
    return rid


def call_run(conn, root, name, at, parent=None, state="done"):
    prompt = json.dumps({"component": name, "args": [], "caller_dir": str(root), "caller_run": parent})
    with db.tx(conn):
        tid = store.create_thread(conn, f"use {name}", None, by="eki")
        rid = store.create_run(conn, tid, prompt, provider="component", parent=parent)
    t = time.time() - at
    conn.execute("UPDATE runs SET state=?, created_at=?, started_at=? WHERE id=?",
                 (state, t, t if state != "queued" else None, rid))
    conn.commit()
    return rid


def test_a_service_card_used_by_build_agents(conn, root):
    voice = card(root, "voice")
    a = build_run(conn, root, "Convert river and finale story into dialogue scenes", 3600)
    b = build_run(conn, root, "Add the title screen", 720)
    for at in (3500, 3400, 3300):
        call_run(conn, root, "voice", at, parent=a)
    call_run(conn, root, "voice", 700, parent=b)
    call_run(conn, root, "other", 10, parent=b)                # another card's call doesn't count
    u = cardview.overview(conn, root, voice, {})["used_by"]
    assert [r["run"] for r in u["runs"]] == [b, a]             # each caller once, newest first
    assert [r["title"] for r in u["runs"]] == ["Add the title screen",
                                               "Convert river and finale story into dia…"]
    assert u["line"].startswith("used 4× today by build agents; last by 'Add the title screen' ")
    assert u["line"].endswith("min ago")
    assert u["stages"] == []                                   # a tool card feeds no stage


def test_a_call_with_no_caller_is_its_own_run(conn, root):
    voice = card(root, "voice")
    rid = call_run(conn, root, "voice", 30)
    u = cardview.overview(conn, root, voice, {})["used_by"]
    assert [r["run"] for r in u["runs"]] == [rid] and u["runs"][0]["title"] == "use voice"


def test_never_used(conn, root):
    u = cardview.overview(conn, root, card(root, "voice"), {})["used_by"]
    assert u == {"stages": [], "line": "not used yet", "runs": []}


def test_a_knowledge_card_gets_the_projects_runs(conn, root, tmp_path):
    k = card(root, "knowledge")
    old = build_run(conn, root, "Old work", 3 * 86400)
    new = build_run(conn, root, "New work", 60)
    with db.tx(conn):                                          # a chat elsewhere isn't the project's
        store.create_run(conn, store.create_thread(conn, "x", str(tmp_path)), "hi", provider="fake")
    u = cardview.overview(conn, root, k, {})["used_by"]
    assert u["stages"] == ["Planner", "Build"]
    assert [r["run"] for r in u["runs"]] == [new, old]
    assert u["line"] == "used 1× today by build agents; last by 'New work' 1 min ago"


def test_the_playtester_feeds_its_stage(conn, root):
    card(root, "workflow")
    pt = card(root, "playtester")
    assert pt.kind == "agent"
    assert cardview.overview(conn, root, pt, {})["used_by"]["stages"] == ["Playtester"]
    other = card(root, "agent", "critic")
    assert cardview.overview(conn, root, other, {})["used_by"]["stages"] == []


def test_a_serve_card_counts_its_links_runs(conn, root):
    serve = card(root, "serve")
    assert cardview.overview(conn, root, serve, {})["used_by"]["stages"] == ["Build", "Playtester"]
    rid = build_run(conn, root, "Convert dialogue", 300)
    servers.write(serve, str(root / "wt"), pid=1, port=1, started_at=time.time() - 200, by=rid)
    u = cardview.overview(conn, root, serve, {})["used_by"]
    assert [r["title"] for r in u["runs"]] == ["Convert dialogue"]
    assert u["line"].startswith("used 1× today by build agents")


def test_now_idle_in_use_held_and_waiting(conn, root):
    voice = card(root, "voice", access="exclusive")
    assert cardview.overview(conn, root, voice, {})["now"] == ["idle"]
    a = build_run(conn, root, "Convert dialogue", 300, state="running")
    c = call_run(conn, root, "voice", 200, parent=a, state="running")
    assert cardview.overview(conn, root, voice, {})["now"] == ["in use by 'Convert dialogue'"]
    store.update_run(conn, c, state="done", ended_at=db.now())
    assert cardview.overview(conn, root, voice, {})["now"] == ["voice is busy: held by 'Convert dialogue'"]
    call_run(conn, root, "voice", 10, state="queued")
    call_run(conn, root, "voice", 5, state="queued")
    now = cardview.overview(conn, root, voice, {})["now"]
    assert now == ["voice is busy: held by 'Convert dialogue'", "2 waiting"]
    assert not any(a in line for line in now)                  # never a run id


def test_now_a_busy_provider(conn, root):
    images = card(root, "images")
    assert images.spec.get("target") == "comfyui:draft"          # a picture card on ComfyUI
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "pic", None), "a cat", provider="comfyui")
    store.update_run(conn, rid, state="running", started_at=db.now())
    assert "ComfyUI is busy" in cardview.overview(conn, root, images, {})["now"]


def test_now_preview_links(conn, root):
    serve = card(root, "serve")
    live = {"links": [
        {"folder": "/w/abc", "url": "http://127.0.0.1:5001/", "task": "Convert dialogue", "run": "r1",
         "started": 1.0, "stops_at": 2.0},
        {"folder": "/w/def", "url": "http://127.0.0.1:5002/", "task": "", "run": "", "started": 1.0,
         "stops_at": None}]}
    assert cardview.overview(conn, root, serve, live)["now"] == [
        "2 preview links running", "Convert dialogue → http://127.0.0.1:5001/", "def → http://127.0.0.1:5002/"]
    assert cardview.overview(conn, root, serve, {"links": []})["now"] == ["no preview links running"]


def test_now_comfy_up_and_down(conn, root):
    images = card(root, "images")
    up = cardview.overview(conn, root, images, {"comfy": {"up": True, "url": "u", "model": "flux-dev"}})
    assert up["now"][-1] == "ComfyUI is up, last used flux-dev"
    assert cardview.overview(conn, root, images, {"comfy": {"up": True, "url": "u", "model": ""}})["now"][-1] \
        == "ComfyUI is up"
    down = cardview.overview(conn, root, images, {"comfy": {"up": False, "url": "u", "model": ""}})
    assert down["now"][-1] == "ComfyUI isn't running — it starts on the next picture"


def test_never_raises(conn, root):
    voice = card(root, "voice")
    serve = card(root, "serve")
    for live in ({"links": "odd"}, {"links": [1, None]}, {"comfy": "odd"}, None, []):
        assert isinstance(cardview.overview(conn, root, serve, live)["now"], list)
        assert isinstance(cardview.overview(conn, root, voice, live)["now"], list)
    conn.execute("DROP TABLE runs")
    got = cardview.overview(conn, root, voice, {})
    assert set(got) == {"used_by", "now"} and got["used_by"]["runs"] == []
    assert set(cardview.overview(conn, root, card(root, "knowledge"), {})["used_by"]) == {"stages", "line", "runs"}


def test_short_and_ago():
    assert cardusedby.short("x" * 50) == "x" * 39 + "…" and cardusedby.short("a\nb") == "a"
    assert cardusedby.ago(time.time() - 5) == "just now" and cardusedby.ago(time.time() - 7300) == "2 h ago"
