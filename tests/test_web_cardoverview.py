"""An open card (projectcardpanel.js) starts with three plain sections drawn by cardoverview.js — What it
does (card.explain, then card.about), Used by (its line, the stages as chips, up to 5 recent runs with how
long ago) and Right now (card.overview.now) — above a folded Details holding the path, the kind and the
settings form, which stays open or closed when the panel is drawn again. The header shows the card's title;
a card from an older server without these keys still opens."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web_cardchoices import STUB as CHOICES

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# the choices harness, but its files are cardoverview.js and the panel, and it reads back the whole panel
STUB = CHOICES.split("const [card, ro, pick]")[0] + r"""
const [card, steps] = JSON.parse(process.argv[4]);
const out = { snaps: [] };
const ctx = { ago: (t) => `${t}s-ago`, reload() {}, api: async (url) => url.endsWith("/uses") ? { uses: [] } : {} };
const box = mk(null);
const html = (sel) => { const e = box.querySelector(sel); return e ? e.innerHTML : null; };
(async () => {
  projectCardPanel.open(box, "p1", card, "", ctx);
  for (const s of steps) {
    if (s === "unfold") box.querySelector(".pp-details").open = true;
    if (s === "redraw") projectCardPanel.redraw();
    if (s === "update") projectCardPanel.update(card, "");
    if (s === "snap") out.snaps.push({ panel: box.innerHTML, head: html(".pp-head"), overview: html(".pp-overview"),
      meta: html(".pp-meta"), settings: html(".pp-settings") });
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""

RUNS = [{"run": f"r{i}", "title": f"Convert part {i}", "when": 100 + i} for i in range(7)]
SERVE = {"name": "serve", "kind": "serve", "title": "Preview links", "state": "idle", "editable": True,
         "card": "/p/components/serve", "about": "one copy per task",
         "explain": "Every agent gets its own running copy of the game.",
         "spec": {"kind": "serve", "port": 4200},
         "overview": {"used_by": {"stages": ["build", "check"], "line": "used 6× today by build agents", "runs": RUNS},
                      "now": ["2 preview links running", "your copy: :4200"]},
         "live": {}}


def run(tmp_path, card, steps=("snap",)):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "cardoverview.js"), str(WEB / "projectcardpanel.js"),
                          json.dumps([card, list(steps)])], capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)["snaps"]


def test_the_three_sections_come_first_in_order_and_above_details(tmp_path):
    snap = run(tmp_path, SERVE)[0]
    ov = snap["overview"]
    assert ov.index("What it does") < ov.index("Used by") < ov.index("Right now")
    assert ov.index("Every agent gets its own running copy") < ov.index("one copy per task") < ov.index("Used by")
    assert "used 6× today by build agents" in ov
    assert '<span class="co-chip">build</span>' in ov and '<span class="co-chip">check</span>' in ov
    assert "Convert part 4" in ov and "104s-ago" in ov and "Convert part 5" not in ov     # 5 runs at most
    assert ov.index("2 preview links running") > ov.index("Right now") and "your copy: :4200" in ov
    panel = snap["panel"]
    assert panel.index("pp-head") < panel.index("pp-overview") < panel.index("pp-details")


def test_details_is_folded_and_holds_the_settings_path_and_kind(tmp_path):
    snap = run(tmp_path, SERVE)[0]
    panel = snap["panel"]
    assert '<details class="pp-details"><summary>Details</summary>' in panel
    assert panel.index("pp-details") < panel.index("pp-meta") < panel.index("pp-settings")
    assert 'data-key="port"' in snap["settings"]
    assert "/p/components/serve" in snap["meta"] and "<code>serve</code>" in snap["meta"]
    assert "/p/components/serve" not in snap["head"]


def test_details_stays_open_or_closed_across_redraws(tmp_path):
    shut, opened, kept = run(tmp_path, SERVE, ["redraw", "snap", "unfold", "redraw", "snap", "update", "snap"])
    assert '<details class="pp-details">' in shut["panel"]
    assert '<details class="pp-details" open>' in opened["panel"]
    assert '<details class="pp-details" open>' in kept["panel"]


def test_the_header_shows_the_title_not_the_kind(tmp_path):
    head = run(tmp_path, SERVE)[0]["head"]
    assert '<span class="pp-title">Preview links</span>' in head


def test_a_card_without_overview_still_opens(tmp_path):
    card = {k: v for k, v in SERVE.items() if k not in ("title", "explain", "overview", "live", "about")}
    snap = run(tmp_path, card)[0]
    ov = snap["overview"]
    assert "What it does" in ov and "Used by" in ov and "Right now" in ov and "Not used yet." in ov
    assert '<span class="pp-title">serve</span>' in snap["head"] and 'data-key="port"' in snap["settings"]


def test_small():
    for f in ("cardoverview.js", "cardoverview.css", "projectcardpanel.js"):
        assert len((WEB / f).read_text().splitlines()) < 400, f
