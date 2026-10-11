"""The Workflow page (eki/web/workflowpage.js): #project/<id>/workflow loads GET /api/projects/<id>/workflow,
draws it on the canvas (workflowcanvas.js), opens a stage in the inspector (workflowinspect.js) and a card's
side node in the side panel, polls every 4 s while shown, and offers a workflow card when there is none."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough page for workflowpage.js: elements by id, a fetch that answers from a table, and fake
# canvas, inspector, panel and card panel that record what they are asked.
STUB = r"""
const log = [];
const els = {};
function El(id) {
  return { id, innerHTML: "", textContent: "", hidden: true, href: "", dataset: {}, disabled: false, on: {},
    addEventListener(t, f) { this.on[t] = f; }, insertBefore(el) { els[el.id] = el; }, appendChild(el) { log.push(["script", el.src || el.href]); } };
}
const $ = (id) => els[id] || (els[id] = El(id));
global.md = { esc: (s) => String(s).replace(/</g, "&lt;") };
global.document = { getElementById: (id) => id === "workflow-view" ? els[id] || null : $(id), createElement: () => El(""), querySelector: () => null, head: $("head") };
const timers = [];
global.setInterval = (f, ms) => { timers.push({ f, ms, on: true }); return timers.length; };
global.clearInterval = (n) => { if (n && timers[n - 1]) timers[n - 1].on = false; };
const scene = JSON.parse(process.argv[3]);
const answers = scene.answers;
global.fetch = async (url, opt) => {
  log.push(["fetch", url, opt && opt.method ? opt.method : "GET", opt && opt.body ? JSON.parse(opt.body) : null,
            opt && opt.headers ? opt.headers["X-Eki"] || "" : ""]);
  const got = answers[url] || { error: "nope" };
  return { ok: url in answers, statusText: "bad", json: async () => got };   // a broken card is a 200 with error
};
let handlers = null, selected = "unset";
let hosted = null;
global.window = {
  workflowCanvas: { mount(el, h) { handlers = h; log.push(["mount", el.id]);
    return { draw(d, sel) { log.push(["draw", d.name, sel]); }, fit() { log.push(["fit"]); }, unmount() { log.push(["unmount"]); } }; } },
  workflowInspect: { open(pid, d, id, reload) { selected = id; log.push(["inspect", pid, id, typeof reload]); },
    update(d) { log.push(["update", d.name]); }, close() { log.push(["iclose"]); }, current() { return selected === "unset" ? null : selected; } },
  panel: { host(o) { hosted = o; log.push(["host", o.name]); o.draw($("panel-body")); }, owner: () => hosted,
    close() { log.push(["pclose"]); const o = hosted; hosted = null; if (o) o.close("close"); } },
  projectCardPanel: { open(body, pid, card, ro, ctx) { log.push(["card", pid, card.name, ro, Object.keys(ctx).sort()]); },
    update() {}, close() {} },
  nav: { register(name, v) { global.view = [name, v]; } },
};
global.panel = window.panel; global.projectCardPanel = window.projectCardPanel; global.nav = window.nav;
require(process.argv[2]);
const tick = () => new Promise((r) => setTimeout(r, 5));
(async () => {
  const [name, v] = global.view;
  log.push(["registered", name]);
  v.show(scene.pid);
  await tick();
  for (const step of scene.steps) {
    if (step === "poll") { for (const t of timers) if (t.on) t.f(); }
    else if (step === "hide") v.hide();
    else if (step === "add") await els["workflow-view"].on.click({ target: { closest: (s) => s === "[data-wf-add]" ? {} : null } });
    else if (step.stage) handlers.stage(step.stage);
    else if (step.feed) handlers.feed(step.feed);
    await tick();
  }
  process.stdout.write(JSON.stringify({ log, timers: timers.map((t) => [t.ms, t.on]),
    title: els.title.textContent, meta: els.meta.textContent, note: $("wf-note").innerHTML,
    back: [$("wf-back").href, $("wf-back").textContent], hidden: els["workflow-view"].hidden }));
})();
"""

FLOW = {"project": "p1", "name": "holo-cards", "card": "workflow", "error": "", "editable": True, "eki": False,
        "line": "plan split · 4 parallel · checks: self · merge auto", "stages": [], "feeds": []}


def run(tmp_path, steps=(), answers=None, pid="p1"):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    scene = {"pid": pid, "steps": list(steps),
             "answers": answers if answers is not None else {"/api/projects/p1/workflow": FLOW}}
    out = subprocess.run(["node", str(stub), str(WEB / "workflowpage.js"), json.dumps(scene)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def of(got, kind):
    return [x for x in got["log"] if x[0] == kind]


def test_the_page_loads_draws_and_polls_until_hidden(tmp_path):
    got = run(tmp_path, ["poll", "hide"])
    assert ["registered", "workflow"] in got["log"]
    assert of(got, "mount") == [["mount", "wf-canvas"]]
    assert [x[1] for x in of(got, "fetch")] == ["/api/projects/p1/workflow"] * 2
    assert of(got, "draw")[0] == ["draw", "holo-cards", None] and of(got, "fit") == [["fit"]]   # fit once, on open
    assert of(got, "update") == [["update", "holo-cards"]] * 2
    assert got["timers"] == [[4000, False]]                     # every 4 s, stopped on hide
    assert ["iclose"] in got["log"] and ["unmount"] in got["log"] and got["hidden"]
    assert got["title"] == "holo-cards · Workflow" and got["meta"] == FLOW["line"]
    assert got["back"] == ["#project/p1", "← holo-cards"]
    assert "No workflow card" not in got["note"]


def test_a_stage_click_opens_the_inspector_and_redraws_with_it_selected(tmp_path):
    got = run(tmp_path, [{"stage": "review"}])
    assert of(got, "inspect") == [["inspect", "p1", "review", "function"]]
    assert of(got, "draw")[-1] == ["draw", "holo-cards", "review"]


def test_a_feed_with_a_card_hosts_it_and_the_command_feed_opens_its_stage(tmp_path):
    answers = {"/api/projects/p1/workflow": FLOW,
               "/api/projects/p1/cards": {"cards": [{"name": "knowledge", "kind": "knowledge"}], "readonly": ""},
               "/api/projects/p1/cards/knowledge": {"card": {"name": "knowledge", "kind": "knowledge", "spec": {}}}}
    got = run(tmp_path, [{"feed": {"card": "knowledge", "kind": "knowledge", "to": "plan"}},
                         {"feed": {"card": "", "kind": "command", "to": "command"}}, "hide"], answers)
    assert ["fetch", "/api/projects/p1/cards", "GET", None, ""] in got["log"]
    assert ["fetch", "/api/projects/p1/cards/knowledge", "GET", None, ""] in got["log"]   # the panel gets the details
    assert of(got, "host") == [["host", "knowledge"]]
    assert of(got, "card") == [["card", "p1", "knowledge", "", ["ago", "api", "close", "reload"]]]
    assert of(got, "inspect") == [["inspect", "p1", "command", "function"]]


def test_hide_closes_the_card_panel_it_owns(tmp_path):
    answers = {"/api/projects/p1/workflow": FLOW,
               "/api/projects/p1/cards": {"cards": [{"name": "serve", "kind": "serve"}], "readonly": ""},
               "/api/projects/p1/cards/serve": {"card": {"name": "serve", "kind": "serve"}}}
    got = run(tmp_path, [{"feed": {"card": "serve", "kind": "serve", "to": "build"}}, "hide"], answers)
    assert of(got, "pclose") == [["pclose"]]


def test_no_card_shows_the_defaults_banner_and_adds_the_template(tmp_path):
    answers = {"/api/projects/p1/workflow": {**FLOW, "card": ""}, "/api/projects/p1/cards": {"card": "workflow"}}
    got = run(tmp_path, ["add"], answers)
    assert "No workflow card: these are the defaults." in got["note"] and "+ Add workflow card" in got["note"]
    assert ["fetch", "/api/projects/p1/cards", "POST", {"template": "workflow"}, "1"] in got["log"]
    assert [x[1] for x in of(got, "fetch")].count("/api/projects/p1/workflow") == 2   # reloaded after


def test_a_broken_card_and_a_failed_load_say_so(tmp_path):
    got = run(tmp_path, answers={"/api/projects/p1/workflow": {**FLOW, "error": "checks: batch needs command"}})
    assert "is broken" in got["note"] and "checks: batch needs command" in got["note"]
    broken = {**FLOW, "card": "", "error": "checks: train is eki's own"}           # a broken card is no card,
    got = run(tmp_path, answers={"/api/projects/p1/workflow": broken})            # but not "add one"
    assert "is broken" in got["note"] and "No workflow card" not in got["note"]
    got = run(tmp_path, answers={})
    assert "Can&#x27;t read the workflow" in got["note"] or "Can't read the workflow" in got["note"]


def test_the_files_stay_small():
    for f in ("workflowpage.js", "workflowpage.css", "nav.js", "project.js"):
        assert len((WEB / f).read_text().splitlines()) < 400, f
    assert 'nav.register("workflow"' in (WEB / "workflowpage.js").read_text()
    assert "/ui/workflowpage.css" in (WEB / "workflowpage.js").read_text()
