"""Unsent text and pictures are kept per conversation in the window's localStorage (ekiDrafts in attach.js): typing
saves after a pause, switching threads puts each one's draft back, sending clears it, and blocked storage costs
nothing but the drafts."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# attach.js and app.js on a page of plain objects, an in-memory localStorage and timers run by hand.
STUB = r"""
const els = {};
function mk(id) {
  const on = {};
  const e = { id, value: "", innerHTML: "", textContent: "", title: "", hidden: false, disabled: false, className: "",
    style: {}, dataset: {}, children: [], options: [{ text: "Auto" }], selectedIndex: 0, scrollHeight: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener(k, f) { (on[k] = on[k] || []).push(f); },
    fire(k, ev) { (on[k] || []).forEach((f) => f(ev || { preventDefault() {}, target: e })); },
    appendChild(c) { e.children.push(c); c.parent = e; }, remove() { if (e.parent) e.parent.children.splice(e.parent.children.indexOf(e), 1); },
    setAttribute() {}, focus() {}, contains: () => false, matches: () => false, closest: () => mk("x"),
    querySelector(sel) { return (e.q = e.q || {})[sel] || (e.q[sel] = mk(sel)); }, querySelectorAll: () => [],
    requestSubmit() { e.fire("submit", { preventDefault() {}, submitter: null }); } };
  return e;
}
const el = (id) => els[id] || (els[id] = mk(id));
const winOn = {};
const fired = [];
let blocked = process.argv[4] === "blocked";
const mem = {};
const storage = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); },
  removeItem: (k) => { delete mem[k]; } };
global.window = { addEventListener: (k, f) => { (winOn[k] = winOn[k] || []).push(f); },
  dispatchEvent: (ev) => { fired.push(ev.detail); }, isSecureContext: false };
Object.defineProperty(window, "localStorage", { get() { if (blocked) throw new Error("SecurityError"); return storage; } });
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o.detail; } };
global.document = { getElementById: el, createElement: () => mk("new"), addEventListener() {}, body: mk("body"),
  visibilityState: "visible", activeElement: null };
global.location = { hash: "" };
const timers = [];
global.setTimeout = (f, ms) => { const t = { f, ms }; timers.push(t); return t; };
global.clearTimeout = (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); };
const tick = () => timers.splice(0).forEach((t) => t.f());
global.URL = { createObjectURL: () => "blob:x", revokeObjectURL() {} };
global.MutationObserver = class { observe() {} };
global.navigator = {};
let asked = null;
global.fetch = (path, opts) => {
  if (path === "/api/ask") {
    asked = JSON.parse(opts.body);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ thread: asked.thread || "T9", run: 1 }) });
  }
  return new Promise(() => {});
};
global.md = { esc: (s) => String(s), render: (s) => String(s || "") };
global.cards = { render: () => "" };
global.side = { loadThreads: () => Promise.resolve() };
let openThread = null;
global.nav = { thread: (f) => { openThread = f; }, start() {}, current: () => null };
require(process.argv[2]);
global.ekiDrafts = window.ekiDrafts; global.ekiAttach = window.ekiAttach;   // a page's window is its global
require(process.argv[3]);

const prompt = el("prompt"), strip = el("strip");
const type = (text) => { prompt.value = text; prompt.fire("input"); };
const thumbs = () => strip.children.length;
const out = {};
const steps = {
  debounce() {
    openThread("A");
    type("half a thought");
    out.before = mem["eki.draft.A"] || null;
    tick();
    out.after = JSON.parse(mem["eki.draft.A"]);
    out.fired = fired.slice();
  },
  swap() {
    openThread("A");
    type("for A");
    ekiAttach.restore(["/pics/a.png"]);     // as if picked; restore draws it with its path set
    ekiDrafts.flush(prompt);
    openThread("B");
    out.onB = { text: prompt.value, thumbs: thumbs() };
    type("for B");
    openThread("A");
    out.backA = { text: prompt.value, paths: ekiAttach.paths(), thumbs: thumbs() };
    out.storedB = JSON.parse(mem["eki.draft.B"]).text;
    openThread(null);
    out.newChat = prompt.value;
    out.hasA = ekiDrafts.has("A");
    out.hasC = ekiDrafts.has("C");
  },
  async send() {
    openThread("A");
    type("send me");
    ekiAttach.restore(["/pics/s.png"]);
    tick();
    out.kept = !!mem["eki.draft.A"];
    el("composer").fire("submit", { preventDefault() {}, submitter: null });
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    out.asked = asked;
    out.gone = !("eki.draft.A" in mem);
    out.prompt = prompt.value;
  },
  empty() {
    ekiDrafts.save("A", "words", []);
    out.saved = JSON.parse(mem["eki.draft.A"]);
    ekiDrafts.save("A", "   ", []);
    out.gone = !("eki.draft.A" in mem);
    ekiDrafts.save("P", "", ["/pics/p.png"]);
    out.picOnly = ekiDrafts.get("P");
    out.fired = fired.slice();
  },
  blocked() {
    openThread("A");
    type("nowhere to go");
    tick();
    ekiDrafts.save("A", "x", ["/p.png"]);
    ekiDrafts.clear("A");
    winOn.beforeunload.forEach((f) => f());
    openThread("B");
    out.get = ekiDrafts.get("A");
    out.has = ekiDrafts.has("A");
    out.prompt = prompt.value;
  },
};
(async () => {
  await steps[process.argv[4]]();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


def run(tmp_path, step):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "drafts.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "attach.js"), str(WEB / "app.js"), step],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_typing_saves_after_the_pause(tmp_path):
    got = run(tmp_path, "debounce")
    assert got["before"] is None                                   # not on every key
    assert got["after"]["text"] == "half a thought" and got["after"]["attachments"] == []
    assert got["after"]["at"] > 0 and got["fired"] == ["A"]


def test_switching_threads_keeps_each_ones_draft(tmp_path):
    got = run(tmp_path, "swap")
    assert got["onB"] == {"text": "", "thumbs": 0}
    assert got["backA"] == {"text": "for A", "paths": ["/pics/a.png"], "thumbs": 1}
    assert got["storedB"] == "for B" and got["newChat"] == ""
    assert got["hasA"] and not got["hasC"]


def test_sending_clears_the_draft(tmp_path):
    got = run(tmp_path, "send")
    assert got["kept"]
    assert got["asked"]["prompt"] == "send me" and got["asked"]["attachments"] == ["/pics/s.png"]
    assert got["gone"] and got["prompt"] == ""


def test_an_empty_draft_is_removed(tmp_path):
    got = run(tmp_path, "empty")
    assert got["saved"]["text"] == "words"
    assert got["gone"]
    assert got["picOnly"]["attachments"] == ["/pics/p.png"]          # pictures alone are a draft
    assert got["fired"] == ["A", "A", "P"]                           # appeared, went, appeared


def test_blocked_storage_throws_nothing(tmp_path):
    got = run(tmp_path, "blocked")
    assert got == {"get": None, "has": False, "prompt": ""}


def test_drafts_stay_in_the_window():
    js = (WEB / "attach.js").read_text()
    assert '"eki.draft."' in js and "localStorage" in js and "window.ekiDrafts" in js
    app = (WEB / "app.js").read_text()
    assert "localStorage only" in app and "nothing is kept here\n" not in app
    assert "draft" not in app[app.index('call("/api/ask"'):app.index("$(\"prompt\").value = \"\"")]
    assert len(js.splitlines()) <= 400 and len(app.splitlines()) <= 400
