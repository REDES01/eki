"""The workflow graph wraps into rows when one row would be too small to read: layout(data, {perRow})
puts the stages perRow to a row like text, an elbow arrow from a row's end to the next row's start; fit
picks the row length that shows the graph largest in the canvas, and fits again when the canvas changes
size (the side panel opening) unless the person has panned or zoomed since."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_web_workflowcanvas import eki, holo

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

STUB = r"""
const fs = require("fs");
const [, , file, args] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}), head: null };
let watched = null;
global.ResizeObserver = class { constructor(fn) { watched = fn; } observe() {} disconnect() { watched = null; } };
eval(fs.readFileSync(file, "utf8"));
const [data, perRow, size, steps] = JSON.parse(args);
const world = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
const el = { _h: "", listeners: {}, clientWidth: size[0], clientHeight: size[1],
  classList: { add() {}, remove() {} },
  get innerHTML() { return this._h; }, set innerHTML(v) { this._h = String(v); },
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
  removeEventListener() {}, getBoundingClientRect: () => ({ left: 0, top: 0 }),
  querySelector: (sel) => (sel === ".wf-world" ? world : null) };
const out = { layout: workflowCanvas.layout(data, { perRow }), views: [] };
const c = workflowCanvas.mount(el, {});
c.draw(data, null);
out.views.push(c.view()); out.html = el.innerHTML;
for (const s of steps) {
  if (s.resize) { el.clientWidth = s.resize[0]; el.clientHeight = s.resize[1]; watched && watched([]); }
  if (s.drag) {
    const fire = (ev, e) => el.listeners[ev].forEach((fn) => fn(e));
    fire("pointerdown", { button: 0, target: { closest: () => null }, clientX: 0, clientY: 0 });
    fire("pointermove", { clientX: s.drag[0], clientY: s.drag[1] }); fire("pointerup", {});
  }
  out.views.push(c.view());
}
out.after = el.innerHTML;
process.stdout.write(JSON.stringify(out));
"""


def run(tmp_path, data, per_row=0, size=(1200, 600), steps=()):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "rows.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "workflowcanvas.js"),
                          json.dumps([data, per_row, list(size), list(steps)])], capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def overlap(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


@pytest.mark.parametrize("fixture", [holo, eki])
def test_rows_like_text_with_no_box_on_another(tmp_path, fixture):
    data = fixture()
    L = run(tmp_path, data, per_row=4)["layout"]
    ids = [s["id"] for s in data["stages"]]
    assert [n["id"] for n in L["nodes"]] == ids
    rows = [L["nodes"][i:i + 4] for i in range(0, len(ids), 4)]
    assert all(len({n["y"] for n in r}) == 1 for r in rows)                # a row shares a y
    assert [r[0]["y"] for r in rows] == sorted({n["y"] for n in L["nodes"]})
    assert all(r[0]["x"] == rows[0][0]["x"] for r in rows)                 # each row starts at the left
    assert [(e["from"], e["to"]) for e in L["edges"]] == list(zip(ids, ids[1:]))
    boxes = L["nodes"] + L["feeds"]
    assert not [(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:] if overlap(a, b)]


def test_a_feed_into_a_later_row_sits_below_its_row(tmp_path):
    data = holo()                                     # site: build (row 0) and playtester (row 1) at 4 a row
    L = run(tmp_path, data, per_row=4)["layout"]
    site = L["feeds"][[f["card"] for f in data["feeds"]].index("site")]
    build = next(n for n in L["nodes"] if n["id"] == "build")
    assert site["y"] > build["y"] + build["h"]


def test_fit_wraps_a_narrow_canvas_and_keeps_one_row_on_a_wide_one(tmp_path):
    narrow = run(tmp_path, eki(), size=(500, 900))
    one = run(tmp_path, eki(), per_row=0, size=(3200, 400))
    assert one["views"][0]["k"] > 0.9 and 'L' in one["html"]
    assert narrow["views"][0]["k"] > 0.5                                   # readable, not 0.2
    assert narrow["html"].count(" L") > len(eki()["stages"]) - 1           # elbow arrows between rows


def test_a_resize_fits_again_until_the_person_moves_the_view(tmp_path):
    got = run(tmp_path, eki(), size=(1400, 700), steps=[{"resize": [600, 700]}, {"drag": [40, 10]},
                                                        {"resize": [1400, 700]}])
    first, refit, dragged, kept = got["views"]
    assert refit != first and refit["k"] <= first["k"]
    assert dragged["tx"] == refit["tx"] + 40 and kept == dragged
