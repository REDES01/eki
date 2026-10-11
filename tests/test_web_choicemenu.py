"""choicemenu.js draws a setting's fixed choices as a small dropdown with each choice's one-line
explanation (card.choice_help) in its open list only: every option's text beside it in the html, no
title or tooltip anywhere, the button showing just the value, the bare switch a plain checkbox, missing
help meaning no explanation, and the keyboard (ArrowDown opens and moves, Enter picks, Esc closes)
through a tiny stubbed DOM. It is served with its look."""
import json
import re
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from eki import server

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# A tiny DOM: html parsed into elements with classList, dataset, attributes, closest / querySelector for
# simple class selectors (".a", ".a.b"), focus and hidden. Events go straight to a root's listeners.
DOM = r"""
const VOID = new Set(["input", "br", "img", "hr", "meta", "link"]);
function matches(el, sel) {
  return sel.split(",").some((s) => {
    const cls = s.trim().split(".").filter(Boolean);
    return el.tag && cls.length && cls.every((c) => el.classList.contains(c));
  });
}
function node(tag, attrs, parent) {
  const el = { tag, attrs, parent, children: [], text: "", listeners: {},
    get classList() { const a = this; const list = () => (a.attrs.class || "").split(/\s+/).filter(Boolean);
      return { contains: (c) => list().includes(c),
        add(c) { if (!list().includes(c)) a.attrs.class = list().concat(c).join(" "); },
        remove(c) { a.attrs.class = list().filter((x) => x !== c).join(" "); },
        toggle(c, on) { (on === undefined ? !list().includes(c) : on) ? this.add(c) : this.remove(c); } }; },
    get dataset() { const d = {}; for (const k in this.attrs) if (k.startsWith("data-"))
      d[k.slice(5).replace(/-(\w)/g, (_, x) => x.toUpperCase())] = this.attrs[k]; return d; },
    get id() { return this.attrs.id || ""; },
    get hidden() { return "hidden" in this.attrs; }, set hidden(v) { if (v) this.attrs.hidden = ""; else delete this.attrs.hidden; },
    get disabled() { return "disabled" in this.attrs; },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    focus() { document.activeElement = this; },
    matches(sel) { return matches(this, sel); },
    closest(sel) { for (let n = this; n; n = n.parent) if (n.tag && matches(n, sel)) return n; return null; },
    contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; },
    querySelectorAll(sel) { const out = []; const walk = (n) => { for (const k of n.children) { if (matches(k, sel)) out.push(k); walk(k); } };
      walk(this); return out; },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    get textContent() { return this.text + this.children.map((k) => k.textContent).join(""); } };
  return el;
}
function parse(html) {
  const root = node("root", {}, null);
  let at = root;
  const re = /<(\/?)([a-zA-Z0-9]+)((?:[^>"]|"[^"]*")*)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[4]) { at.text += m[4]; continue; }
    if (m[1]) { at = at.parent || root; continue; }
    const attrs = {};
    for (const a of m[3].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = (a[2] || "").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
    const el = node(m[2].toLowerCase(), attrs, at);
    at.children.push(el);
    if (!VOID.has(el.tag)) at = el;
  }
  return root;
}
function fire(root, type, target, extra) {
  const e = Object.assign({ type, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
    stopPropagation() {} }, extra || {});
  for (const fn of root.listeners[type] || []) fn(e);
  if (type === "click") for (const fn of document.listeners[type] || []) fn(e);
  return e;
}
global.window = global;
global.document = { activeElement: null, listeners: {},
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); } };
"""

# Draws one dropdown, then plays a list of steps against it and reports what happened.
MENU = DOM + r"""
require(process.argv[2]);
const [key, choices, value, help, disabled, steps] = JSON.parse(process.argv[3]);
const html = choiceMenu.html(key, choices, value, help, disabled);
const root = parse("<div>" + html + "</div>");
const picked = [];
choiceMenu.attach(root, (k, v) => picked.push([k, v]));
const wrap = root.querySelector(".cm"), btn = root.querySelector(".cm-btn"), list = root.querySelector(".cm-list");
const state = () => ({ open: wrap.classList.contains("open"), expanded: btn.getAttribute("aria-expanded"),
  hidden: list.hidden, active: list.getAttribute("aria-activedescendant"),
  hi: root.querySelectorAll(".cm-opt.hi").map((o) => o.dataset.value),
  focus: document.activeElement === btn ? "button" : document.activeElement === list ? "list" : null });
const log = [];
for (const s of steps) {
  if (s[0] === "click") fire(root, "click", s[1] === "button" ? btn : s[1] === "outside" ? root
    : root.querySelectorAll(".cm-opt").find((o) => o.dataset.value === s[1]).children[0]);
  else if (s[0] === "hover") fire(root, "mouseover", root.querySelectorAll(".cm-opt").find((o) => o.dataset.value === s[1]));
  else fire(root, "keydown", document.activeElement || btn, { key: s[1] });
  log.push(state());
}
process.stdout.write(JSON.stringify({ html, picked, log, state: state(), api: Object.keys(choiceMenu).sort() }));
"""

READ = ["always", "when-relevant"]
READ_HELP = {"always": "Every run in this project reads it before starting.",
             "when-relevant": "Runs are told it exists and read it only when their work touches it."}
ACCESS = ["queue", "exclusive", "shared"]
ACCESS_HELP = {"queue": "Calls wait in line and are served in order, a few at a time (slots).",
               "exclusive": "One run holds it from first use until that run ends; others wait.",
               "shared": "Any number of runs can use it at once."}


def node(tmp_path, script, *args):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(script)
    got = subprocess.run(["node", str(stub), *map(str, args)], capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def menu(tmp_path, key, choices, value, help=None, disabled=False, steps=()):
    return node(tmp_path, MENU, WEB / "choicemenu.js", json.dumps([key, choices, value, help, disabled, list(steps)]))


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def test_every_option_carries_its_explanation_in_the_list_and_nothing_else_does(tmp_path):
    for key, cs, value, help in (("read", READ, "always", READ_HELP), ("access", ACCESS, "exclusive", ACCESS_HELP)):
        got = menu(tmp_path, key, cs, value, help)
        html = got["html"]
        for c in cs:
            assert f'<span class="cm-name">{c}</span><span class="cm-help">{esc(help[c])}</span>' in html
        assert html.count('class="cm-help"') == len(cs)
        assert "title=" not in html and "cm-tip" not in html and 'role="tooltip"' not in html
        assert html.startswith('<div class="cm">')
        inner = re.search(r"<button[^>]*>(.*?)</button>", html, re.S).group(1)
        assert inner == f'<span class="cm-val">{value}</span><span class="cm-caret" aria-hidden="true">▾</span>'
        assert got["api"] == ["attach", "html"]
        assert 'role="listbox"' in html and html.count('role="option"') == len(cs)
        assert 'aria-haspopup="listbox"' in html and 'aria-expanded="false"' in html


def test_missing_help_draws_no_explanation_and_still_works(tmp_path):
    for help in (None, {}, {"always": "only one"}):
        got = menu(tmp_path, "read", READ, "when-relevant", help,
                   steps=[("click", "button"), ("click", "always")])
        assert '<span class="cm-name">when-relevant</span></li>' in got["html"]
        assert "cm-tip" not in got["html"] and "title=" not in got["html"]
        assert got["picked"] == [["read", "always"]]
    html = menu(tmp_path, "read", READ, "always", {"always": "only one"})["html"]
    assert html.count('class="cm-help"') == 1 and '<span class="cm-help">only one</span>' in html


def test_keyboard_opens_moves_picks_and_escape_closes(tmp_path):
    got = menu(tmp_path, "access", ACCESS, "queue", ACCESS_HELP,
               steps=[("key", "ArrowDown"), ("key", "ArrowDown"), ("key", "ArrowUp"), ("key", "ArrowDown"), ("key", "Enter")])
    opened, down, up, down2, picked = got["log"]
    assert opened["open"] and opened["expanded"] == "true" and not opened["hidden"] and opened["hi"] == ["queue"]
    assert opened["focus"] == "list" and opened["active"].endswith("-0")
    assert down["hi"] == ["exclusive"] and down["active"].endswith("-1")
    assert up["hi"] == ["queue"] and down2["hi"] == ["exclusive"]
    assert not picked["open"] and picked["expanded"] == "false" and picked["hidden"] and picked["focus"] == "button"
    assert got["picked"] == [["access", "exclusive"]]

    got = menu(tmp_path, "access", ACCESS, "queue", ACCESS_HELP, steps=[("key", "Enter"), ("key", "ArrowDown"), ("key", "Escape")])
    assert got["log"][0]["open"] and got["log"][1]["hi"] == ["exclusive"]
    assert not got["state"]["open"] and got["state"]["focus"] == "button" and got["picked"] == []
    got = menu(tmp_path, "access", ACCESS, "queue", ACCESS_HELP, steps=[("key", " ")])
    assert got["state"]["open"]


def test_hover_highlights_click_outside_closes_and_picking_the_same_value_saves_nothing(tmp_path):
    got = menu(tmp_path, "access", ACCESS, "queue", ACCESS_HELP,
               steps=[("click", "button"), ("hover", "shared"), ("click", "outside"), ("click", "button"), ("click", "queue")])
    assert got["log"][1]["hi"] == ["shared"]
    assert not got["log"][2]["open"] and got["log"][3]["open"]
    assert got["picked"] == []


def test_booleans_come_back_as_booleans_and_a_bad_value_is_marked(tmp_path):
    got = menu(tmp_path, "bare", [False, True], "yes", {"true": "on", "false": "off"},
               steps=[("click", "button"), ("click", "true")])
    assert 'class="set-in cm-btn invalid"' in got["html"] and 'data-type="boolchoice"' in got["html"]
    assert '<span class="cm-val">yes</span>' in got["html"] and "cm-tip" not in got["html"]
    assert got["picked"] == [["bare", True]]


def test_disabled_does_not_open(tmp_path):
    got = menu(tmp_path, "read", READ, "always", READ_HELP, disabled=True, steps=[("click", "button"), ("key", "Enter")])
    assert "disabled" in got["html"].split("</button>")[0]
    assert not got["state"]["open"] and got["picked"] == []


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_served_small_and_loaded_by_the_panel(web):
    for f in ("choicemenu.js", "choicemenu.css"):
        with urllib.request.urlopen(web + "/ui/" + f, timeout=10) as r:
            assert r.status == 200 and r.read().strip(), f
        assert len((WEB / f).read_text().splitlines()) < 400, f
    panel = (WEB / "projectcardpanel.js").read_text()
    assert "/ui/choicemenu.js" in panel and "/ui/choicemenu.css" in panel


def test_the_bare_switch_is_a_plain_checkbox_without_a_tooltip(tmp_path):
    from test_web_cardchoices import AGENT, run
    help = {"bare": {"true": "On: the agent gets only its system prompt.", "false": "Off: as usual."},
            "access": {"shared": "Any number of runs can use it at once."}}
    for bare in (True, False):
        for card_help in (help, None):
            card = dict(AGENT, spec={"kind": "agent", "bare": bare}, **({"choice_help": card_help} if card_help else {}))
            html = run(tmp_path, card)["settings"]
            assert re.search(r'<label class="set-check"><input type="checkbox" data-key="bare"[^>]*>', html), html
            assert "cm-tip" not in html and 'role="tooltip"' not in html and "title=" not in html
            assert esc(help["bare"]["true"]) not in html and esc(help["bare"]["false"]) not in html
            assert 'class="set-in cm-btn" data-key="access"' in html
