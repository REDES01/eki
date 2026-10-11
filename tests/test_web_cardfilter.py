"""The Cards area's type filter (projectcardfilter.js in projectcards.js's header row): the kinds present with
their counts, picking one narrows the grid, the choice kept per project in localStorage (and a broken storage
harmless), the grid sorted by kind then name, the keys, and an open card left open. Driven through the page's
own click and key handlers."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

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
const out = { log: [], closes: [] };
const doc = {};
const [files, steps, mode] = [process.argv.slice(2, 7), JSON.parse(process.argv[7]), process.argv[8]];
const store = {};
global.localStorage = mode === "throw"
  ? { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); }, removeItem() { throw new Error("no"); } }
  : { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; } };
global.Event = class { constructor(type) { this.type = type; } };
global.window = global;
global.addEventListener = () => {};
global.dispatchEvent = () => {};
global.setInterval = () => 1; global.clearInterval = () => {};
global.confirm = () => true;
global.CSS = { escape: (s) => s };
global.document = { getElementById: el, addEventListener(ev, fn) { doc[ev] = fn; }, hidden: false, activeElement: null };
global.md = { esc: (s) => String(s), render: (t) => t, outline: () => [] };
const TITLES = { knowledge: "Documents", service: "Tool" };
const card = (name, kind) => ({ name, kind, title: TITLES[kind], about: "", state: "idle", spec: { kind }, editable: true, card: "/p/components/" + name });
const CARDS = { cards: [
  card("zeta", "knowledge"), card("beta", "service"), card("alpha", "knowledge"), card("gamma", "agent"),
  card("mid", "knowledge"), card("odd", ""), card("api", "service"),
], readonly: "" };
global.fetch = async (url) => {
  let d = {};
  if (/\\/cards$/.test(url)) d = CARDS;
  else if (/\\/uses$/.test(url)) d = { uses: [] };
  else if (/\\/sessions$/.test(url)) d = { sessions: [] };
  return { ok: true, statusText: "OK", json: async () => d, text: async () => "" };
};
for (const f of files) require(f);
const host = panel.host;
panel.host = (o) => {
  if (!o.__logged) { const c = o.close; o.close = (why) => { out.closes.push([o.name, why]); c(why); }; o.__logged = true; }
  return host(o);
};
const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0)); };
const target = (attr, key, value) => ({ dataset: { [key]: value }, matches: (s) => s === ".pc-tile" && attr === "data-card",
  closest(sel) { return sel === "[" + attr + "]" ? this : null; } });
const fire = (box, ev, e) => { for (const fn of (box && box.listeners[ev]) || []) fn(Object.assign({ preventDefault() {}, stopPropagation() {} }, e)); };
const area = el("pj-cards");
(async () => {
  for (const s of steps) {
    if (s[0] === "mount") { projectCards.mount(area, s[1]); await tick(); }
    if (s[0] === "unmount") projectCards.unmount();
    if (s[0] === "click") { fire(area, "click", { target: target("data-card", "card", s[1]) }); await tick(); }
    if (s[0] === "menu") fire(area, "click", { target: target("data-filter", "filter", "toggle") });
    if (s[0] === "pick") fire(area, "click", { target: target("data-fkind", "fkind", s[1]) });
    if (s[0] === "key") fire(area, "keydown", { key: s[1], target: target("data-filter", "filter", "toggle") });
    if (s[0] === "outside") doc.pointerdown({ target: { closest: () => null } });
    if (s[0] === "snap") {
      const grid = area.querySelector(".pc-grid").innerHTML;
      out.log.push({ filter: area.querySelector(".pc-filter").innerHTML,
        tiles: [...grid.matchAll(/data-card="(\\w+)"/g)].map((m) => m[1]),
        kinds: [...grid.matchAll(/class="pc-kind dim">([^<]*)</g)].map((m) => m[1]),
        on: [...grid.matchAll(/class="pc-tile[^"]*\\bon" data-card="(\\w+)"/g)].map((m) => m[1]),
        add: grid.includes("+ Add card"), hidden: el("panel").hidden, owner: panel.owner() && panel.owner().name,
        store: Object.assign({}, store) });
    }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""

FILES = ("panel.js", "projectcardfilter.js", "projectcards.js", "projectcardpanel.js", "cardsessions.js")


def run(tmp_path, steps, mode="ok"):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    files = [str(WEB / f) for f in FILES]
    out = subprocess.run(["node", str(stub), *files, json.dumps(steps), mode], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def options(html):
    """[(kind, label text)] of the open list, the label's tags dropped."""
    return [(k, " ".join(re.sub(r"<[^>]+>", " ", t).split()))
            for k, t in re.findall(r'data-fkind="([^"]*)">(.*?)</li>', html, re.S)]


def test_the_options_are_the_kinds_present_with_counts(tmp_path):
    closed, opened = run(tmp_path, [["mount", "p1"], ["snap"], ["menu"], ["snap"]])["log"]
    assert "All types" in closed["filter"] and "pc-flist" not in closed["filter"]
    assert options(opened["filter"]) == [("", "All types"), ("?", "? 1"), ("agent", "agent 1"),
                                         ("knowledge", "Documents 3"), ("service", "Tool 2")]
    assert "instructions" not in opened["filter"] and "mcp" not in opened["filter"]


def test_tiles_are_sorted_by_kind_then_name(tmp_path):
    snap = run(tmp_path, [["mount", "p1"], ["snap"]])["log"][0]
    assert snap["tiles"] == ["odd", "gamma", "alpha", "mid", "zeta", "api", "beta"] and snap["add"]
    # a tile shows its kind's title, the raw kind when it has none
    assert snap["kinds"] == ["?", "agent", "Documents", "Documents", "Documents", "Tool", "Tool"]


def test_picking_a_kind_narrows_the_grid_and_keeps_the_add_tile(tmp_path):
    snap = run(tmp_path, [["mount", "p1"], ["menu"], ["pick", "knowledge"], ["snap"]])["log"][0]
    assert snap["tiles"] == ["alpha", "mid", "zeta"] and snap["add"]
    assert "pc-flist" not in snap["filter"] and "Documents" in snap["filter"]
    snap = run(tmp_path, [["mount", "p1"], ["pick", "knowledge"], ["pick", ""], ["snap"]])["log"][0]
    assert len(snap["tiles"]) == 7


def test_the_choice_is_kept_per_project(tmp_path):
    log = run(tmp_path, [["mount", "p1"], ["pick", "service"], ["snap"], ["unmount"], ["mount", "p1"], ["snap"],
                         ["mount", "p2"], ["snap"], ["mount", "p1"], ["pick", ""], ["snap"]])["log"]
    assert log[0]["store"] == {"eki.cards.type.p1": "service"}
    assert log[1]["tiles"] == ["api", "beta"]                      # restored on mount
    assert len(log[2]["tiles"]) == 7                                # another project doesn't see it
    assert log[3]["store"] == {} and len(log[3]["tiles"]) == 7       # All types forgets it


def test_a_kept_kind_no_longer_present_shows_all(tmp_path):
    log = run(tmp_path, [["mount", "p1"], ["pick", "skills"], ["snap"]])["log"]
    assert len(log[0]["tiles"]) == 7 and "All types" in log[0]["filter"]


def test_a_throwing_storage_breaks_nothing(tmp_path):
    log = run(tmp_path, [["mount", "p1"], ["snap"], ["pick", "agent"], ["snap"]], mode="throw")["log"]
    assert len(log[0]["tiles"]) == 7 and log[1]["tiles"] == ["gamma"]


def test_the_keys_and_a_press_outside(tmp_path):
    log = run(tmp_path, [["mount", "p1"], ["key", "ArrowDown"], ["snap"], ["key", "ArrowDown"], ["key", "ArrowDown"],
                         ["key", "Enter"], ["snap"], ["key", "Enter"], ["key", "Escape"], ["snap"],
                         ["menu"], ["outside"], ["snap"]])["log"]
    opened, picked, escaped, outside = log
    assert "pc-flist" in opened["filter"] and re.search(r'pc-fopt active[^>]*data-fkind=""', opened["filter"])
    assert picked["tiles"] == ["gamma"] and "pc-flist" not in picked["filter"]   # All → ? → agent
    assert "pc-flist" not in escaped["filter"] and escaped["tiles"] == ["gamma"]
    assert "pc-flist" not in outside["filter"]


def test_the_open_card_stays_open_when_the_filter_hides_it(tmp_path):
    got = run(tmp_path, [["mount", "p1"], ["menu"], ["click", "gamma"], ["pick", "service"], ["snap"], ["pick", ""],
                         ["snap"]])
    hidden, back = got["log"]                                # a tile click with the list open still opens it
    assert got["closes"] == []
    assert hidden["owner"] == "gamma" and hidden["hidden"] is False and hidden["tiles"] == ["api", "beta"]
    assert back["on"] == ["gamma"]


def test_small_and_valid():
    assert len((WEB / "projectcardfilter.js").read_text().splitlines()) < 400
    assert len((WEB / "projectcards.js").read_text().splitlines()) < 400
