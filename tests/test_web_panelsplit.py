"""The right side panel's left edge is dragged to resize it; the width is kept in the page."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# panelsplit.js needs a page; this stub is just enough to load it and call window.panelSplit.
# argv[3] = "broken" makes every storage call throw; argv[4] is a width saved before loading.
STUB = """
const props = {};
const app = { style: { setProperty(k, v) { props[k] = v; }, getPropertyValue(k) { return props[k] || ""; },
                       removeProperty(k) { delete props[k]; } } };
const store = {}, broken = process.argv[3] === "broken";
if (process.argv[4]) store["eki.panel.w"] = process.argv[4];
const no = () => { throw new Error("storage is off"); };
global.localStorage = broken ? { getItem: no, setItem: no, removeItem: no } :
  { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; } };
global.Event = function (type) { this.type = type; };
let sent = 0;
global.window = { innerWidth: 1600, addEventListener() {}, dispatchEvent() { sent++; } };
global.document = { getElementById: (id) => (id === "app" ? app : null), body: { classList: { add() {}, remove() {} } } };
require(process.argv[2]);
const s = window.panelSplit, out = {};
out.restored = props["--panel-w"] || null; out.sentOnLoad = sent;
out.clamp = [[100, 1600, 272], [3000, 3000, 272], [900, 1600, 480], [600, 800, 272], [400.4, 1600, 272]]
  .map(([w, t, side]) => s.clamp(w, t, side));
out.set = s.set(500);
out.saved = store[s.KEY] || null; out.var = props["--panel-w"]; out.sent = sent;
props["--side-w"] = "480px";
out.wideSide = s.set(1200);
s.reset();
out.after = [s.KEY in store, "--panel-w" in props, sent];
out.consts = [s.MIN, s.KEY, s.DEFAULT];
process.stdout.write(JSON.stringify(out));
"""


def run(tmp_path, *args):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "panelsplit.js"), *args],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_grip_is_in_the_panel_outside_the_drag_header():
    page = (WEB / "index.html").read_text()
    panel = page[page.index('<aside id="panel"'):]
    panel = panel[:panel.index("</aside>")]
    grip = re.search(r'<div id="panel-grip"[^>]*>', panel).group(0)
    assert 'role="separator"' in grip and 'aria-orientation="vertical"' in grip and 'tabindex="0"' in grip
    before = panel[:panel.index('id="panel-grip"')]
    for m in re.finditer(r"<(\w+)[^>]*\bdata-drag\b", before):
        assert f"</{m.group(1)}>" in before[m.end():]
    assert "data-drag" not in grip


def test_panelsplit_js_loads_after_panel_js():
    page = (WEB / "index.html").read_text()
    at = [page.index(f'<script src="/ui/{s}.js"></script>') for s in ("panel", "panelsplit")]
    assert at == sorted(at)


def test_the_grid_uses_the_panel_width_and_the_grip_has_its_rules():
    css = (WEB / "style.css").read_text()
    rule = lambda sel: next(l for l in css.splitlines() if l.startswith(sel + " {"))
    assert "minmax(380px, 1fr) var(--panel-w, minmax(360px, 44%))" in rule("#app.with-panel")
    assert "position: relative" in rule("#panel")
    assert "col-resize" in rule("#panel-grip") and "left: -3px" in rule("#panel-grip")
    assert "body.resizing #panel-grip" in css


def test_clamp_keeps_the_min_the_seventy_percent_and_the_chat_column(tmp_path):
    out = run(tmp_path)
    # min; 70% of 3000; 1600-480-380 with a wide sidebar; MIN wins when narrow; rounded
    assert out["clamp"] == [320, 2100, 740, 320, 400]
    assert out["consts"] == [320, "eki.panel.w", "minmax(360px, 44%)"]


def test_set_saves_the_width_and_reset_forgets_it(tmp_path):
    out = run(tmp_path)
    assert out["restored"] is None and out["sentOnLoad"] == 0
    assert out["set"] == 500 and out["saved"] == "500" and out["var"] == "500px" and out["sent"] == 1
    assert out["wideSide"] == 740  # the sidebar's own width is read at drag time
    assert out["after"] == [False, False, 3]


def test_the_saved_width_is_restored_clamped_without_a_resize(tmp_path):
    assert run(tmp_path, "ok", "640")["restored"] == "640px"
    out = run(tmp_path, "ok", "5000")
    assert out["restored"] == "948px" and out["sentOnLoad"] == 0


def test_storage_that_throws_does_not_break_anything(tmp_path):
    out = run(tmp_path, "broken")
    assert out["restored"] is None
    assert out["set"] == 500 and out["var"] == "500px" and out["saved"] is None
    assert out["after"][1] is False
