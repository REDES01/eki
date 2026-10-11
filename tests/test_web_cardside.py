"""A card opens in the side panel (projectcards.js hosting projectcardpanel.js through panel.js): open, swap,
close by Esc and ✕, the open tile marked in the grid, and an agent's session shown in the card's place
with a way back (cardsessions.js). Driven through the page's own click and key handlers."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough page to load panel.js and the cards' files, click tiles and sessions, press Esc and see the result.
STUB = """
const els = {};
function mk(id, parent) {
  const e = { id, tagName: "DIV", textContent: "", href: "", hidden: id === "panel", dataset: {}, listeners: {},
    parent: parent || null, parentNode: parent || null, kids: {}, scrollTop: 0, _html: "",
    classList: { add() {}, remove() {}, toggle() {} },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); this.kids = {}; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; },
    closest() { return null; }, matches() { return false; }, focus() {}, blur() {},
    querySelectorAll() { return []; },
    querySelector(sel) {
      const m = /^\\.([\\w-]+)$/.exec(sel);
      if (!m || !new RegExp('class="[^"]*\\\\b' + m[1] + '\\\\b').test(this._html)) return null;
      return this.kids[sel] || (this.kids[sel] = mk(sel, this));
    } };
  return e;
}
const el = (id) => els[id] || (els[id] = mk(id));
const out = { log: [], closes: [], fetched: [] };
const doc = {};
global.Event = class { constructor(type) { this.type = type; } };
global.window = global;
global.addEventListener = () => {};
global.dispatchEvent = () => {};
global.setInterval = () => 1; global.clearInterval = () => {};
global.confirm = () => true;
global.CSS = { escape: (s) => s };
global.document = { getElementById: el, addEventListener(ev, fn) { doc[ev] = fn; }, hidden: false, activeElement: null };
global.md = { esc: (s) => String(s), render: (t) => t, outline: () => [] };
const CARDS = { cards: [
  { name: "alpha", kind: "agent", about: "plays the game", state: "idle", spec: { kind: "agent", about: "plays the game" },
    prompt: "hi", editable: true, card: "/p/components/alpha" },
  { name: "beta", kind: "service", about: "", state: "idle", spec: { kind: "service" }, editable: true, card: "/p/components/beta" },
], readonly: "" };
global.fetch = async (url) => {
  out.fetched.push(url);
  let d = {};
  if (/\\/cards$/.test(url)) d = CARDS;
  else if (/\\/uses$/.test(url)) d = { uses: [] };
  else if (/\\/sessions$/.test(url)) d = { sessions: [{ id: "s1", when: 0, review: "Great fun\\nmore", tainted: false }] };
  else if (/\\/sessions\\/s1$/.test(url)) d = { id: "s1", when: 0, review: "Great fun, floaty jump", notes: ["menu hidden"],
    emotions: [{ t: "00:10", emotion: "curious", why: "title" }], screenshots: [], tainted: false };
  return { ok: true, statusText: "OK", json: async () => d, text: async () => "" };
};
for (const f of process.argv.slice(2, 6)) require(f);
const host = panel.host;
panel.host = (o) => {
  if (!o.__logged) { const c = o.close; o.close = (why) => { out.closes.push([o.name, why]); c(why); }; o.__logged = true; }
  return host(o);
};
const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0)); };
const target = (attr, value, key) => ({ dataset: { [key]: value }, matches: (s) => s === ".pc-tile" && attr === "data-card",
  closest(sel) { return sel === "[" + attr + "]" ? this : null; } });
const fire = (box, ev, e) => { for (const fn of (box && box.listeners[ev]) || []) fn(Object.assign({ preventDefault() {}, stopPropagation() {} }, e)); };
const area = el("pj-cards");
const body = el("panel-body");
const pp = (sel) => { for (const k of Object.values(body.kids)) if (k.id === sel) return k; return body.querySelector(sel); };
(async () => {
  projectCards.mount(area, "p1");
  await tick();
  for (const s of JSON.parse(process.argv[6])) {
    if (s[0] === "click") { fire(area, "click", { target: target("data-card", s[1], "card") }); await tick(); }
    if (s[0] === "enter") { fire(area, "keydown", { key: "Enter", target: target("data-card", s[1], "card") }); await tick(); }
    if (s[0] === "esc") { doc.keydown({ key: "Escape" }); await tick(); }
    if (s[0] === "closex") { for (const fn of el("panel-close").listeners.click) fn({}); await tick(); }
    if (s[0] === "session") { fire(pp(".pp-sessions"), "click", { target: target("data-sess", s[1], "sess") }); await tick(); }
    if (s[0] === "back") { fire(pp(".pp-sessview"), "click", { target: target("data-cs-back", "1", "csBack") }); await tick(); }
    if (s[0] === "snap") {
      const grid = area.querySelector(".pc-grid").innerHTML;
      const on = [...grid.matchAll(/class="pc-tile[^"]*\\bon" data-card="(\\w+)"/g)].map((m) => m[1]);
      const view = pp(".pp-cardview"), sess = pp(".pp-sessview");
      out.log.push({ hidden: el("panel").hidden, name: el("panel-name").textContent, sub: el("panel-path").textContent,
        owner: panel.owner() && panel.owner().name, on, body: body.innerHTML,
        cardHidden: view ? !!view.hidden : null, sess: sess && !sess.hidden ? sess.innerHTML : "",
        sessions: pp(".pp-sessions") ? pp(".pp-sessions").innerHTML : "" });
    }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


def run(tmp_path, steps):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    files = [str(WEB / f) for f in ("panel.js", "projectcards.js", "projectcardpanel.js", "cardsessions.js")]
    out = subprocess.run(["node", str(stub), *files, json.dumps(steps)], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_click_opens_the_card_in_the_side_panel(tmp_path):
    got = run(tmp_path, [["snap"], ["click", "alpha"], ["snap"]])
    before, after = got["log"]
    assert before["hidden"] is True and before["on"] == []
    assert after["hidden"] is False and after["owner"] == "alpha"
    assert after["name"] == "alpha" and after["sub"] == "agent · plays the game"
    assert "pp-cardview" in after["body"] and "pp-settings" in after["body"] and "pp-sessions" in after["body"]
    assert after["on"] == ["alpha"]
    assert any(u.endswith("/cards/alpha/uses") for u in got["fetched"])


def test_enter_on_a_tile_opens_it_too(tmp_path):
    log = run(tmp_path, [["enter", "beta"], ["snap"]])["log"]
    assert log[0]["owner"] == "beta" and log[0]["sub"] == "service" and log[0]["on"] == ["beta"]


def test_another_card_swaps_the_content(tmp_path):
    got = run(tmp_path, [["click", "alpha"], ["click", "beta"], ["snap"]])
    snap = got["log"][0]
    assert got["closes"] == [["alpha", "swap"]]
    assert snap["owner"] == "beta" and snap["name"] == "beta" and snap["on"] == ["beta"]
    assert "pp-sessions" not in snap["body"]                 # a service has no sessions: beta's view, not alpha's


def test_esc_and_the_x_close_it_and_the_mark_goes(tmp_path):
    for how in ("esc", "closex"):
        got = run(tmp_path, [["click", "alpha"], [how], ["snap"]])
        snap = got["log"][0]
        assert got["closes"] == [["alpha", "close"]], how
        assert snap["hidden"] is True and snap["owner"] is None and snap["on"] == [], how


def test_clicking_the_open_card_again_closes_it(tmp_path):
    got = run(tmp_path, [["click", "beta"], ["click", "beta"], ["snap"]])
    assert got["closes"] == [["beta", "close"]]
    assert got["log"][0]["hidden"] is True and got["log"][0]["on"] == []


def test_a_session_takes_the_cards_place_with_a_way_back(tmp_path):
    got = run(tmp_path, [["click", "alpha"], ["snap"], ["session", "s1"], ["snap"], ["back"], ["snap"]])
    card, sess, back = got["log"]
    assert 'data-sess="s1"' in card["sessions"] and "Great fun" in card["sessions"] and card["cardHidden"] is False
    assert sess["cardHidden"] is True
    assert "← back to alpha" in sess["sess"] and "Great fun, floaty jump" in sess["sess"]
    assert "menu hidden" in sess["sess"] and "curious" in sess["sess"] and "Review" in sess["sess"]
    assert sess["owner"] == "alpha" and sess["on"] == ["alpha"]              # still the card's panel
    assert back["cardHidden"] is False and back["sess"] == "" and back["owner"] == "alpha"


def test_the_inline_panel_and_its_close_are_gone():
    cards = (WEB / "projectcards.js").read_text()
    panel = (WEB / "projectcardpanel.js").read_text()
    assert "pc-panel" not in cards and "panel.host(" in cards and "panel.retitle(" in cards
    assert 'data-pp="close"' not in panel and "cardsessions.js" in panel
    assert "#panel-body .pp-field" in (WEB / "style.css").read_text()
    assert not re.search(r"\.pp-field\s*\{[^}]*grid-template-columns", (WEB / "projectcards.css").read_text())


def test_small():
    for f in ("projectcards.js", "projectcardpanel.js", "cardsessions.js", "style.css", "projectcards.css"):
        assert len((WEB / f).read_text().splitlines()) < 400, f
