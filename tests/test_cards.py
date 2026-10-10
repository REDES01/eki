"""Cards on a project: a project's own components/ entries, pool reference cards, uses from runs."""
import json
import shutil

from eki import api_cards, apiroutes, components, db, store
from eki.components import uses


def put(base, name, spec):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(spec if isinstance(spec, str) else json.dumps(spec))
    return d


def svc(about="does a thing", **kw):
    return {"kind": "service", "about": about, "run": "echo hi", **kw}


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder) if folder else None))
    conn.commit()


def get(conn, path):
    fn, m = apiroutes.match("GET", path)
    return fn(conn, m, {}, {})


def call_run(conn, name, caller_dir, state="done", text="", parent=None, at=None):
    prompt = json.dumps({"component": name, "args": [], "caller_dir": str(caller_dir), "caller_run": parent})
    with db.tx(conn):
        tid = store.create_thread(conn, f"use {name}", None, by="eki")
        rid = store.create_run(conn, tid, prompt, provider="component", parent=parent)
    conn.execute("UPDATE runs SET state=?, created_at=?, started_at=?, ended_at=? WHERE id=?",
                 (state, at, at if state != "queued" else None, at + 2.04 if state == "done" else None, rid))
    if text:
        store.add_event(conn, rid, 0, "text", {"text": text})
    return rid


def test_listing_for_a_project(conn, proj):
    project(conn, proj)
    base = proj / "components"
    shutil.copytree(components.templates_dir() / "voice", base / "voice")
    shutil.copytree(components.templates_dir() / "agent", base / "helper")
    put(components.pool_dir(), "images", svc("pool images"))
    put(components.pool_dir(), "other", svc("never a card"))
    put(base, "images", {"pool": "images"})
    put(base, "bad", "{nope")
    got = get(conn, "/api/projects/p1/cards")
    assert got["project"] == "p1" and got["readonly"] == "" and got["folder"]
    cards = {c["name"]: c for c in got["cards"]}
    assert list(cards) == ["bad", "helper", "images", "voice"]
    keys = {"name", "kind", "about", "scope", "ref", "card", "folder", "error", "notes", "spec", "prompt",
            "instructions", "editable", "state", "last_use", "links", "size"}
    assert all(set(c) == keys for c in cards.values())
    v = cards["voice"]
    assert v["kind"] == "service" and v["scope"] == "project" and v["ref"] == "" and v["editable"]
    assert v["card"] == v["folder"] == str(base.resolve() / "voice") and v["state"] == "idle"
    assert v["spec"]["access"] == "exclusive" and v["prompt"] is None and v["last_use"] is None
    assert cards["helper"]["kind"] == "agent" and cards["helper"]["prompt"]
    i = cards["images"]
    assert i["scope"] == "pool" and i["ref"] == "images" and not i["editable"]
    assert i["folder"] == str(components.pool_dir() / "images") and i["card"] == str(base.resolve() / "images")
    assert i["about"] == "pool images" and i["spec"]["run"] == "echo hi"
    b = cards["bad"]
    assert b["state"] == "broken" and "unreadable" in b["error"] and b["spec"] is None and not b["editable"]
    json.dumps(got)


def test_state_and_last_use_from_runs(conn, proj):
    project(conn, proj)
    put(proj / "components", "voice", svc())
    call_run(conn, "voice", proj, text="\nsaid it\nmore", at=100.0)
    call_run(conn, "voice", proj, state="running", at=200.0)
    call_run(conn, "voice", proj, state="queued", at=300.0)
    c = api_cards.card_of(conn, "p1", "voice")
    assert c["state"].startswith("in use by ") and c["state"].endswith(" · 1 waiting")
    assert c["last_use"] == 300.0


def test_pool_reachable_from_a_project_only_through_a_card(conn, proj, tmp_path):
    put(components.pool_dir(), "images", svc("pool images"))
    project(conn, proj)
    assert components.find(proj, conn)[0] == {}                    # a known project, no components/
    put(proj / "components", "images", {"pool": "images"})
    found, errors = components.find(proj, conn)
    assert errors == [] and found["images"].card == proj.resolve() / "components" / "images"
    other = tmp_path / "other"
    put(other / "components", "own", svc())                         # unknown, but has components/
    assert list(components.find(other)[0]) == ["own"]
    plain = tmp_path / "plain"
    plain.mkdir()
    assert list(components.find(plain, conn)[0]) == ["images"]      # no project: the whole pool
    assert list(components.find(None)[0]) == ["images"]
    rows = {r["name"]: r for r in components.listing(proj, conn)}
    assert rows["images"]["ref"] == "images" and rows["images"]["scope"] == "pool"
    assert rows["images"]["card"] == str(proj.resolve() / "components" / "images")
    assert components.listing(None)[0]["card"] == "" and components.listing(None)[0]["ref"] == ""


def test_broken_ref_cards(proj):
    put(components.pool_dir(), "images", svc())
    put(components.pool_dir(), "broke", {"kind": "rocket", "about": "x"})
    base = proj / "components"
    put(base, "pictures", {"pool": "images"})
    put(base, "voice", {"pool": "voice"})
    put(base, "broke", {"pool": "broke"})
    put(base, "weird", {"pool": "Not A Name"})
    found, errors = components.find(proj)
    assert found == {}
    why = {e[0].rsplit("/", 1)[1]: e[1] for e in errors}
    assert "named like its pool component" in why["pictures"]
    assert "no component voice" in why["voice"]
    assert "pool component broke is broken" in why["broke"]
    assert "not a component name" in why["weird"]
    rows = {r["name"]: r for r in components.listing(proj)}
    assert rows["voice"]["card"] == str(base.resolve() / "voice") and rows["voice"]["ref"] == ""


def test_uses_filter_by_project_and_newest_first(conn, proj, tmp_path):
    put(proj / "components", "voice", svc())
    other = tmp_path / "other"
    other.mkdir()
    a = call_run(conn, "voice", proj, text="first\n", at=100.0)
    call_run(conn, "voice", other, at=150.0)
    b = call_run(conn, "voice", proj / "components", state="failed", at=200.0, parent=a)
    conn.execute("UPDATE runs SET error='boom' WHERE id=?", (b,))
    call_run(conn, "images", proj, at=300.0)
    got = uses.calls(conn, "voice", proj.resolve())
    assert [u["run"] for u in got] == [b, a]
    assert got[1] == {"run": a, "when": 100.0, "started": 100.0, "ended": 102.04, "caller": "you",
                      "state": "done", "result": "first", "error": "", "seconds": 2.0}
    assert got[0]["caller"] == a and got[0]["error"] == "boom" and got[0]["seconds"] is None
    assert len(uses.calls(conn, "voice", None)) == 3
    assert len(uses.calls(conn, "voice", None, limit=1)) == 1
    assert uses.last_use(conn, "voice", proj.resolve()) == 200.0
    assert uses.last_use(conn, "nothing", None) is None and uses.waiting(conn, "voice") == 0


def test_a_project_with_no_folder_is_readonly(conn):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo) VALUES "
                 "('g1','gh','/nowhere','main',0,'me/gh')")
    conn.commit()
    got = get(conn, "/api/projects/g1/cards")
    assert got == {"project": "g1", "folder": None, "readonly": api_cards.READONLY, "cards": [],
                   "suggest": None}
    try:
        api_cards.writable_root(conn, "g1")
        raise AssertionError("no ValueError")
    except ValueError as e:
        assert str(e) == "pick or clone a folder first"
    try:
        api_cards.project_root_of(conn, "nope")
        raise AssertionError("no KeyError")
    except KeyError:
        pass


def test_library_lists_templates_and_pool(conn):
    put(components.pool_dir(), "images", svc("pool images"))
    put(components.pool_dir(), "bad", "{nope")
    got = get(conn, "/api/cards/library")
    names = [t["name"] for t in got["templates"]]
    assert {"playtester", "voice", "images", "assets", "agent", "service"} <= set(names)
    assert next(t for t in got["templates"] if t["name"] == "agent")["kind"] == "agent"
    pool = {p["name"]: p for p in got["pool"]}
    assert pool["images"] == {"name": "images", "kind": "service", "about": "pool images", "error": ""}
    assert pool["bad"]["error"]
