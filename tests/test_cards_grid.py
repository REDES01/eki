"""The Cards grid is the light call — eight keys a card, no subprocess, a few SQL statements — and an
opened card's details come from GET …/cards/<name>, its pool size from the stored value."""
import json
import subprocess

import pytest

from eki import api_carddetail, apiroutes, components, db, store
from eki.components import assets

KEYS = {"name", "title", "kind", "about", "ref", "error", "state", "last_use"}


def put(root, name, spec):
    d = root / "components" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(spec if isinstance(spec, str) else json.dumps(spec))
    return d


def get(conn, path):
    fn, m = apiroutes.match("GET", path)
    return fn(conn, m, {}, {})


def use(conn, name, caller_dir, state="done", at=100.0):
    prompt = json.dumps({"component": name, "args": [], "caller_dir": str(caller_dir), "caller_run": None})
    with db.tx(conn):
        tid = store.create_thread(conn, f"use {name}", None, by="eki")
        rid = store.create_run(conn, tid, prompt, provider="component")
    conn.execute("UPDATE runs SET state=?, created_at=?, started_at=?, ended_at=? WHERE id=?",
                 (state, at, at, at + 1 if state == "done" else None, rid))
    conn.commit()


@pytest.fixture
def game(conn, proj):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 ("p1", "game", str(proj), "trunk", 0, str(proj)))
    conn.commit()
    for i in range(13):
        put(proj, f"tool{i:02d}", {"kind": "service", "about": f"tool {i}", "run": "echo hi"})
    put(proj, "media", {"kind": "assets", "about": "media", "path": "assets"})
    put(proj, "site", {"kind": "serve", "about": "the game", "path": "/"})
    put(proj, "bad", "{nope")
    (proj / "assets").mkdir()
    (proj / "assets" / "a.bin").write_bytes(b"12345")
    use(conn, "tool00", proj, at=100.0)
    use(conn, "tool01", proj, state="running", at=200.0)
    api_carddetail._kept.clear()
    return proj


def test_the_grid_has_only_the_eight_keys(conn, game):
    got = get(conn, "/api/projects/p1/cards")
    assert set(got) == {"project", "folder", "readonly", "cards", "suggest"}
    cards = {c["name"]: c for c in got["cards"]}
    assert len(cards) == 16 and all(set(c) == KEYS for c in cards.values())
    assert cards["tool00"] == {"name": "tool00", "title": "Tool", "kind": "service", "about": "tool 0", "ref": "",
                               "error": "", "state": "idle", "last_use": 101.0}
    assert cards["tool01"]["state"] == "in use" and cards["tool02"]["last_use"] is None
    assert cards["bad"]["state"] == "broken" and cards["bad"]["error"]


def test_a_warm_grid_runs_no_subprocess_and_no_kind_hook(conn, game, monkeypatch):
    first = get(conn, "/api/projects/p1/cards")

    def boom(*a, **k):
        raise AssertionError("the grid ran a subprocess")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    for kind in ("service", "assets", "serve"):
        mod = components.kind_module(kind)
        for hook in ("is_up", "live", "notes"):
            monkeypatch.setattr(mod, hook, boom, raising=False)
    monkeypatch.setattr(components, "_notes", boom)
    assert get(conn, "/api/projects/p1/cards") == first


def test_a_changed_component_json_shows_in_the_next_grid(conn, game):
    about = lambda: {c["name"]: c["about"] for c in get(conn, "/api/projects/p1/cards")["cards"]}["tool03"]
    assert about() == "tool 3"
    put(game, "tool03", {"kind": "service", "about": "a newer and longer line", "run": "echo hi"})
    assert about() == "a newer and longer line"


def test_the_grid_runs_at_most_four_sql_statements(conn, game):
    get(conn, "/api/projects/p1/cards")                     # warm: folders and git asked once
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        cards = get(conn, "/api/projects/p1/cards")["cards"]
    finally:
        conn.set_trace_callback(None)
    assert len(cards) == 16 and len(seen) <= 4, seen


def test_an_opened_card_has_the_heavy_parts_and_the_stored_size(conn, game, monkeypatch):
    def walk(p):
        raise AssertionError("the pool was walked during a request")
    monkeypatch.setattr(assets, "_size", walk)
    c = get(conn, "/api/projects/p1/cards/media")["card"]
    assert {"overview", "live", "notes", "size", "links", "spec", "choices"} <= set(c)
    assert c["size"] == ""                                  # never measured yet
    monkeypatch.undo()
    assets.measure([game])                                  # housekeeping, in the background
    monkeypatch.setattr(assets, "_size", walk)
    api_carddetail._kept.clear()
    assert get(conn, "/api/projects/p1/cards/media")["card"]["size"] == "5 B"
    assert get(conn, "/api/projects/p1/cards/bad")["card"]["overview"]["now"][0].startswith("broken: ")
    with pytest.raises(KeyError):
        get(conn, "/api/projects/p1/cards/nothing")


def test_an_opened_card_is_kept_briefly_and_rebuilt_on_change(conn, game, monkeypatch):
    built = []
    real = api_carddetail.build
    monkeypatch.setattr(api_carddetail, "build", lambda *a: built.append(a[2]) or real(*a))
    get(conn, "/api/projects/p1/cards/tool04")
    get(conn, "/api/projects/p1/cards/tool04")
    assert built == ["tool04"]
    put(game, "tool04", {"kind": "service", "about": "changed, and longer", "run": "echo hi"})
    assert get(conn, "/api/projects/p1/cards/tool04")["card"]["about"] == "changed, and longer"
    assert built == ["tool04", "tool04"]
