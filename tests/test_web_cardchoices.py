"""A card's settings with fixed choices (card.choices / card.choice_defaults, declared by its kind) are
dropdowns in the open card (projectcardpanel.js): the current value selected, a default shown when the spec
lacks it, true/false kept as the switch, a value outside the set kept as a marked first option, a change
saved through the card's settings route, read-only cards disabled, and no choices meaning the form of before."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough page to open one card in a box, change a setting and read the settings form back.
STUB = """
function mk(parent) {
  const e = { parent, dataset: {}, listeners: {}, kids: {}, _html: "",
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); this.kids = {}; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; },
    closest() { return null; }, matches() { return false; }, focus() {},
    querySelector(sel) {
      const m = /^\\.([\\w-]+)$/.exec(sel);
      if (!m || !new RegExp('class="[^"]*\\\\b' + m[1] + '\\\\b').test(this._html)) return null;
      return this.kids[sel] || (this.kids[sel] = mk(this));
    } };
  return e;
}
global.window = global;
global.setTimeout = setTimeout;
global.CSS = { escape: (s) => s };
global.document = { querySelector: () => ({}), activeElement: null };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
require(process.argv[2]);
const [card, ro, change] = JSON.parse(process.argv[3]);
const out = { posted: [] };
const ctx = { ago: () => "", reload() {},
  api: async (url, body) => { if (body) out.posted.push(body); return url.endsWith("/uses") ? { uses: [] } : {}; } };
const box = mk(null);
(async () => {
  projectCardPanel.open(box, "p1", card, ro, ctx);
  if (change) {
    const target = { dataset: { key: change[0], type: change[1] }, value: change[2], parent: box,
      matches: () => false, closest: () => null };
    for (const fn of box.listeners.change) fn({ type: "change", target });
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
  }
  out.settings = box.querySelector(".pp-settings").innerHTML;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""

KNOWLEDGE = {"name": "notes", "kind": "knowledge", "state": "idle", "editable": True, "card": "/p/components/notes",
             "spec": {"kind": "knowledge", "read": "always", "files": ["a.md"]},
             "choices": {"access": ["queue", "exclusive", "shared"], "read": ["always", "when-relevant"]},
             "choice_defaults": {"access": "shared"}}
AGENT = {"name": "alpha", "kind": "agent", "state": "idle", "editable": True, "card": "/p/components/alpha",
         "spec": {"kind": "agent", "target": "claude"}, "prompt": "hi",
         "choices": {"access": ["queue", "exclusive", "shared"], "bare": [False, True]},
         "choice_defaults": {"access": "shared", "bare": False}}


def run(tmp_path, card, ro="", change=None):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "projectcardpanel.js"), json.dumps([card, ro, change])],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def select(html, key):
    m = re.search(r'<select class="([^"]*)" data-key="' + key + r'"([^>]*)>(.*?)</select>', html, re.S)
    assert m, html
    opts = re.findall(r'<option value="([^"]*)"( selected)?>', m.group(3))
    return m.group(1).split(), m.group(2), [(v, bool(s)) for v, s in opts]


def test_read_is_a_dropdown_with_its_choices_and_the_value_selected(tmp_path):
    html = run(tmp_path, KNOWLEDGE)["settings"]
    cls, attrs, opts = select(html, "read")
    assert "set-in" in cls and "invalid" not in cls and 'data-type="choice"' in attrs and "disabled" not in attrs
    assert opts == [("always", True), ("when-relevant", False)]
    assert 'data-key="files"' in html and "<textarea" in html                # a key without choices as before


def test_access_shows_shared_when_the_spec_lacks_it(tmp_path):
    for card in (KNOWLEDGE, AGENT):
        _, _, opts = select(run(tmp_path, card)["settings"], "access")
        assert opts == [("queue", False), ("exclusive", False), ("shared", True)], card["kind"]


def test_agent_bare_stays_a_switch_and_target_a_text_box(tmp_path):
    html = run(tmp_path, AGENT)["settings"]
    assert re.search(r'<input type="checkbox" data-key="bare" data-type="bool">', html), html
    assert '<select class="set-in" data-key="bare"' not in html
    assert re.search(r'<input class="set-in" data-key="target" data-type="text" value="claude"', html)


def test_a_value_outside_the_choices_is_kept_first_and_marked(tmp_path):
    card = dict(KNOWLEDGE, spec={"kind": "knowledge", "read": "sometimes"})
    html = run(tmp_path, card)["settings"]
    cls, _, opts = select(html, "read")
    assert "invalid" in cls
    assert opts == [("sometimes", True), ("always", False), ("when-relevant", False)]
    assert "not one of: always, when-relevant" in html


def test_a_non_boolean_bare_is_a_marked_dropdown_saving_booleans(tmp_path):
    card = dict(AGENT, spec={"kind": "agent", "bare": "yes"})
    got = run(tmp_path, card, change=["bare", "boolchoice", "true"])
    cls, attrs, opts = select(got["settings"], "bare")
    assert "invalid" in cls and 'data-type="boolchoice"' in attrs
    assert opts == [("yes", True), ("false", False), ("true", False)]
    assert got["posted"] == [{"settings": {"bare": True}}]


def test_changing_the_dropdown_saves_the_chosen_value(tmp_path):
    got = run(tmp_path, KNOWLEDGE, change=["read", "choice", "when-relevant"])
    assert got["posted"] == [{"settings": {"read": "when-relevant"}}]


def test_no_choices_means_text_boxes_as_before(tmp_path):
    card = {k: v for k, v in KNOWLEDGE.items() if k not in ("choices", "choice_defaults")}
    html = run(tmp_path, card)["settings"]
    assert "<select" not in html and 'data-key="access"' not in html
    assert re.search(r'<input class="set-in" data-key="read" data-type="text" value="always"', html)


def test_a_read_only_card_has_the_dropdown_disabled(tmp_path):
    for card, ro in ((dict(KNOWLEDGE, ref="pool-notes"), ""), (KNOWLEDGE, "the project is archived")):
        _, attrs, _ = select(run(tmp_path, card, ro)["settings"], "read")
        assert "disabled" in attrs


def test_the_panel_stays_under_400_lines():
    assert len((WEB / "projectcardpanel.js").read_text().splitlines()) < 400
