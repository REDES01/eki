"""sidestatus.js: one line per project under the machine line with the build cap in force; hidden when none."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

STUB = """
const els = {};
const el = (id) => els[id] || (els[id] = { id, innerHTML: "", textContent: "", className: "", value: "", hidden: false,
  dataset: {}, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { els[c.id] = c; }, remove() {} });
global.window = { addEventListener() {}, dispatchEvent() {} };
global.document = { getElementById: (id) => (id === "caps" ? els.caps : el(id)),
  createElement: () => ({ hidden: false, innerHTML: "", dataset: {} }), addEventListener() {} };
global.fetch = () => new Promise(() => {});
global.setInterval = () => 0;
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;") };
global.prs = { lines: () => "", total: () => 0 };
require(process.argv[2]);
const out = [];
for (const st of JSON.parse(process.argv[3])) {
  window.sidestatus.draw([], st);
  out.push(els.caps ? { html: els.caps.innerHTML, hidden: els.caps.hidden } : null);
}
process.stdout.write(JSON.stringify(out));
"""


def draws(tmp_path, *sts):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "caps.js"
    stub.write_text(STUB)
    base = {"running": 1, "queued": 0, "room": True, "prs": {}, "digest": None}
    out = subprocess.run(["node", str(stub), str(WEB / "sidestatus.js"), json.dumps([{**base, **s} for s in sts])],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_one_line_per_project_then_hidden_when_none(tmp_path):
    caps = [{"name": "alpha", "line": "3 building · up to 4 now (machine: 6, 2 in other projects) · this project allows 24"},
            {"name": "<b>", "line": "1 building · up to 2 now (this project) · the machine allows 6"}]
    first, second, third = draws(tmp_path, {"caps": caps}, {"caps": []}, {})
    assert not first["hidden"]
    assert "alpha: 3 building · up to 4 now (machine: 6, 2 in other projects) · this project allows 24" in first["html"]
    assert "&lt;b>: 1 building · up to 2 now (this project)" in first["html"] and "<b>" not in first["html"]
    assert second["hidden"] and third["hidden"]


def test_no_element_before_any_caps(tmp_path):
    assert draws(tmp_path, {"caps": []}) == [None]
