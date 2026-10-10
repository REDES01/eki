"""GET /api/components: the pool and each project's own components, each with its spec."""
import json

from eki import api_components, apiroutes, components, projectchats


def put(base, name, spec):
    d = base / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(spec if isinstance(spec, str) else json.dumps(spec))


def test_lists_pool_and_project_components(conn, proj, monkeypatch):
    put(components.pool_dir(), "images", {"kind": "service", "about": "pictures", "run": "echo p"})
    put(proj / "components", "voice", {"kind": "service", "about": "speaks", "run": "echo v"})
    put(proj / "components", "bad", "{nope")
    monkeypatch.setattr(projectchats, "listing", lambda c: [
        {"name": "game", "folder": str(proj), "folder_ok": True},
        {"name": "gone", "folder": None, "folder_ok": False}])
    fn, m = apiroutes.match("GET", "/api/components")
    got = fn(conn, m, {}, {})
    assert [c["name"] for c in got["pool"]] == ["images"]
    assert got["pool"][0]["spec"]["run"] == "echo p" and got["pool"][0]["scope"] == "pool"
    assert [p["project"] for p in got["projects"]] == ["game"]
    own = {c["name"]: c for c in got["projects"][0]["components"]}
    assert set(own) == {"voice", "bad"}
    assert own["voice"]["spec"]["about"] == "speaks" and own["voice"]["about"] == "speaks"
    assert own["bad"]["error"] and own["bad"]["spec"] is None
    json.dumps(got)


def test_nothing_anywhere(conn, monkeypatch):
    monkeypatch.setattr(projectchats, "listing", lambda c: [])
    assert api_components.component_list(conn, None, {}, {}) == {"pool": [], "projects": []}
