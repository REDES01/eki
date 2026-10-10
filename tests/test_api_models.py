"""The Settings page's model picture: /api/models, its refresh, and the page's wiring."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eki import api_models, db, modellist, roles
from test_web import get, post, web  # noqa: F401  (the fixture)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def _providers(home, **more):
    data = json.loads((home / "providers.json").read_text())
    data.update(more)
    (home / "providers.json").write_text(json.dumps(data))


def _seed(conn, name, models, at=1000.0):
    with db.tx(conn):
        conn.executemany(
            "INSERT INTO provider_models(provider, model, label, is_default, read_at) VALUES (?,?,?,?,?)",
            [(name, m, m.title(), int(i == 1), at) for i, m in enumerate(models)])


def test_read_gives_each_providers_tiers_list_and_suggestion_and_the_roles(home, conn):
    _providers(home, claude={"kind": "claude_code", "model": "claude-sonnet-4-5",
                             "models": {"fast": "claude-haiku-4-5"}})
    _seed(conn, "claude", ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-1"])
    roles.set_role("router", "claude:fast")
    got = api_models.read(conn)
    c = got["providers"]["claude"]
    assert c["tiers"] == {"fast": "claude-haiku-4-5", "default": "claude-sonnet-4-5"}
    assert c["suggested"] == {"fast": "claude-haiku-4-5", "default": "claude-sonnet-4-5", "top": "claude-opus-4-1"}
    assert [m["model"] for m in c["listed"]] == ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-1"]
    assert c["read_at"] == 1000.0 and c["lists"] is True
    assert got["providers"]["fake"]["listed"] is None and got["providers"]["fake"]["suggested"] == {}
    assert set(got["roles"]) == set(roles.ROLES)
    r = got["roles"]["router"]
    assert r["target"] == "claude:fast" and r["default"] == "none" and r["set"] is True
    assert set(r) == {"target", "provider", "model", "why", "default", "set"}
    assert got["roles"]["planner"]["target"] == "fake" and got["roles"]["planner"]["set"] is False


def test_refresh_replaces_one_providers_rows_and_writes_no_settings(home, conn, monkeypatch):
    _providers(home, claude={"kind": "claude_code"}, codex={"kind": "codex"})
    _seed(conn, "claude", ["old-a", "old-b"])
    _seed(conn, "codex", ["gpt-5"])
    before = (home / "providers.json").read_text()
    monkeypatch.setattr(modellist, "read", lambda name, cfg, timeout=0: [
        {"model": "claude-haiku-4-5", "label": "Haiku", "is_default": False}])
    got = api_models.refresh(conn, "claude")
    assert [m["model"] for m in got["providers"]["claude"]["listed"]] == ["claude-haiku-4-5"]
    assert [m["model"] for m in modellist.listed(conn, "codex")] == ["gpt-5"]
    assert (home / "providers.json").read_text() == before


def test_the_routes_answer_and_the_post_needs_x_eki(web, home, monkeypatch):
    code, body = get(web + "/api/models")
    assert code == 200 and set(json.loads(body)) == {"providers", "roles"}
    monkeypatch.setattr(modellist, "read", lambda name, cfg, timeout=0: [
        {"model": "m1", "label": "M1", "is_default": True}])
    code, out = post(web + "/api/models/refresh", {"name": "fake"})
    assert code == 200 and out["providers"]["fake"]["listed"][0]["model"] == "m1"
    code, _ = post(web + "/api/models/refresh", {"name": "fake"}, header=False)
    assert code == 403
    code, out = post(web + "/api/models/refresh", {"name": "nobody"})
    assert code == 404
    code, out = post(web + "/api/models/refresh", {})
    assert code == 400


def test_settings_js_shrank_and_the_models_block_loads_before_it(web):
    assert len((WEB / "settings.js").read_text().splitlines()) < 398
    _, page = get(web + "/")
    assert page.index("/ui/settingsmodels.js") < page.index("/ui/settings.js")
    assert get(web + "/ui/settingsmodels.js")[0] == 200
    js = (WEB / "settings.js").read_text()
    assert "planner.provider" not in js and "Planner model" not in js
    assert "settingsModels.roles(d)" in js and "settingsModels.tiers(name, cfg)" in js


STUB = """
global.window = global; global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;") };
require(process.argv[2]);
const [got, draft] = JSON.parse(process.argv[3]);
global.fetch = async () => ({ ok: true, json: async () => got });
settingsModels.load().then(() => console.log(JSON.stringify({
  roles: settingsModels.roles(draft), summary: settingsModels.summary({ claude: { models: { fast: "h" } } }) })));
"""


def test_the_routing_tab_has_the_router_picker(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    got = {"providers": {"claude": {"kind": "claude_code", "lists": True, "tiers": {"fast": "h"},
                                    "suggested": {}, "listed": [{"model": "h", "label": "H"}], "read_at": 1}},
           "roles": {r: {"target": "local", "provider": "local", "model": None, "default": "local",
                         "why": "router claude:fast can't run (claude: limit) → local" if r == "router" else "",
                         "set": False} for r in roles.ROLES}}
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "settingsmodels.js"),
                         json.dumps([got, {"roles": {"router": "claude:fast"}}])],
                         capture_output=True, text=True, timeout=30, check=True)
    html = json.loads(out.stdout)
    assert "Router" in html["roles"] and 'data-role="router"' in html["roles"]
    assert '<option value="claude:fast" selected>' in html["roles"]
    assert "var(--amber)" in html["roles"] and "can't run" in html["roles"]
    assert 'data-role="planner"' not in html["roles"]          # the others open from the summary
    assert "router claude:fast · planner local" in html["roles"]
    assert "claude fast h" in html["summary"]
