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


def test_read_gives_each_providers_list_and_the_roles(home, conn):
    _providers(home, claude={"kind": "claude_code", "model": "claude-sonnet-4-5",
                             "models": {"fast": "claude-haiku-4-5"}})
    _seed(conn, "claude", ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-1"])
    roles.set_role("router", "claude:fast")
    got = api_models.read(conn)
    c = got["providers"]["claude"]
    assert "suggested" not in c and "tiers" not in c
    assert [m["model"] for m in c["listed"]] == ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-1"]
    assert c["read_at"] == 1000.0 and c["lists"] is True
    assert got["providers"]["fake"]["listed"] is None
    assert set(got["roles"]) == set(roles.ROLES)
    r = got["roles"]["router"]
    assert r["target"] == "claude:fast" and r["default"] == "none" and r["set"] is True
    assert set(r) == {"target", "provider", "model", "effort", "shown", "why", "default", "set"}
    assert got["roles"]["planner"]["target"] == "fake" and got["roles"]["planner"]["set"] is False


def test_floating_models_come_first_then_pinned_in_program_order(home, conn):
    _providers(home, claude={"kind": "claude_code"})
    with db.tx(conn):
        conn.executemany(
            "INSERT INTO provider_models(provider, model, label, description, resolved, efforts,"
            " default_effort, is_default, read_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [("claude", "claude-opus-4-8", "Opus 4.8", "pinned", None, None, None, 0, 1.0),
             ("claude", "default", "Default", "the default", "claude-sonnet-5-5", '["low", "high"]', "high", 1, 1.0),
             ("claude", "claude-haiku-4-5", "Haiku 4.5", None, None, None, None, 0, 1.0),
             ("claude", "opus", "Opus", "newest opus", "claude-opus-5-5", '["low", "high", "max"]', None, 0, 1.0)])
    listed = api_models.read(conn)["providers"]["claude"]["listed"]
    assert [m["model"] for m in listed] == ["default", "opus", "claude-opus-4-8", "claude-haiku-4-5"]
    assert [m["floating"] for m in listed] == [True, True, False, False]
    top = listed[1]
    assert top["label"] == "Opus" and top["description"] == "newest opus" and top["resolved"] == "claude-opus-5-5"
    assert top["efforts"] == ["low", "high", "max"] and listed[0]["default_effort"] == "high"
    assert listed[2]["efforts"] is None


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
    assert len((WEB / "settings.js").read_text().splitlines()) < 392
    _, page = get(web + "/")
    assert page.index("/ui/settingstarget.js") < page.index("/ui/settingsmodels.js") < page.index("/ui/settings.js")
    assert get(web + "/ui/settingsmodels.js")[0] == 200 and get(web + "/ui/settingstarget.js")[0] == 200
    js = (WEB / "settings.js").read_text()
    assert "planner.provider" not in js and "Planner model" not in js
    assert "settingsModels.roles(d)" in js and "settingsModels.line(name)" in js and "settingsTarget.html(" in js
    models = (WEB / "settingsmodels.js").read_text()
    for gone in ("tiers(", "summary(", "editTier", "accept", "TIERS"):
        assert gone not in js and gone not in models


STUB = """
global.window = global; global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
require(process.argv[2]); require(process.argv[3]);
const [got, draft, rows] = JSON.parse(process.argv[4]);
global.fetch = async () => ({ ok: true, json: async () => got });
// a fake wrapper: value() reads [data-tp]/[data-tm]/[data-te] and the changed element
const el = (attr, value, efforts) => ({ value, tagName: "SELECT", hasAttribute: (a) => a === attr,
  selectedOptions: [{ dataset: efforts == null ? {} : { efforts } }] });
const wrap = (p, m, e) => ({ querySelector: (q) => ({ "[data-tp]": { value: p }, "[data-tm]": m == null ? null : { value: m },
  "[data-te]": e == null ? null : { value: e } })[q] });
settingsModels.load().then(() => {
  settingsModels.editRole({ tagName: "SELECT", closest: () => ({ dataset: { role: "planner" }, querySelector: wrap("claude", "opus", "high").querySelector }),
    hasAttribute: (a) => a === "data-te" }, draft);
  console.log(JSON.stringify({
    roles: settingsModels.roles(draft), line: settingsModels.line("claude"), draft,
    rows: rows.map((t) => settingsTarget.html(t, { names: ["claude", "local"], models: got.providers })),
    values: [
      settingsTarget.value(wrap("claude", "opus", "high"), el("data-te", "high")),
      settingsTarget.value(wrap("claude", "opus", ""), el("data-te", "")),
      settingsTarget.value(wrap("claude", "opus", "high"), el("data-tp", "claude")),
      settingsTarget.value(wrap("claude", "claude-haiku-4-5", "high"), el("data-tm", "claude-haiku-4-5", "")),
      settingsTarget.value(wrap("claude", "", "max"), el("data-te", "max")),
      settingsTarget.value(wrap("local", "qwen3:32b"), { tagName: "INPUT", hasAttribute: (a) => a === "data-tm" }),
      settingsTarget.value(wrap("off"), el("data-tp", "off"))],
  }));
});
"""

LISTED = [
    {"model": "default", "label": "Default", "description": "Sonnet 5.5, the default", "resolved": "claude-sonnet-5-5",
     "efforts": ["low", "high"], "default_effort": "high", "is_default": True, "floating": True},
    {"model": "opus", "label": "Opus", "description": "The newest Opus", "resolved": "claude-opus-5-5",
     "efforts": ["low", "high", "max"], "default_effort": None, "is_default": False, "floating": True},
    {"model": "claude-haiku-4-5", "label": "Haiku 4.5", "description": None, "resolved": None,
     "efforts": None, "default_effort": None, "is_default": False, "floating": False}]


def _node(tmp_path, got, draft, rows):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "settingstarget.js"), str(WEB / "settingsmodels.js"),
                         json.dumps([got, draft, rows])], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_one_picker_for_roles_and_row_targets(tmp_path):
    got = {"providers": {"claude": {"kind": "claude_code", "lists": True, "listed": LISTED, "read_at": 1},
                         "local": {"kind": "local", "lists": True, "listed": None, "read_at": None},
                         "comfyui": {"kind": "comfyui", "lists": False, "listed": None, "read_at": None}},
           "roles": {r: {"target": "local", "provider": "local", "model": None, "effort": None,
                         "shown": "local · its default", "default": "local",
                         "why": "router claude:fast can't run (claude: limit) → local" if r == "router" else "",
                         "set": False} for r in roles.ROLES}}
    html = _node(tmp_path, got, {"roles": {"router": "claude:claude-haiku-4-5"}}, [
        {"provider": "claude", "model": "opus", "effort": "high"},
        "claude:claude-opus-4-1",
        {"provider": "claude", "model": "opus", "effort": "medium"},
        {"provider": "claude", "model": "claude-haiku-4-5", "effort": "low"},
        "local:qwen3:32b", "claude:fast"])
    r = html["roles"]
    assert "Router" in r and all(f'data-role="{x}"' in r for x in ("router", "planner", "reviewer", "chores"))
    assert '<option value="claude" selected>' in r and '<option value="claude-haiku-4-5" selected' in r
    assert "var(--amber)" in r and "can't run" in r and "now local · its default" in r
    assert html["draft"]["roles"]["planner"] == {"provider": "claude", "model": "opus", "effort": "high"}
    assert "3 models" in html["line"] and 'data-act="mrefresh"' in html["line"]

    opus, gone, dropped, none, local, tier = html["rows"]
    # the model step: its default first, floating names, then pinned ids, each with its description
    assert opus.index(">its default<") < opus.index("Opus — follows the newest") < opus.index("Haiku 4.5")
    assert 'title="The newest Opus"' in opus and 'data-efforts="low,high,max"' in opus
    # the effort step: exactly the model's levels, the default marked, and its default
    assert '<option value="high" selected>high</option>' in opus and '<option value="max">' in opus
    assert "var(--amber)" not in opus
    assert "claude-opus-4-1 isn't in claude's list any more; its default" in gone
    assert "claude opus doesn't offer effort medium any more; its default" in dropped
    assert "claude-haiku-4-5 takes no effort" in none
    assert 'data-tm value="qwen3:32b"' in local                    # a never-read list: free text
    assert "fast — tier from providers.json" in tier and "var(--amber)" not in tier
    assert html["values"] == [
        {"provider": "claude", "model": "opus", "effort": "high"},
        "claude:opus",
        "claude",                                                   # a new provider starts at its defaults
        "claude:claude-haiku-4-5",                                  # a model without that effort drops it
        {"provider": "claude", "effort": "max"},
        "local:qwen3:32b",
        "off"]


def test_the_default_model_marks_its_default_effort(tmp_path):
    got = {"providers": {"claude": {"kind": "claude_code", "lists": True, "listed": LISTED, "read_at": 1},
                         "comfyui": {"kind": "comfyui", "lists": False, "listed": None, "read_at": None}},
           "roles": {r: {"target": "claude", "provider": "claude", "model": None, "effort": None, "shown": "",
                         "default": "claude", "why": "", "set": False} for r in roles.ROLES}}
    html = _node(tmp_path, got, {}, ["claude", {"provider": "comfyui"}])
    assert '<option value="high">high (default)</option>' in html["rows"][0]
    assert "data-tm" not in html["rows"][1]                         # no list and none to read: provider only
