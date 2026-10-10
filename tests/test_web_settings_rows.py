"""Settings → Rows: each routing row folded to one line, one open at a time."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web import get, web  # noqa: F401  (the fixture)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def test_rows_fold_to_one_line(web):
    code, js = get(f"{web}/ui/settingsrows.js")
    assert code == 200
    for want in ('settings.section("rows"', "settingsForm.fold(", "settingsTarget.label(", '" → "',
                 "settingsForm.memo", "confirm(", 'file: "routing"', 'data-act="addrow"'):
        assert want in js, want
    assert len(re.findall(r"\blet open\b", js)) == 1   # one open id for the whole section
    assert "location.hash" not in js                   # page state only, not in the address
    assert 'style="color: var(--amber)"' not in js
    assert len(js.splitlines()) < 400
    assert "until you save" not in js


# the section run in node over stubs: the summary line, one open at a time, add opens the new row
DRIVER = r"""
global.window = global;
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
let REG = {};
const draft = { rows: [{ key: "code", title: "Write code", targets: [{ provider: "claude", model: "opus", effort: "high" }, "codex"] },
                       { key: "chat", title: "Talk", targets: ["local"] }] };
const got = { effective: { rows: [{ key: "pics", title: "Pictures", targets: ["comfyui"], added: true }] } };
global.settings = { section: (n, s) => (REG[n] = s), draft: () => draft, got: () => got, clone: (x) => JSON.parse(JSON.stringify(x)) };
global.settingsProviders = { names: () => ["claude", "codex", "local"] };
global.settingsModels = { models: () => ({ claude: { listed: [{ model: "opus", label: "Opus 5.5" }] } }) };
global.confirm = () => true;
for (const f of process.argv.slice(2)) require(f);
const s = REG.rows, card = (id) => ({ dataset: { fold: id } });
const opens = (h) => (h.match(/set-fold[^"]*open/g) || []).length;
const out = {};
let h = s.html(draft);
out.summary = h.includes("claude · Opus 5.5 · high → codex") && h.includes("<b data-sum=\"key\">code</b>");
out.closed = opens(h);
out.memo = h.includes("not saved — in memory") && h.includes('class="set-fold added');
s.act("fold", null, card("d0")); h = s.html(draft);
out.one = opens(h) === 1 && h.includes('data-fold="d0"') && h.includes('data-f="examples"');
s.act("fold", null, card("d1")); h = s.html(draft);
out.other = opens(h) === 1 && /data-fold="d1"[^>]*>/.test(h) && h.indexOf('class="set-fold open') > h.indexOf('data-fold="d0"');
s.act("fold", null, card("d1"));
out.shut = opens(s.html(draft));
s.act("addrow", null, null); h = s.html(draft);
out.added = draft.rows.length === 3 && opens(h) === 1 && h.includes('class="set-fold open" data-fold="d2"');
s.act("delrow", null, card("d2"));
out.deleted = draft.rows.length === 2 && opens(s.html(draft)) === 0;
console.log(JSON.stringify(out));
"""


def test_rows_fold_and_open_one_at_a_time(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    drv = tmp_path / "drive.js"
    drv.write_text(DRIVER)
    files = [str(WEB / f"{n}.js") for n in ("settingsform", "settingstarget", "settingsrows")]
    run = subprocess.run(["node", str(drv), *files], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"summary": True, "closed": 0, "memo": True, "one": True, "other": True,
                                      "shut": 0, "added": True, "deleted": True}
