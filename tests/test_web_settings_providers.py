"""Settings → Providers: each entry folded to one line, one open at a time, built-ins read-only."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web import get, web  # noqa: F401  (the fixture)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def test_the_providers_section_folds_its_entries(web):
    code, js = get(f"{web}/ui/settingsproviders.js")
    assert code == 200
    for want in ('settings.section("providers"', "settingsForm.fold(", "set-pill", "settingsModels.line(name)",
                 "settingsForm.memo", "window.settingsProviders = { names }", "Also: ", "kind default", "keep up"):
        assert want in js, want
    assert len(re.findall(r"\blet open\b", js)) == 1          # a single open id: one editor at a time
    assert "set-card" not in js and "data-unw" not in js
    assert "until you save" not in js
    assert len(js.splitlines()) < 400


# the page's pieces with a stub frame: draw, open one, open another, add, edit an in-memory entry
SCRIPT = r"""
global.window = global; global.md = { esc: (s) => String(s) };
global.confirm = () => true;
const got = { effective: { entries: {
  claude: { kind: "claude_code", label: "Claude", can_effective: ["text", "tools"] },
  pics: { kind: "comfyui", label: "ComfyUI", can_effective: ["image"], unwritten: true },
  fake: { kind: "fake", label: "Fake", builtin: true } } } };
const draft = { claude: { kind: "claude_code", label: "Claude", off: true, extra: 1 } };
const secs = {};
global.settings = { section: (n, s) => (secs[n] = s), draft: () => draft, got: () => got,
  note: () => {}, clone: (x) => JSON.parse(JSON.stringify(x)) };
global.settingsModels = { line: (n) => `<div class="dim">3 models · ${n}</div>` };
require(process.argv[2]); require(process.argv[3]);
const P = secs.providers, out = {};
const fold = (n) => ({ dataset: { fold: n }, classList: { remove() {} }, querySelector: () => null });
out.closed = P.html(draft);
P.act("fold", null, fold("claude")); out.one = P.html(draft);
P.act("fold", null, fold("pics")); out.other = P.html(draft);
P.edit({ dataset: { f: "label" }, value: "Pictures" }, draft, fold("pics"), { type: "input" });
out.draft = JSON.parse(JSON.stringify(draft));
P.act("addprov", null, null); out.added = P.html(draft);
out.file = P.file; out.names = settingsProviders.names();
console.log(JSON.stringify(out));
"""


def test_one_entry_opens_at_a_time_and_edits_write_the_draft(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "prov.js"
    stub.write_text(SCRIPT)
    out = json.loads(subprocess.run(["node", str(stub), str(WEB / "settingsform.js"), str(WEB / "settingsproviders.js")],
                                    capture_output=True, text=True, timeout=30, check=True).stdout)
    assert out["file"] == "providers"
    closed = out["closed"]
    assert "set-fold open" not in closed and "data-f=" not in closed          # all folded
    assert '<span class="set-pill">tools</span>' in closed and '<span class="set-pill">off</span>' in closed
    assert "3 models · claude" in closed and "3 models · pics" not in closed  # no list for an unwritten one
    assert 'class="set-fold added"' in closed and "not saved — in memory" in closed
    assert "always there, not written to providers.json" in closed and 'class="set-line"' in closed
    one = out["one"]
    assert one.count("set-fold open") == 1 and 'data-f="base_url"' in one and "kind default" in one
    assert "<code>extra</code>" in one
    other = out["other"]
    assert other.count(" open\" data-fold") == 1 and 'set-fold added open" data-fold="pics"' in other
    assert out["draft"]["pics"] == {"kind": "comfyui", "label": "Pictures"}   # joins the draft, marks dropped
    assert out["added"].count("set-fold open") == 1 and 'data-fold="provider-1"' in out["added"]
    assert out["names"] == ["claude", "pics", "provider-1"]
