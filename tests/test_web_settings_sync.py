"""Settings → Claude sync: registered, in SECTIONS, loaded by the page, and drawn read-only."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web import get, web  # noqa: F401  (the fixture)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def test_registered_in_sections_and_loaded_before_the_frame(web):
    code, js = get(f"{web}/ui/settingssync.js")
    assert code == 200 and len(js.splitlines()) < 400
    assert 'settings.section("sync"' in js and 'push(["sync"' in js and 'title: "Claude sync"' in js
    assert '"/api/sync"' in js and "method:" not in js            # reads only
    assert '"sync"]' in (WEB / "settings.js").read_text().split("const SECTIONS", 1)[1].split("\n", 1)[0]
    _, page = get(web + "/")
    assert page.index("/ui/settingscomponents.js") < page.index("/ui/settingssync.js") < page.index("/ui/settings.js")


SCRIPT = r"""
global.window = global; global.md = { esc: (s) => String(s) };
global.location = { hash: "#settings/sync" };
const now = Date.now() / 1000;
global.fetch = async () => ({ ok: true, json: async () => ({ directions: [
  { name: "skills-in", at: now - 120, count: 7, clashes: ["pdf: edited in eki", "docx: learned"], note: "" },
  { name: "skills-out", at: null, count: 0, clashes: [], note: "" },
  { name: "memory-in", at: now - 5, count: 3, clashes: ["one"], note: "3 notes" },
  { name: "memory-out", at: null, count: 0, clashes: [], note: "" },
  { name: "instructions", at: null, count: 0, clashes: [], note: "" }], diff: "-old\n+new\n" }) });
const secs = {};
global.settings = { section: (n, s) => (secs[n] = s), on: () => true, render: () => {} };
require(process.argv[2]); require(process.argv[3]);
const S = secs.sync, out = {};
const el = (fold) => ({ dataset: { fold } });
(async () => {
  out.loading = S.html({});
  await new Promise((r) => setTimeout(r, 0));
  out.closed = S.html({});
  out.redraw = S.act("fold", null, el("skills-in"));
  out.open = S.html({});
  console.log(JSON.stringify(out));
})();
"""


def test_lines_fold_open_to_clashes_and_the_diff_shows(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "sync.js"
    stub.write_text(SCRIPT)
    out = json.loads(subprocess.run(["node", str(stub), str(WEB / "settingsform.js"), str(WEB / "settingssync.js")],
                                    capture_output=True, text=True, timeout=30, check=True).stdout)
    assert "Loading" in out["loading"]
    closed = out["closed"]
    assert "Claude sync" in closed and closed.count('class="set-fold"') == 5
    for want in ("2m ago", "2 clashes", "1 clash<", "never", "no clashes", "<pre", "-old\n+new"):
        assert want in closed, want
    assert closed.index("skills-in") < closed.index("skills-out") < closed.index("instructions")
    assert "pdf: edited in eki" not in closed
    assert out["redraw"] is True
    assert "set-fold open" in out["open"] and "pdf: edited in eki" in out["open"] and "docx: learned" in out["open"]
