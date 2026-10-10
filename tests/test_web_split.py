"""The sidebar's edge is dragged to resize it; the width is a view preference kept in the page."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# split.js needs a page; this stub is just enough to load it and call window.split.
STUB = """
const props = {};
const app = { style: { setProperty(k, v) { props[k] = v; }, getPropertyValue(k) { return props[k] || ""; },
                       removeProperty(k) { delete props[k]; } } };
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
                        removeItem: (k) => { delete store[k]; } };
global.Event = function (type) { this.type = type; };
let sent = 0;
global.window = { innerWidth: 1400, addEventListener() {}, dispatchEvent() { sent++; } };
global.document = { getElementById: (id) => (id === "app" ? app : null), body: { classList: { add() {}, remove() {} } } };
require(process.argv[2]);
const s = window.split, out = {};
out.clamp = [[100, 1400], [300, 1400], [900, 1400], [450, 800], [272.4, 1400]].map(([w, t]) => s.clamp(w, t));
s.set(320);
out.saved = store[s.KEY]; out.var = props["--side-w"]; out.sent = sent;
s.reset();
out.after = s.KEY in store;
out.consts = [s.MIN, s.MAX, s.DEFAULT, s.KEY];
process.stdout.write(JSON.stringify(out));
"""


def run(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "split.js")], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_grip_is_the_sidebars_last_child_outside_the_drag_regions():
    page = (WEB / "index.html").read_text()
    side = page[page.index('<aside id="side">'):]
    side = side[:side.index("</aside>")]
    assert 'id="side-grip"' in side and 'role="separator"' in side
    assert side.rstrip().endswith('</div>') and side.rstrip().rsplit("<div", 1)[1].startswith(' id="side-grip"')
    # every data-drag element is closed before the grip opens
    before = side[:side.index('id="side-grip"')]
    for m in re.finditer(r"<(\w+)[^>]*\bdata-drag\b", before):
        assert f"</{m.group(1)}>" in before[m.end():]


def test_split_js_loads_after_nav_and_before_side():
    page = (WEB / "index.html").read_text()
    at = [page.index(f'<script src="/ui/{s}.js"></script>') for s in ("nav", "split", "side")]
    assert at == sorted(at)


def test_the_grid_uses_the_side_width_and_the_grip_has_its_rules():
    css = (WEB / "style.css").read_text()
    rule = lambda sel: next(l for l in css.splitlines() if l.startswith(sel + " {"))
    assert "var(--side-w, 272px) 1fr" in rule("#app")
    assert "var(--side-w, 272px) minmax(380px, 1fr) minmax(360px, 44%)" in rule("#app.with-panel")
    assert "position: relative" in rule("#side")
    assert "col-resize" in rule("#side-grip") and "right: -3px" in rule("#side-grip")
    assert "user-select: none" in rule("body.resizing")
    assert "pointer-events: none" in rule("body.resizing iframe")


def test_clamp_keeps_the_bounds_and_room_for_the_main_area(tmp_path):
    out = run(tmp_path)
    assert out["clamp"] == [200, 300, 480, 380, 272]
    assert out["consts"] == [200, 480, 272, "eki.side.w"]


def test_set_saves_the_width_and_reset_forgets_it(tmp_path):
    out = run(tmp_path)
    assert out["saved"] == "320" and out["var"] == "320px" and out["sent"] == 1
    assert out["after"] is False
