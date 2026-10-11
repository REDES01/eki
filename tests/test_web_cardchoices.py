"""A card's settings with fixed choices (card.choices / card.choice_defaults, declared by its kind) are
choicemenu.js dropdowns in the open card (projectcardpanel.js): the current value on the button, a default
shown when the spec lacks it, true/false kept as the switch, a value outside the set kept and marked, a pick
saved through the card's settings route (booleans as booleans), read-only cards disabled, and no choices
meaning the form of before."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web_choicemenu import DOM

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough page to open one card in a box, change a setting and read the settings form back.
STUB = DOM + r"""
function mk(parent) {
  const e = { parent, dataset: {}, listeners: {}, kids: {}, _html: "",
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); this.kids = {}; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; },
    closest() { return null; }, matches() { return false; }, focus() {}, querySelectorAll() { return []; },
    querySelector(sel) {
      const m = /^\.([\w-]+)$/.exec(sel);
      if (!m || !new RegExp('class="[^"]*\\b' + m[1] + '\\b').test(this._html)) return null;
      return this.kids[sel] || (this.kids[sel] = mk(this));
    } };
  return e;
}
global.setTimeout = setTimeout;
global.CSS = { escape: (s) => s };
document.querySelector = () => ({});
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
require(process.argv[2]);
require(process.argv[3]);
const [card, ro, pick] = JSON.parse(process.argv[4]);
const out = { posted: [] };
const ctx = { ago: () => "", reload() {},
  api: async (url, body) => { if (body) out.posted.push(body); return url.endsWith("/uses") ? { uses: [] } : {}; } };
const box = mk(null);
(async () => {
  projectCardPanel.open(box, "p1", card, ro, ctx);
  out.settings = box.querySelector(".pp-settings").innerHTML;
  if (pick) {                                   // open the key's dropdown and click the option
    const tree = parse(out.settings);
    const btn = tree.querySelectorAll(".cm-btn").find((b) => b.dataset.key === pick[0]);
    const wrap = btn.closest(".cm");
    fire(box, "click", btn);
    out.opened = wrap.classList.contains("open");
    const opt = wrap.querySelectorAll(".cm-opt").find((o) => o.dataset.value === pick[1]);
    if (opt) fire(box, "click", opt);
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
  }
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


def run(tmp_path, card, ro="", pick=None):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "projectcardpanel.js"),
                          json.dumps([card, ro, pick])],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def menu(html, key):
    """The dropdown for `key`: the button's classes and attributes, its shown value, the options and which is on."""
    m = re.search(r'<button type="button" class="([^"]*)" data-key="' + key + r'"([^>]*)><span class="cm-val">([^<]*)</span>'
                  r'.*?<ul class="cm-list"[^>]*>(.*?)</ul>', html, re.S)
    assert m, html
    opts = re.findall(r'<li class="cm-opt( on)?" role="option" id="[^"]*" data-value="([^"]*)"', m.group(4))
    return m.group(1).split(), m.group(2), m.group(3), [(v, bool(on)) for on, v in opts]


def test_read_is_a_dropdown_with_its_choices_and_the_value_shown(tmp_path):
    html = run(tmp_path, KNOWLEDGE)["settings"]
    cls, attrs, shown, opts = menu(html, "read")
    assert "set-in" in cls and "invalid" not in cls and 'data-type="choice"' in attrs and "disabled" not in attrs
    assert shown == "always" and opts == [("always", True), ("when-relevant", False)]
    assert "<select" not in html
    assert 'data-key="files"' in html and "<textarea" in html                # a key without choices as before


def test_access_shows_shared_when_the_spec_lacks_it(tmp_path):
    for card in (KNOWLEDGE, AGENT):
        _, _, shown, opts = menu(run(tmp_path, card)["settings"], "access")
        assert shown == "shared" and opts == [("queue", False), ("exclusive", False), ("shared", True)], card["kind"]


def test_agent_bare_stays_a_switch_and_target_a_text_box(tmp_path):
    html = run(tmp_path, AGENT)["settings"]
    assert re.search(r'<input type="checkbox" data-key="bare" data-type="bool">', html), html
    assert 'class="set-in cm-btn" data-key="bare"' not in html
    assert re.search(r'<input class="set-in" data-key="target" data-type="text" value="claude"', html)


def test_a_value_outside_the_choices_is_kept_and_marked(tmp_path):
    card = dict(KNOWLEDGE, spec={"kind": "knowledge", "read": "sometimes"})
    html = run(tmp_path, card)["settings"]
    cls, _, shown, opts = menu(html, "read")
    assert "invalid" in cls and shown == "sometimes"
    assert opts == [("always", False), ("when-relevant", False)]
    assert "not one of: always, when-relevant" in html


def test_a_non_boolean_bare_is_a_marked_dropdown_saving_booleans(tmp_path):
    card = dict(AGENT, spec={"kind": "agent", "bare": "yes"})
    got = run(tmp_path, card, pick=["bare", "true"])
    cls, attrs, shown, opts = menu(got["settings"], "bare")
    assert "invalid" in cls and 'data-type="boolchoice"' in attrs and shown == "yes"
    assert opts == [("false", False), ("true", False)]
    assert got["opened"] and got["posted"] == [{"settings": {"bare": True}}]


def test_picking_a_choice_saves_the_chosen_value(tmp_path):
    got = run(tmp_path, KNOWLEDGE, pick=["read", "when-relevant"])
    assert got["opened"] and got["posted"] == [{"settings": {"read": "when-relevant"}}]
    got = run(tmp_path, KNOWLEDGE, pick=["access", "exclusive"])
    assert got["posted"] == [{"settings": {"access": "exclusive"}}]


def test_no_choices_means_text_boxes_as_before(tmp_path):
    card = {k: v for k, v in KNOWLEDGE.items() if k not in ("choices", "choice_defaults")}
    html = run(tmp_path, card)["settings"]
    assert "cm-btn" not in html and 'data-key="access"' not in html
    assert re.search(r'<input class="set-in" data-key="read" data-type="text" value="always"', html)


def test_a_read_only_card_has_the_dropdown_disabled(tmp_path):
    for card, ro in ((dict(KNOWLEDGE, ref="pool-notes"), ""), (KNOWLEDGE, "the project is archived")):
        got = run(tmp_path, card, ro, pick=["read", "when-relevant"])
        _, attrs, _, _ = menu(got["settings"], "read")
        assert "disabled" in attrs and not got["opened"] and got["posted"] == []


def test_the_panel_stays_under_400_lines():
    assert len((WEB / "projectcardpanel.js").read_text().splitlines()) < 400
