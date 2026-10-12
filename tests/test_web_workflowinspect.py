"""workflowinspect.js shows one stage of a project's workflow in the side panel, for the JSON of
GET /api/projects/<pid>/workflow: what the stage does, its settings with the workflow card's controls
(router dropdown and the target picker when off, parallel, a switch per check, the command, the
playtester card, merge, after_merge), each saved at once into the workflow card, and "Now here": the
items at the stage, each opening its detail. Run through node with a fake panel.host and fetch."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eki import components

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "workflowinspect.js").read_text()
IDS = ["request", "router", "plan", "build", "self", "review", "command", "batch", "playtester", "train",
       "merge", "after_merge"]


def test_the_file_is_small_parses_and_loads_its_look():
    assert len(JS.splitlines()) < 400
    assert 'href="/ui/workflowinspect.css"' in JS and (WEB / "workflowinspect.css").is_file()
    assert "window.workflowInspect = { open, update, close, current" in JS
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    out = subprocess.run(["node", "--check", str(WEB / "workflowinspect.js")], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr


STUB = r"""
const fs = require("fs");
const [, , cm, st, wr, wi, args] = process.argv;
const [data, stageId, steps, scene] = JSON.parse(args);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
function el() {
  return { _h: "", dataset: {}, listeners: {}, kids: [],
    get innerHTML() { return this._h; }, set innerHTML(v) { this._h = String(v); this.kids = []; },
    appendChild(k) { this.kids.push(k); return k; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    contains() { return false; }, querySelectorAll() { return []; } };
}
global.window = global; global.md = { esc };
global.confirm = (q) => { out.asked = (out.asked || []).concat(q); return !scene.no; };
global.document = { querySelector: () => ({}), createElement: el, head: el(), activeElement: null, addEventListener() {} };
const out = { posts: [], html: [], picks: 0, title: null, reloads: 0 };
eval(fs.readFileSync(cm, "utf8"));
eval(fs.readFileSync(st, "utf8"));
eval(fs.readFileSync(wr, "utf8"));
let onPick = null;
const attach = choiceMenu.attach;
choiceMenu.attach = (root, fn) => { onPick = fn; out.picks++; };
const body = el();
let owner = null;
global.panel = { host(o) { owner = o; out.title = [o.name, o.sub]; o.draw(body); }, owner: () => owner,
  close() { const o = owner; owner = null; if (o) o.close("close"); }, retitle(n, s) { out.title = [n, s]; } };
global.fetch = async (url, opts) => {
  if (url === "/api/models") return { ok: true, json: async () => ({ providers: { claude: { listed: null, lists: false } } }) };
  out.posts.push([url, JSON.parse(opts.body)]);
  if (url.startsWith("/api/self/items/")) {
    if (scene.fail) return { ok: false, statusText: "Bad", json: async () => ({ error: scene.fail }) };
    return { ok: true, json: async () => ({ result: "done" }) };
  }
  if (scene.refuse) return { ok: false, statusText: "Bad", json: async () => ({ error: "no", problems: scene.refuse }) };
  const set = JSON.parse(opts.body).settings;                 // saved as the server does: null removes
  data.spec = Object.assign({}, data.spec, set);
  for (const k of Object.keys(set)) if (set[k] === null) delete data.spec[k];
  return { ok: true, json: async () => ({ card: { spec: data.spec } }) };
};
eval(fs.readFileSync(wi, "utf8"));
const root = () => body.kids[body.kids.length - 1];
const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0)); };
const reload = () => { out.reloads++; return Promise.resolve(scene.after || data); };
(async () => {
  workflowInspect.open("p1", data, stageId, reload);
  await tick();
  out.html.push(root().innerHTML);
  for (const s of steps) {
    const r = root();
    if (s.check) await r.listeners.change[0]({ target: { dataset: { wiCheck: s.check }, checked: s.on } });
    else if (s.field) await r.listeners.change[0]({ target: { dataset: { wi: s.field }, value: s.value } });
    else if (s.pick) await onPick(s.pick[0], s.pick[1]);
    else if (s.target) {
      const wrap = { querySelector: (a) => (a === "[data-tp]" ? { value: s.target } : null) };
      await r.listeners.change[0]({ target: { dataset: {}, closest: (q) => (q === "[data-tgt]" ? wrap : null),
        hasAttribute: (a) => a === "data-tp", tagName: "SELECT" } });
    } else if (s.item || s.back) {
      const hit = s.item ? { cls: ".wi-item", dataset: { wiItem: s.item } } : { cls: ".wi-back", dataset: { wiBack: "1" } };
      await r.listeners.click[0]({ target: { closest: (q) => (q === hit.cls ? hit : null) }, preventDefault() {} });
    } else if (s.click) {
      const hit = { dataset: s.click[1] };
      await r.listeners.click[0]({ target: { closest: (q) => (q === s.click[0] ? hit : null) }, preventDefault() {} });
    } else if (s.act) {
      const hit = { dataset: { wiAct: s.act[0], wiId: s.act[1] } };
      await r.listeners.click[0]({ target: { closest: (q) => (q === "[data-wi-act]" ? hit : null) }, preventDefault() {} });
    } else if (s.row) {
      await r.listeners.change[0]({ target: { dataset: { wrRow: String(s.row[0]), wrKey: s.row[1] }, value: s.row[2] } });
    } else if (s.update) workflowInspect.update(s.update);
    else if (s.close) workflowInspect.close();
    await tick();
    out.html.push(root().innerHTML);
  }
  out.current = workflowInspect.current();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


def item(i, state="building", since=None, **kw):
    import time
    return {"id": i, "kind": "item", "title": f"item {i}", "state": state,
            "since": since if since is not None else time.time() - 300, "where": "", "note": "",
            "thread": "", "goal": "", **kw}


def wf(eki=False, editable=True, card="workflow", items=None, **spec):
    locked = ["batch", "train"] if eki else []
    sp = {"kind": "workflow", "router": "on", "plan": "split", "parallel": 4, "checks": ["self"], "merge": "auto", **spec}
    on = sp["checks"]
    stages = [{"id": s, "title": s.replace("_", "-").capitalize(), "summary": f"{s} summary",
               "what": f"What {s} does.", "on": s not in ("review", "command", "batch", "playtester", "train") or s in on,
               "locked": s in locked, "keys": [], "count": len((items or {}).get(s, [])), "alert": "",
               "items": (items or {}).get(s, [])} for s in IDS]
    return {"project": "p1", "name": "p1", "card": card, "error": "" if editable else "the card is broken",
            "editable": editable, "eki": eki, "line": "", "spec": sp,
            "choices": components.choices("workflow"), "choice_defaults": components.choice_defaults("workflow"),
            "choice_help": components.choice_help("workflow"), "locked": locked, "stages": stages, "feeds": []}


def run(tmp_path, data, stage, steps=(), **scene):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "workflowrules.js"), str(WEB / "workflowinspect.js"), json.dumps([data, stage, list(steps), scene])],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def switch(html):
    m = re.search(r'<input type="checkbox" data-wi-check="(\w+)"([^>]*)>', html)
    return m and (m.group(1), " checked" in m.group(2), " disabled" in m.group(2))


SAVE = "/api/projects/p1/cards/workflow"


# ---- each stage draws its settings ----------------------------------------------------------------

def test_router_is_a_dropdown_with_its_help_and_the_title_is_the_stage(tmp_path):
    got = run(tmp_path, wf(), "router")
    html = got["html"][0]
    assert got["title"] == ["Router", "Workflow"] and got["current"] == "router" and got["picks"] == 1
    assert 'data-key="router"' in html and "cm-btn" in html and "What router does." in html
    assert "Requests are routed: rules first" in html and "data-tgt" not in html


def test_router_off_shows_the_target_picker(tmp_path):
    html = run(tmp_path, wf(router="off", target="claude:opus"), "router")["html"][0]
    assert "data-tgt" in html and 'value="claude" selected' in html
    assert "router off: everything goes here" in html


def test_build_parallel_merge_after_merge_and_command(tmp_path):
    assert re.search(r'data-wi="parallel" value="2"', run(tmp_path, wf(parallel=2), "build")["html"][0])
    html = run(tmp_path, wf(merge="pr"), "merge")["html"][0]
    assert 'data-key="merge"' in html and "A pull request on GitHub." in html
    html = run(tmp_path, wf(after_merge="npm run build"), "after_merge")["html"][0]
    assert 'data-wi="after_merge" value="npm run build"' in html and "empty: off" in html
    html = run(tmp_path, wf(checks=["self", "command"], command="make test"), "command")["html"][0]
    assert switch(html) == ("command", True, False) and 'data-wi="command" value="make test"' in html
    html = run(tmp_path, wf(checks=["self", "playtester"]), "playtester")["html"][0]
    assert switch(html) == ("playtester", True, False) and 'data-wi="playtester" value="playtester"' in html
    assert "nothing to set" in run(tmp_path, wf(), "request")["html"][0]


def test_self_is_always_on_and_eki_locked_checks_are_fixed(tmp_path):
    html = run(tmp_path, wf(), "self")["html"][0]
    assert switch(html) == ("self", True, True) and "always on" in html
    ek = wf(eki=True, checks=["self", "batch", "train"])
    for s in ("batch", "train"):
        html = run(tmp_path, ek, s)["html"][0]
        assert switch(html) == (s, True, True), s
        assert "protects eki itself" in html
    assert switch(run(tmp_path, ek, "review")["html"][0]) == ("review", False, False)
    html = run(tmp_path, wf(), "train")["html"][0]                 # train is eki's own
    assert switch(html) == ("train", False, True) and "only eki's own project has the train" in html


# ---- a change is saved into the card --------------------------------------------------------------

def test_a_switch_saves_the_whole_checks_list_in_order_then_reloads(tmp_path):
    got = run(tmp_path, wf(checks=["self", "review"]), "playtester", [{"check": "playtester", "on": True}])
    assert got["posts"] == [[SAVE, {"settings": {"checks": ["self", "review", "playtester"]}}]] and got["reloads"] == 1
    got = run(tmp_path, wf(checks=["self", "review"]), "review", [{"check": "review", "on": False}])
    assert got["posts"] == [[SAVE, {"settings": {"checks": ["self"]}}]]
    got = run(tmp_path, wf(eki=True, checks=["self", "batch", "train"]), "review", [{"check": "review", "on": True}])
    assert got["posts"] == [[SAVE, {"settings": {"checks": ["self", "review", "batch", "train"]}}]]


def test_command_waits_for_its_command_then_saves_both(tmp_path):
    got = run(tmp_path, wf(checks=["self", "review"]), "command",
              [{"check": "command", "on": True}, {"field": "command", "value": " npm test "}])
    assert "type the project's check command to turn it on" in got["html"][1]
    assert got["posts"] == [[SAVE, {"settings": {"command": "npm test", "checks": ["self", "review", "command"]}}]]
    got = run(tmp_path, wf(), "batch", [{"check": "batch", "on": True}])
    assert got["posts"] == [] and 'data-wi="command"' in got["html"][1]


def test_router_off_then_a_target_and_other_fields_save(tmp_path):
    got = run(tmp_path, wf(), "router", [{"pick": ["router", "off"]}],
              refuse={"target": "target is required when router is off: where everything goes"})
    assert got["posts"] == [[SAVE, {"settings": {"router": "off"}}]]
    assert "data-tgt" in got["html"][-1] and "target is required when router is off" in got["html"][-1]
    got = run(tmp_path, wf(), "router", [{"target": "claude"}])
    assert got["posts"] == [[SAVE, {"settings": {"target": "claude", "router": "off"}}]]
    got = run(tmp_path, wf(), "build", [{"field": "parallel", "value": "2"}])
    assert got["posts"] == [[SAVE, {"settings": {"parallel": 2}}]]
    got = run(tmp_path, wf(), "after_merge", [{"field": "after_merge", "value": "  "}])
    assert got["posts"] == [[SAVE, {"settings": {"after_merge": ""}}]]
    got = run(tmp_path, wf(), "merge", [{"pick": ["merge", "person"]}])
    assert got["posts"] == [[SAVE, {"settings": {"merge": "person"}}]]


def test_the_servers_problem_shows_beside_the_field(tmp_path):
    got = run(tmp_path, wf(), "build", [{"field": "parallel", "value": "0"}],
              refuse={"parallel": "parallel must be a whole number ≥ 1"})
    assert re.search(r'data-wi="parallel".*?parallel must be a whole number ≥ 1', got["html"][-1], re.S)


def test_not_editable_disables_with_the_reason_and_saves_nothing(tmp_path):
    got = run(tmp_path, wf(editable=False, checks=["self", "review"]), "review", [{"check": "review", "on": False}])
    assert switch(got["html"][0]) == ("review", True, True) and "Read-only: the card is broken." in got["html"][0]
    assert got["posts"] == []
    html = run(tmp_path, wf(card="", editable=False), "build")["html"][0]
    assert "no workflow card yet" in html and re.search(r'data-wi="parallel"[^>]*disabled', html)
    assert re.search(r'data-key="router"[^>]*disabled', run(tmp_path, wf(card="", editable=False), "router")["html"][0])


# ---- what's at the stage now ----------------------------------------------------------------------

def test_items_listed_with_state_and_age_and_one_opens_its_detail(tmp_path):
    its = {"build": [item("a1", "building"), item("a2", "failed", note="tests red", thread="t9", where="/w/a2")]}
    got = run(tmp_path, wf(items=its), "build", [{"item": "a2"}, {"back": True}])
    html = got["html"][0]
    assert "item a1" in html and "item a2" in html and "building · 5m ago" in html and "failed · 5m ago" in html
    detail = got["html"][1]
    assert "← back to Build" in detail and "tests red" in detail and 'href="#t9"' in detail and "/w/a2" in detail
    assert "item a1" not in detail
    assert "item a1" in got["html"][2] and "Now here" in got["html"][2]
    assert "nothing here now" in run(tmp_path, wf(), "plan")["html"][0]


def test_update_redraws_with_fresh_data_and_close_lets_go(tmp_path):
    fresh = wf(items={"build": [item("b7", "queued")]})
    got = run(tmp_path, wf(), "build", [{"update": fresh}, {"close": True}])
    assert "nothing here now" in got["html"][0] and "item b7" in got["html"][1]
    assert got["current"] is None


# ---- the command as rules by path, and batch_command ----------------------------------------------

GAME = [{"name": "story files", "paths": ["design/script/**", "design/quests/**", "slice/data/**"],
         "run": "npm run check:story"},
        {"name": "code", "paths": ["slice/src/**", "slice/tools/**", "tools/**"],
         "run": "npm run typecheck && npm run build"}]


def test_rules_files_are_small_parse_and_load_before_the_panel():
    rules = WEB / "workflowrules.js"
    assert len(rules.read_text().splitlines()) < 400
    page = (WEB / "index.html").read_text()
    assert page.index("/ui/workflowrules.js") < page.index("/ui/workflowinspect.js")
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    out = subprocess.run(["node", "--check", str(rules)], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr


def test_a_rules_card_draws_its_rows(tmp_path):
    html = run(tmp_path, wf(checks=["self", "command"], command=GAME), "command")["html"][0]
    assert 'class="wr-mode wr-on" data-wr-mode="rules"' in html and 'data-wr-mode="text"' in html
    assert 'data-wr-row="0" data-wr-key="name" value="story files"' in html
    assert "design/script/**\ndesign/quests/**\nslice/data/**</textarea>" in html
    assert 'data-wr-row="1" data-wr-key="run" value="npm run typecheck &amp;&amp; npm run build"' in html
    assert 'data-wr-del="1"' in html and "data-wr-add" in html and 'data-wi="command"' not in html
    html = run(tmp_path, wf(checks=["self", "command"], command="make test"), "command")["html"][0]
    assert 'class="wr-mode wr-on" data-wr-mode="text"' in html and 'data-wi="command" value="make test"' in html


def test_editing_a_row_saves_the_rules_as_a_list_without_blank_names(tmp_path):
    rules = [{"paths": ["docs/**"], "run": "make docs"}]
    got = run(tmp_path, wf(checks=["self", "command"], command=rules), "command",
              [{"row": [0, "paths", "docs/**, *.md\n README"]}, {"row": [0, "name", "docs"]}])
    assert got["posts"] == [
        [SAVE, {"settings": {"command": [{"paths": ["docs/**", "*.md", "README"], "run": "make docs"}]}}],
        [SAVE, {"settings": {"command": [{"name": "docs", "paths": ["docs/**", "*.md", "README"], "run": "make docs"}]}}]]


def test_switching_between_text_and_rules(tmp_path):
    got = run(tmp_path, wf(checks=["self", "command"], command="make test"), "command",
              [{"click": ["[data-wr-mode]", {"wrMode": "rules"}]}, {"row": [0, "paths", "src/**"]},
               {"click": ["[data-wr-mode]", {"wrMode": "text"}]}])
    first = got["html"][1]
    assert 'data-wr-row="0" data-wr-key="run" value="make test"' in first
    assert "give each rule its paths and its command to save" in first
    assert got["posts"][0] == [SAVE, {"settings": {"command": [{"paths": ["src/**"], "run": "make test"}]}}]
    assert got["posts"][1] == [SAVE, {"settings": {"command": "make test"}}]
    assert 'data-wi="command" value="make test"' in got["html"][-1]


def test_add_and_remove_a_row(tmp_path):
    got = run(tmp_path, wf(checks=["self", "command"], command=GAME), "command",
              [{"click": ["[data-wr-add]", {"wrAdd": "1"}]}, {"row": [2, "paths", "*.md"]},
               {"row": [2, "run", "npm run lint:md"]}, {"click": ["[data-wr-del]", {"wrDel": "0"}]}])
    assert 'data-wr-row="2"' in got["html"][1] and got["posts"][0][1]["settings"]["command"][2] == \
        {"paths": ["*.md"], "run": "npm run lint:md"}
    assert got["posts"][-1] == [SAVE, {"settings": {"command": [GAME[1], {"paths": ["*.md"], "run": "npm run lint:md"}]}}]
    assert len(got["posts"]) == 2                                      # the half-typed row wasn't sent


def test_batch_command_saves_and_batch_on_a_rules_card_waits_for_it(tmp_path):
    got = run(tmp_path, wf(checks=["self", "command", "batch"], command=GAME, batch_command="npm run build"), "batch",
              [{"field": "batch_command", "value": " npm run build:all "}, {"field": "batch_command", "value": ""}])
    assert 'data-wi="batch_command" value="npm run build"' in got["html"][0]
    assert got["posts"] == [[SAVE, {"settings": {"batch_command": "npm run build:all"}}],
                            [SAVE, {"settings": {"batch_command": None}}]]
    got = run(tmp_path, wf(checks=["self", "command"], command=GAME), "batch",
              [{"check": "batch", "on": True}, {"field": "batch_command", "value": "npm run build"}])
    assert "type what runs on the changes together to turn batch on" in got["html"][1]
    assert got["posts"] == [[SAVE, {"settings": {"batch_command": "npm run build",
                                                 "checks": ["self", "command", "batch"]}}]]
    html = run(tmp_path, wf(checks=["self", "command", "batch"], command="make test"), "batch")["html"][0]
    assert 'placeholder="same as each change: make test"' in html
    assert 'data-wi="batch_command"' not in run(tmp_path, wf(eki=True, checks=["self", "batch", "train"]), "batch")["html"][0]


def test_the_servers_objection_shows_beside_the_rules_and_batch_command(tmp_path):
    got = run(tmp_path, wf(checks=["self", "command"], command=GAME), "command",
              [{"row": [0, "paths", "!design/**"]}], refuse={"": "paths: `!` patterns aren't supported"})
    assert re.search(r'data-wr-add.*?paths: `!` patterns aren&#39;t supported|data-wr-add.*?paths: `!` patterns aren\'t supported',
                     got["html"][-1], re.S)
    assert got["html"][-1].count("patterns aren") == 1
    got = run(tmp_path, wf(checks=["self", "command", "batch"], command=GAME, batch_command="x"), "batch",
              [{"field": "batch_command", "value": "y"}], refuse={"batch_command": "batch_command must be text"})
    assert re.search(r'data-wi="batch_command".*?batch_command must be text', got["html"][-1], re.S)


# ---- what needs a look: why, and Retry / Drop / Open thread ---------------------------------------

def _left(**kw):
    return item("l1", "left", error="the agent changed nothing", needs_look=True, thread="t4", **kw)


def test_an_item_that_needs_a_look_says_why_with_retry_drop_and_its_thread(tmp_path):
    data = wf(items={"build": [_left(), item("b2", "building", needs_look=False)]})
    by = {s["id"]: s for s in data["stages"]}
    by["build"].update(alert=True, alert_count=1)
    html = run(tmp_path, data, "build")["html"][0]
    assert "1 needs a look" in html
    assert html.count('class="wi-look"') == 1
    assert '<span class="wi-why" title="the agent changed nothing">the agent changed nothing</span>' in html
    assert 'data-wi-act="retry" data-wi-id="l1">Retry<' in html and 'data-wi-act="drop" data-wi-id="l1">Drop<' in html
    assert '<a class="wi-btn" href="#t4">Open thread</a>' in html
    assert "<button" not in html.split('class="wi-item"')[1].split("</a>")[0]      # no button inside the link


def test_the_reason_falls_back_to_the_note_and_retry_is_only_for_what_can_be_retried(tmp_path):
    its = [item("p1", "proposed", needs_look=True, error="", note="ready to apply"),
           item("g9", "failed", kind="goal", needs_look=True, error="planner timed out")]
    html = run(tmp_path, wf(items={"merge": its}), "merge")["html"][0]
    assert "ready to apply</span>" in html and "planner timed out</span>" in html
    assert 'data-wi-act="retry"' not in html
    assert 'data-wi-act="drop" data-wi-id="p1"' in html and 'data-wi-id="g9"' not in html   # goals: no item route
    assert 'disabled title="no thread yet">Open thread' in html


def test_retry_and_drop_post_the_item_route_then_reload(tmp_path):
    data = wf(items={"build": [_left()]})
    after = wf(items={"build": []})
    got = run(tmp_path, data, "build", [{"act": ["retry", "l1"]}, {"act": ["drop", "l1"]}], after=after)
    assert got["posts"] == [["/api/self/items/l1/retry", {}], ["/api/self/items/l1/drop", {}]]
    assert got["asked"] == ["Drop item l1?"] and got["reloads"] == 2
    assert "nothing here now" in got["html"][-1]


def test_drop_asks_first_and_a_refusal_shows_beside_the_item(tmp_path):
    got = run(tmp_path, wf(items={"build": [_left()]}), "build", [{"act": ["drop", "l1"]}], no=True)
    assert got["posts"] == [] and got["reloads"] == 0
    got = run(tmp_path, wf(items={"build": [_left()]}), "build", [{"act": ["retry", "l1"]}], fail="item is building")
    assert "item is building" in got["html"][-1]
