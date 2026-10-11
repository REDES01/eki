"""The workflow card in the window: cardworkflow.js gives the kind a short part in the open card
(projectcardpanel.js loads it like cardknowledge.js) — one line, the same words as the server's "line"
(eki/api_workflow.py), and "Open workflow →" to the project's Workflow page; every workflow setting is
kept out of the panel's form and there's no Settings section — and the card JSON carries `locked`."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eki import api_cards, api_workflow, apiroutes, builds, components, workflow
from test_web_choicemenu import DOM

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "cardworkflow.js").read_text()
PANEL = (WEB / "projectcardpanel.js").read_text()
KEYS = ["router", "target", "plan", "parallel", "checks", "command", "merge", "playtester", "after_merge", "access"]


# ---- the API --------------------------------------------------------------------------------------

def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def _project(conn, pid, folder):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, pid, str(folder), "trunk", 0, str(folder)))
    conn.commit()


def _card(conn, pid, name):
    return api_cards.card_of(conn, pid, name)


def test_the_card_json_carries_locked_on_eki_and_none_elsewhere(conn, proj, tmp_path, monkeypatch):
    eki = tmp_path / "eki"
    shutil.copytree(builds.source() / "components" / "workflow", eki / "components" / "workflow")
    monkeypatch.setenv("EKI_SOURCE", str(eki))
    _project(conn, "eki", eki)
    shutil.copytree(components.templates_dir() / "workflow", proj / "components" / "workflow")
    shutil.copytree(components.templates_dir() / "knowledge", proj / "components" / "notes")
    _project(conn, "game", proj)
    mine = _card(conn, "eki", "workflow")
    assert mine["locked"] == list(workflow.LOCKED) == ["batch", "train"]
    assert mine["choices"]["router"] == ["on", "off"]
    assert _card(conn, "game", "workflow")["locked"] == []
    assert "locked" not in _card(conn, "game", "notes")                     # only a workflow card has it
    edited = call(conn, "POST", "/api/projects/eki/cards/workflow", {"settings": {"parallel": 2}})["card"]
    assert edited["spec"]["parallel"] == 2 and edited["locked"] == ["batch", "train"]


# ---- the files ------------------------------------------------------------------------------------

def test_files_are_short_and_the_panel_loads_the_part():
    assert '"cardworkflow.js"' in PANEL and "kindPart().keys" in PANEL
    assert len(PANEL.splitlines()) < 400 and len(JS.splitlines()) < 400
    assert 'href="/ui/cardworkflow.css"' in JS and (WEB / "cardworkflow.css").is_file()
    for gone in ("data-cw-check", "settingsTarget", "data-cw=", "/api/models"):   # on the Workflow page now
        assert gone not in JS, gone


def test_both_parse():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    for f in ("cardworkflow.js", "projectcardpanel.js"):
        out = subprocess.run(["node", "--check", str(WEB / f)], capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr


# ---- the part, run --------------------------------------------------------------------------------

STUB = r"""
const fs = require("fs");
const [, , part, args] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}) };
eval(fs.readFileSync(part, "utf8"));
const box = { innerHTML: "" };
const k = window.projectCardKinds.workflow;
k.draw(box, JSON.parse(args), "my game", "", { save() {}, problems: {}, editable: true });
k.close();
process.stdout.write(JSON.stringify({ html: box.innerHTML, keys: k.keys, title: k.title, form: k.form }));
"""


def card(**spec):
    return {"name": "workflow", "kind": "workflow", "editable": True, "locked": [],
            "spec": {"kind": "workflow", **spec}}


def run(tmp_path, c):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "cardworkflow.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def server_line(spec):
    return api_workflow.line(api_workflow._values(spec, False))


def test_the_line_for_a_default_card(tmp_path):
    got = run(tmp_path, card())
    assert '<div class="cw-line">plan split · 4 parallel · checks: self · merge auto</div>' in got["html"]
    assert server_line({}) == "plan split · 4 parallel · checks: self · merge auto"
    assert got["title"] == "Workflow" and got["keys"] == KEYS and got["form"] is False


@pytest.mark.parametrize("spec, want", [
    ({"router": "off", "target": "claude:opus"},
     "router off → claude:opus · plan split · 4 parallel · checks: self · merge auto"),
    ({"router": "off", "target": {"provider": "claude", "model": "opus", "effort": "high"}},
     "router off → claude:opus@high · plan split · 4 parallel · checks: self · merge auto"),
    ({"plan": "single", "parallel": 1, "merge": "person"},
     "plan single · 1 parallel · checks: self · merge person"),
    ({"checks": ["self", "review", "command"], "command": "make test", "parallel": 2, "merge": "pr"},
     "plan split · 2 parallel · checks: self, review, command · merge pr"),
])
def test_the_line_matches_the_server_word_for_word(tmp_path, spec, want):
    html = run(tmp_path, card(**spec))["html"]
    assert f'<div class="cw-line">{want}</div>' in html
    assert server_line(spec) == want


def test_the_link_opens_the_workflow_page(tmp_path):
    html = run(tmp_path, card())["html"]
    assert '<a class="cw-open" href="#project/my%20game/workflow">Open workflow →</a>' in html


def test_a_broken_card_shows_no_line_but_the_link(tmp_path):
    c = card()
    c.update(spec=None, error="component.json: not valid JSON")
    html = run(tmp_path, c)["html"]
    assert "cw-line" not in html and "Open workflow →" in html


def test_the_panel_draws_the_line_no_workflow_keys_and_no_settings(tmp_path):
    out = panel(tmp_path, dict(card(checks=["self", "review"], after_merge="npm run build", access="all"),
                               choices=components.choices("workflow"),
                               choice_defaults=components.choice_defaults("workflow"),
                               choice_help=components.choice_help("workflow"),
                               field_help=components.field_help("workflow")))
    assert '<h4 class="pp-h">Workflow</h4>' in out["frame"]
    for h in ("Settings", "Run", "Uses"):
        assert f'<h4 class="pp-h">{h}' not in out["frame"], h
    assert out["settings"] is None and "data-key=" not in out["frame"] + out["kind"]
    assert "plan split · 4 parallel · checks: self, review · merge auto" in out["kind"]


def test_a_broken_workflow_card_still_shows_its_error(tmp_path):
    c = dict(card(), spec=None, error="component.json: not valid JSON", choices=components.choices("workflow"))
    out = panel(tmp_path, c)
    assert "component.json: not valid JSON" in out["head"] and "broken" in out["head"]


def panel(tmp_path, c):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "panel.js"
    stub.write_text(PANEL_STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "cardworkflow.js"), str(WEB / "projectcardpanel.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


PANEL_STUB = DOM + r"""
function mk(parent) {
  const e = { parent, dataset: {}, listeners: {}, kids: {}, _html: "",
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); this.kids = {}; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    contains() { return false; }, closest() { return null; }, matches() { return false; }, focus() {},
    querySelectorAll() { return []; },
    querySelector(sel) {
      const m = /^\.([\w-]+)$/.exec(sel);
      if (!m || !new RegExp('class="[^"]*\\b' + m[1] + '\\b').test(this._html)) return null;
      return this.kids[sel] || (this.kids[sel] = mk(this));
    } };
  return e;
}
global.CSS = { escape: (s) => s };
document.querySelector = () => ({});
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
for (const f of process.argv.slice(2, 6)) require(f);
const card = JSON.parse(process.argv[6]);
const box = mk(null);
projectCardPanel.open(box, "p1", card, "", { ago: () => "", reload() {}, api: async () => ({}) });
const settings = box.querySelector(".pp-settings");
process.stdout.write(JSON.stringify({ frame: box.innerHTML, settings: settings ? settings.innerHTML : null,
  head: box.querySelector(".pp-head").innerHTML,
  kind: box.querySelector(".pp-kind").innerHTML }));
"""
