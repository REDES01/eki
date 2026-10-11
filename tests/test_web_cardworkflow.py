"""The workflow card in the window: cardworkflow.js gives the kind its own part in the open card
(projectcardpanel.js loads it like cardknowledge.js) — the checks as toggles in the order they run with
self always on, eki's locked checks fixed, the command field when command or batch is on, the target
picker when router is off, parallel a number — and the card JSON carries `locked` (eki/api_cards.py)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eki import apiroutes, builds, components, workflow
from test_web_choicemenu import DOM

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "cardworkflow.js").read_text()
PANEL = (WEB / "projectcardpanel.js").read_text()
ORDER = ["self", "review", "command", "batch", "playtester", "train"]


# ---- the API --------------------------------------------------------------------------------------

def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def _project(conn, pid, folder):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, pid, str(folder), "trunk", 0, str(folder)))
    conn.commit()


def _card(conn, pid, name):
    return {c["name"]: c for c in call(conn, "GET", f"/api/projects/{pid}/cards")["cards"]}[name]


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

def test_the_panel_loads_cardworkflow_and_keeps_its_keys_out_of_the_form():
    assert '"cardworkflow.js"' in PANEL
    assert re.search(r'const PASSIVE = \[[^\]]*"workflow"', PANEL)
    assert "kindPart().keys" in PANEL and "save: saveSettings" in PANEL
    assert len(PANEL.splitlines()) < 400 and len(JS.splitlines()) < 400
    assert 'href="/ui/cardworkflow.css"' in JS and (WEB / "cardworkflow.css").is_file()


def test_both_parse():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    for f in ("cardworkflow.js", "projectcardpanel.js"):
        out = subprocess.run(["node", "--check", str(WEB / f)], capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr


# ---- the part, run --------------------------------------------------------------------------------

# Just enough page to draw the part, flip one toggle or field, and read what was saved.
STUB = r"""
const fs = require("fs");
const [, , part, target, args] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}), activeElement: null };
global.fetch = async () => ({ json: async () => ({ providers: { claude: { listed: null, lists: false } } }) });
eval(fs.readFileSync(target, "utf8"));
eval(fs.readFileSync(part, "utf8"));
const [card, change] = JSON.parse(args);
const flips = {};
const box = { _h: "", dataset: {}, listeners: {},
  get innerHTML() { return this._h; }, set innerHTML(v) { this._h = String(v); },
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
  contains() { return false; },
  querySelectorAll(sel) {
    if (sel !== "[data-cw-check]") return [];
    return [...this._h.matchAll(/data-cw-check="(\w+)"( checked)?/g)].map((m) =>
      ({ dataset: { cwCheck: m[1] }, checked: m[1] in flips ? flips[m[1]] : !!m[2] }));
  } };
const out = { saved: [], html: [] };
const ctx = { editable: card.editable !== false, problems: card.problems || {},
  save: async (set) => { out.saved.push(set); } };
const kinds = window.projectCardKinds;
kinds.workflow.draw(box, card, "p1", "", ctx);
out.html.push(box.innerHTML);
(async () => {
  if (change) {
    let t;
    if (change.check) { flips[change.check] = change.on; t = { dataset: { cwCheck: change.check } }; }
    else t = { dataset: { cw: change.field }, value: change.value };
    for (const fn of box.listeners.change) await fn({ target: t });
    for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
    out.html.push(box.innerHTML);
  }
  out.keys = kinds.workflow.keys; out.title = kinds.workflow.title;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


def card(**spec):
    locked = spec.pop("locked", [])
    extra = {k: spec.pop(k) for k in ("problems", "editable") if k in spec}
    return {"name": "workflow", "kind": "workflow", "editable": True, "locked": locked,
            "spec": {"kind": "workflow", "router": "on", "plan": "split", "parallel": 4, "checks": ["self"],
                     "merge": "auto", **spec}, **extra}


def run(tmp_path, c, change=None):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "cardworkflow.js"), str(WEB / "settingstarget.js"),
                          json.dumps([c, change])], capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def toggles(html):
    """Each check toggle in order: (name, checked, disabled)."""
    return [(m.group(1), " checked" in m.group(2), " disabled" in m.group(2))
            for m in re.finditer(r'<input type="checkbox" data-cw-check="(\w+)"([^>]*)>', html)]


def test_six_checks_in_order_with_self_on_and_fixed(tmp_path):
    got = run(tmp_path, card(checks=["self", "review"]))
    html = got["html"][0]
    assert got["title"] == "Workflow" and got["keys"] == ["checks", "command", "target", "after_merge", "parallel"]
    assert [t[0] for t in toggles(html)] == ORDER
    assert toggles(html)[:2] == [("self", True, True), ("review", True, False)]
    assert ("command", False, False) in toggles(html)
    assert ("train", False, True) in toggles(html)                 # train is eki's own
    for line in ("the building agent verifies its own work — screenshots, its tests — before it finishes; always on",
                 "a second reader agent", "the project's check command",
                 "the queue checks queued changes together and bisects on red",
                 "the project's playtester card plays the change", "build + drills + swap, only for eki itself"):
        assert line in html, line
    assert "can't be turned off here" not in html
    assert re.search(r'data-cw="parallel" value="4"', html)
    assert 'data-cw="command"' not in html and "data-tgt" not in html


def test_eki_locked_checks_are_on_and_disabled_with_why(tmp_path):
    html = run(tmp_path, card(checks=["self", "review", "batch", "train"], locked=["batch", "train"]))["html"][0]
    t = dict((n, (on, dis)) for n, on, dis in toggles(html))
    assert t["batch"] == (True, True) and t["train"] == (True, True) and t["self"] == (True, True)
    assert t["review"] == (True, False) and t["playtester"] == (False, False)
    assert html.count("protects eki itself: the train, the drills and locked files can't be turned off here") == 2
    assert 'data-cw="command"' in html and 'placeholder="bin/check"' in html     # batch is on


def test_the_command_field_shows_only_with_command_or_batch(tmp_path):
    for checks, shown in ((["self"], False), (["self", "review"], False), (["self", "command"], True),
                          (["self", "batch"], True)):
        html = run(tmp_path, card(checks=checks, command="make test"))["html"][0]
        assert ('data-cw="command" value="make test"' in html) is shown, checks


def test_the_target_picker_shows_only_when_router_is_off(tmp_path):
    assert "data-tgt" not in run(tmp_path, card())["html"][0]
    html = run(tmp_path, card(router="off", target="claude:opus"))["html"][-1]
    assert "data-tgt" in html and 'value="claude" selected' in html
    html = run(tmp_path, card(problems={"target": "target is required when router is off: where everything goes"}))["html"][-1]
    assert "data-tgt" in html and "target is required when router is off" in html


def test_a_toggle_saves_the_checks_in_order(tmp_path):
    got = run(tmp_path, card(checks=["self", "review"]), {"check": "playtester", "on": True})
    assert got["saved"] == [{"checks": ["self", "review", "playtester"]}]
    got = run(tmp_path, card(checks=["self", "review"]), {"check": "review", "on": False})
    assert got["saved"] == [{"checks": ["self"]}]
    got = run(tmp_path, card(checks=["self", "batch", "train"], locked=["batch", "train"]), {"check": "review", "on": True})
    assert got["saved"] == [{"checks": ["self", "review", "batch", "train"]}]


def test_command_waits_for_its_command_then_saves_both(tmp_path):
    got = run(tmp_path, card(), {"check": "command", "on": True})
    assert got["saved"] == [] and 'data-cw="command"' in got["html"][-1]
    assert "type the project's check command to turn it on" in got["html"][-1]
    got = run(tmp_path, card(checks=["self", "command"]), {"field": "command", "value": " npm test "})
    assert got["saved"] == [{"command": "npm test"}]


def test_parallel_saves_a_number_and_problems_show(tmp_path):
    got = run(tmp_path, card(), {"field": "parallel", "value": "2"})
    assert got["saved"] == [{"parallel": 2}]
    html = run(tmp_path, card(problems={"parallel": "parallel must be a whole number ≥ 1",
                                        "": "train protects eki itself: it can't be turned off here"}))["html"][0]
    assert "parallel must be a whole number ≥ 1" in html and "train protects eki itself" in html


AFTER = ("a shell command eki runs in your folder after it lands a change (merge auto), "
         "e.g. npm run build; empty: none")


def test_after_merge_shows_with_merge_auto_and_its_help(tmp_path):
    html = run(tmp_path, card(after_merge="npm run build"))["html"][0]
    assert 'data-cw="after_merge" value="npm run build"' in html and AFTER in html
    c = card()
    del c["spec"]["merge"]                                         # unset is auto
    assert 'data-cw="after_merge" value=""' in run(tmp_path, c)["html"][0]
    for merge in ("person", "pr"):
        html = run(tmp_path, card(merge=merge, after_merge="npm run build"))["html"][0]
        assert 'data-cw="after_merge"' not in html and AFTER not in html, merge


def test_after_merge_saves_trimmed_and_problems_show_beside_it(tmp_path):
    got = run(tmp_path, card(), {"field": "after_merge", "value": "  npm run build "})
    assert got["saved"] == [{"after_merge": "npm run build"}]
    got = run(tmp_path, card(after_merge="x"), {"field": "after_merge", "value": "  "})
    assert got["saved"] == [{"after_merge": ""}]
    html = run(tmp_path, card(problems={"after_merge": "after_merge must be a shell command"}))["html"][0]
    assert re.search(r'data-cw="after_merge".*?after_merge must be a shell command', html, re.S)


def test_read_only_disables_everything_and_saves_nothing(tmp_path):
    got = run(tmp_path, card(checks=["self", "command"], command="x", editable=False), {"check": "review", "on": True})
    assert all(dis for _, _, dis in toggles(got["html"][0]))
    assert re.search(r'data-cw="command"[^>]*disabled', got["html"][0]) and got["saved"] == []


def test_the_panel_draws_the_part_and_no_checks_textarea(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "panel.js"
    stub.write_text(PANEL_STUB)
    c = dict(card(checks=["self", "review"]), choices=components.choices("workflow"),
             choice_defaults=components.choice_defaults("workflow"), choice_help=components.choice_help("workflow"))
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "cardworkflow.js"), str(WEB / "projectcardpanel.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    assert '<h4 class="pp-h">Workflow</h4>' in out["frame"] and '<h4 class="pp-h">Run</h4>' not in out["frame"]
    for k in ("checks", "command", "target", "after_merge", "parallel"):
        assert f'data-key="{k}"' not in out["settings"], k
    for k in ("router", "plan", "merge"):
        assert f'data-key="{k}"' in out["settings"], k
    assert 'data-cw-check="self"' in out["kind"]


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
process.stdout.write(JSON.stringify({ frame: box.innerHTML, settings: box.querySelector(".pp-settings").innerHTML,
  kind: box.querySelector(".pp-kind").innerHTML }));
"""
