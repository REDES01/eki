"""The workflow graph in the window: workflowcanvas.js lays out the JSON of GET /api/projects/<pid>/workflow
(stages left to right in pipeline order, an arrow per consecutive pair with the next stage's count, the
cards that feed a stage as side nodes above or below the row with a link to every target) and draws it as
SVG — off stages wf-off, locked ones a .wf-lock, alerts a .wf-dot, counted edges a .wf-badge — calling
handlers.stage(id) / handlers.feed(feed) on a click, keeping the pan and zoom between draws."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "workflowcanvas.js").read_text()


def stage(id, title, summary="", on=True, locked=False, count=0, alert=False, alert_count=0):
    return {"id": id, "title": title, "summary": summary, "what": "", "on": on, "locked": locked, "keys": [],
            "count": count, "alert": alert, "alert_count": alert_count, "items": []}


def holo():
    """holo-cards: router off, plan split, 4 in parallel, checks self + playtester, merge auto, after_merge."""
    return {"project": "holo-cards", "name": "workflow", "editable": True, "eki": False, "locked": [],
            "stages": [stage("request", "Request in", count=1),
                       stage("router", "Router", "straight to claude:opus", on=False),
                       stage("plan", "Planner", "split into items", count=2),
                       stage("build", "Build ×N", "4 in parallel", count=3, alert=True),
                       stage("self", "Self check", "the agent checks its own work"),
                       stage("review", "Review", "a second reader", on=False),
                       stage("playtester", "Playtester", "plays the change", count=1),
                       stage("merge", "Merge", "auto"),
                       stage("after_merge", "After-merge", "npm run build")],
            "feeds": [{"card": "notes", "kind": "knowledge", "label": "notes", "to": ["plan", "build"],
                       "note": "picked per item"},
                      {"card": "instructions", "kind": "instructions", "label": "instructions", "to": ["plan", "build"],
                       "note": ""},
                      {"card": "art", "kind": "skills", "label": "art <skill>", "to": ["build"], "note": ""},
                      {"card": "site", "kind": "serve", "label": "site", "to": ["build", "playtester"], "note": ""},
                      {"card": "tester", "kind": "playtester", "label": "tester", "to": ["playtester"], "note": ""}]}


def eki():
    ids = ["request", "router", "plan", "build", "self", "review", "command", "batch", "train", "merge"]
    stages = [stage(i, i.title()) for i in ids]
    for s in stages:
        s["locked"] = s["id"] in ("batch", "train")
    return {"project": "eki", "name": "workflow", "editable": True, "eki": True, "locked": ["batch", "train"],
            "stages": stages,
            "feeds": [{"card": "check", "kind": "command", "label": "bin/check", "to": ["command"], "note": ""}]}


# A node stub: draw into a fake element, read its markup, click what the args name.
STUB = r"""
const fs = require("fs");
const [, , file, args] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}), head: null };
eval(fs.readFileSync(file, "utf8"));
const [data, clicks, selected] = JSON.parse(args);
const world = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
const el = { _h: "", listeners: {}, clientWidth: 1200, clientHeight: 600, cls: new Set(),
  classList: { add(...c) { c.forEach((x) => el.cls.add(x)); }, remove(...c) { c.forEach((x) => el.cls.delete(x)); } },
  get innerHTML() { return this._h; }, set innerHTML(v) { this._h = String(v); },
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
  removeEventListener(ev, fn) { this.listeners[ev] = (this.listeners[ev] || []).filter((f) => f !== fn); },
  getBoundingClientRect: () => ({ left: 0, top: 0 }),
  querySelector: (sel) => (sel === ".wf-world" ? world : null) };
const got = { stages: [], feeds: [] };
const c = workflowCanvas.mount(el, { stage: (id) => got.stages.push(id), feed: (f) => got.feeds.push(f.card) });
c.draw(data, selected);
const out = { layout: workflowCanvas.layout(data), html: el.innerHTML, first: c.view(), cls: [...el.cls] };
const fire = (ev, e) => el.listeners[ev].forEach((fn) => fn(e));
const target = (attr, v) => ({ closest: (sel) => (sel.includes(attr) ? { getAttribute: () => v } : null) });
for (const k of clicks) {
  if (k.stage) fire("click", { target: target("data-stage", k.stage) });
  if (k.feed != null) fire("click", { target: target("data-feed", String(k.feed)) });
  if (k.wheel) fire("wheel", { ...k.wheel, clientX: 600, clientY: 300, preventDefault() {} });
  if (k.drag) {
    const t = { closest: () => null };
    fire("pointerdown", { button: 0, target: t, clientX: 100, clientY: 100 });
    fire("pointermove", { clientX: 100 + k.drag[0], clientY: 100 + k.drag[1] });
    fire("pointerup", {});
  }
  if (k.redraw) c.draw(data, k.redraw);
}
out.after = c.view(); out.world = world.attrs.transform || ""; out.html2 = el.innerHTML; out.got = got;
c.unmount();
out.gone = el.innerHTML === "" && Object.values(el.listeners).every((l) => !l.length);
process.stdout.write(JSON.stringify(out));
"""


def run(tmp_path, data, clicks=(), selected=None):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "workflowcanvas.js"), json.dumps([data, list(clicks), selected])],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_the_file_is_small_parses_and_loads_its_css():
    assert len(JS.splitlines()) < 400
    assert 'href = "/ui/workflowcanvas.css"' in JS and (WEB / "workflowcanvas.css").is_file()
    assert "window.workflowCanvas = { layout, mount }" in JS
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    out = subprocess.run(["node", "--check", str(WEB / "workflowcanvas.js")], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr


@pytest.mark.parametrize("fixture", [holo, eki])
def test_stages_left_to_right_in_order_with_an_edge_per_pair(tmp_path, fixture):
    data = fixture()
    L = run(tmp_path, data)["layout"]
    ids = [s["id"] for s in data["stages"]]
    assert [n["id"] for n in L["nodes"]] == ids
    xs = [n["x"] for n in L["nodes"]]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)
    assert len({n["y"] for n in L["nodes"]}) == 1                          # one row
    assert [(e["from"], e["to"]) for e in L["edges"]] == list(zip(ids, ids[1:]))
    counts = {s["id"]: s["count"] for s in data["stages"]}
    assert all(e["count"] == counts[e["to"]] for e in L["edges"])
    if fixture is eki:
        assert "train" in ids


def test_feeds_sit_off_the_row_near_their_first_target_with_a_link_per_target(tmp_path):
    data = holo()
    L = run(tmp_path, data)["layout"]
    nodes = {n["id"]: n for n in L["nodes"]}
    row = L["nodes"][0]
    assert len(L["feeds"]) == len(data["feeds"])
    for f, b in zip(data["feeds"], L["feeds"]):
        assert b["w"] < row["w"] and b["h"] < row["h"]                     # smaller
        assert b["y"] + b["h"] < row["y"] or b["y"] > row["y"] + row["h"]  # above or below the row
        first = nodes[f["to"][0]]
        assert abs((b["x"] + b["w"] / 2) - (first["x"] + first["w"] / 2)) < 2 * (row["w"] + 64)
    for side in (lambda b: b["y"] < 0, lambda b: b["y"] > 0):              # none overlap on a side
        boxes = sorted((b for b in L["feeds"] if side(b)), key=lambda b: b["x"])
        assert all(a["x"] + a["w"] <= b["x"] for a, b in zip(boxes, boxes[1:]))
    want = [(i, t) for i, f in enumerate(data["feeds"]) for t in f["to"]]
    assert [(l["feed"], l["to"]) for l in L["links"]] == want
    assert [l["note"] for l in L["links"] if l["feed"] == 0] == ["picked per item", ""]        # once


def test_the_markup_marks_off_locked_alert_counted_and_feeds(tmp_path):
    data = holo()
    html = run(tmp_path, data, selected="plan")["html"]
    nodes = dict(re.findall(r'<g class="([^"]*)" data-stage="(\w+)"', html)[i][::-1] for i in range(len(data["stages"])))
    assert list(nodes) == [s["id"] for s in data["stages"]]
    assert {k for k, c in nodes.items() if "wf-off" in c} == {"router", "review"}
    assert "wf-sel" in nodes["plan"] and sum("wf-sel" in c for c in nodes.values()) == 1
    assert html.count('class="wf-dot"') == 1 and "wf-alert" in nodes["build"]
    assert html.count('class="wf-lock"') == 0
    assert html.count('class="wf-badge"') == 3                             # plan 2, build 3, playtester 1
    assert 'marker-end="url(#wf-arrow)"' in html
    assert len(re.findall(r'<g class="wf-feed" data-feed="\d"', html)) == 5
    assert html.count('class="wf-link"') == 8 and "picked per item" in html
    assert "art &lt;skill&gt;" in html and "art <skill>" not in html       # escaped
    for s in data["stages"]:
        assert s["title"] in html


def test_eki_shows_its_locked_stages(tmp_path):
    html = run(tmp_path, eki())["html"]
    locked = re.findall(r'data-stage="(\w+)"[^>]*>\s*<title>[^<]*</title>(?:(?!</g>).)*class="wf-lock"', html, re.S)
    assert sorted(locked) == ["batch", "train"]
    assert html.count('class="wf-lock"') == 2 and "wf-off" not in html


def test_a_click_calls_the_handlers(tmp_path):
    got = run(tmp_path, holo(), [{"stage": "router"}, {"feed": 3}, {"stage": "merge"}])["got"]
    assert got == {"stages": ["router", "merge"], "feeds": ["site"]}


def test_it_fits_on_open_and_keeps_pan_and_zoom_across_draws(tmp_path):
    out = run(tmp_path, holo(), [{"drag": [40, -20]}, {"wheel": {"deltaX": 0, "deltaY": -100, "ctrlKey": True}},
                                 {"wheel": {"deltaX": 10, "deltaY": 5}}, {"redraw": "build"}])
    first, after = out["first"], out["after"]
    assert 0.25 <= first["k"] <= 1.2                                       # fitted to 1200×600
    assert after["k"] > first["k"]                                         # pinch zoomed in
    assert f'scale({after["k"]})' in out["html2"]                          # the redraw kept the view
    assert re.search(r'class="wf-node wf-sel[^"]*" data-stage="build"', out["html2"])
    assert out["got"] == {"stages": [], "feeds": []}                        # a drag is no click
    assert "wf-canvas" in out["cls"] and out["gone"]


def test_zoom_is_clamped(tmp_path):
    out = run(tmp_path, holo(), [{"wheel": {"deltaX": 0, "deltaY": -5000, "metaKey": True}}])
    assert out["after"]["k"] == 2.5
    out = run(tmp_path, holo(), [{"wheel": {"deltaX": 0, "deltaY": 5000, "ctrlKey": True}}])
    assert out["after"]["k"] == 0.25


def test_the_red_dot_says_how_many_need_a_look(tmp_path):
    data = holo()
    by = {s["id"]: s for s in data["stages"]}
    by["build"]["alert_count"] = 1
    html = run(tmp_path, data)["html"]
    assert '<circle class="wf-dot" cx="' in html and "<title>1 needs a look</title></circle>" in html
    by["build"]["alert_count"] = 3
    assert "<title>3 need a look</title></circle>" in run(tmp_path, data)["html"]
