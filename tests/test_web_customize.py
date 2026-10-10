"""The Customize page: customize.js's frame (tabs, Yours / Discover, search, sort, Add) with
customizeskills.js and customizeconnectors.js drawing the body, run in node against a stub page."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
FILES = ("customize.js", "customizeskills.js", "customizeconnectors.js")

# Just enough of a page: elements by id, dialogs whose fields the scenario fills (TYPED), a fetch
# that answers from REPLIES and records every call, and a click as {target} with closest().
STUB = r"""
const els = {}, made = [], calls = [];
global.TYPED = {}; global.REPLIES = {}; global.CONFIRM = true; global.confirms = [];
function El(tag, id) {
  const e = { tag, id, dataset: {}, innerHTML: "", textContent: "", value: "", hidden: false, handlers: {}, q: {},
    classList: { s: new Set(), add(c) { this.s.add(c); }, remove(c) { this.s.delete(c); },
      toggle(c, on) { (on === undefined ? !this.s.has(c) : on) ? this.s.add(c) : this.s.delete(c); }, contains(c) { return this.s.has(c); } },
    addEventListener(t, f) { this.handlers[t] = f; }, appendChild(c) { return c; }, insertBefore(c) { return c; },
    remove() {}, focus() {}, removeAttribute() {}, showModal() { this.open = true; },
    close() { this.open = false; if (this.handlers.close) this.handlers.close(); },
    querySelector(sel) {
      if (!this.q[sel]) { this.q[sel] = El("q", sel); const m = /name="(\w+)"/.exec(sel); if (m && m[1] in TYPED) this.q[sel].value = TYPED[m[1]]; }
      return this.q[sel];
    },
    querySelectorAll(sel) {
      if (sel !== "[data-env]") return [];
      return [...this.innerHTML.matchAll(/data-env="([^"]+)"/g)].map((m) => ({ dataset: { env: m[1] }, value: TYPED[m[1]] || "" }));
    } };
  return e;
}
const byId = (id) => els[id] || (els[id] = El("div", id));
global.window = { addEventListener() {}, dispatchEvent() {} };
global.Event = function () {};
global.localStorage = { m: {}, getItem(k) { return this.m[k] || null; }, setItem(k, v) { this.m[k] = v; } };
global.document = {
  head: El("head"), body: El("body"),
  getElementById: (id) => (id === "customize-view" || id === "cz-style") ? made.find((e) => e.id === id) || null : byId(id),
  createElement: (tag) => { const e = El(tag); made.push(e); return e; },
  querySelectorAll: () => [], addEventListener() {} };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"), render: (s) => "<p>" + s + "</p>" };
global.confirm = (m) => { confirms.push(m); return CONFIRM; };
global.fetch = async (url, opt) => {
  calls.push([url, opt && opt.body ? JSON.parse(opt.body) : null]);
  return { ok: true, statusText: "OK", json: async () => REPLIES[url] || {} };
};
const views = {};
global.nav = { register: (n, v) => { views[n] = v; } };
for (const f of process.argv.slice(3)) require(f);
const ekiCz = window.customize;
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
const body = () => els["cz-body"].innerHTML;
const tgt = (data, button) => ({ dataset: data, button,
  closest(sel) { const m = /^\[data-([\w-]+)\]$/.exec(sel); if (m) return this.dataset[m[1].replace(/-(\w)/g, (_, c) => c.toUpperCase())] !== undefined ? this : null;
    return sel === "button" && this.button ? this : null; } });
const click = (data) => ekiCz.click({ target: tgt(data, true) });
const dialog = () => made.filter((e) => e.tag === "dialog").pop();
const submit = async (d) => { await d.querySelector("form").handlers.submit({ preventDefault() {} }); await settle(); };
const out = {};
(async () => {
  await eval("(async () => {" + require("fs").readFileSync(process.argv[2], "utf8") + "})()");
  await settle();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

SKILLS = {"skills": [
    {"name": "beta", "description": "Draws diagrams", "path": "/s/beta", "source": "anthropics/skills#skills/beta", "added_at": 100},
    {"name": "alpha", "description": "Writes poems", "path": "/s/alpha", "source": None, "added_at": 300},
    {"name": "gamma", "description": "Makes diagrams too", "path": "/s/gamma", "source": "~/x", "added_at": 200}]}
CONNECTORS = {"connectors": [
    {"name": "brave", "spec": {"command": "npx", "args": ["-y", "brave"], "env": {"BRAVE_API_KEY": "••••yz"}},
     "on": True, "programs": {"claude": True, "codex": False}}]}
CATALOG = {"catalog": [
    {"id": "brave-search", "title": "Brave Search", "blurb": "Search.", "needs": ["BRAVE_API_KEY"], "folder": False, "added": False},
    {"id": "fetch", "title": "Fetch", "blurb": "Fetch pages.", "needs": [], "folder": False, "added": True}]}
REPLIES = {"/api/skills": SKILLS, "/api/connectors": CONNECTORS, "/api/connectors/discover": CATALOG,
           "/api/skills/discover": {"skills": [], "why": "gh is not installed"}}


def run(tmp_path, scenario):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub, scen = tmp_path / "stub.js", tmp_path / "scenario.js"
    stub.write_text(STUB)
    scen.write_text(f"REPLIES = {json.dumps(REPLIES)};\n" + scenario)
    out = subprocess.run(["node", str(stub), str(scen), *[str(WEB / f) for f in FILES]],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_tabs_and_the_switch_change_what_is_shown(tmp_path):
    got = run(tmp_path, """
      views.customize.show(); await settle(); out.skills = body(); out.tab = ekiCz.state.tab;
      views.customize.show("connectors"); await settle(); out.connectors = body();
      click({ mode: "discover" }); await settle(); out.discover = body();
      views.customize.show("skills"); await settle(); out.back = [ekiCz.state.tab, ekiCz.state.mode];
      click({ mode: "discover" }); await settle(); out.skdisc = body();
      out.calls = calls.map((c) => c[0]);
    """)
    assert got["tab"] == "skills" and "alpha" in got["skills"] and "brave" not in got["skills"]
    assert "brave" in got["connectors"] and "Claude ✓ · Codex –" in got["connectors"] and "alpha" not in got["connectors"]
    assert "Brave Search" in got["discover"] and "Needs BRAVE_API_KEY" in got["discover"] and "Added" in got["discover"]
    assert got["back"] == ["skills", "yours"]
    assert "gh is not installed" in got["skdisc"]
    assert got["calls"] == ["/api/skills", "/api/connectors", "/api/connectors/discover", "/api/skills", "/api/skills/discover"]


def test_search_and_sort_order_and_filter_skills(tmp_path):
    got = run(tmp_path, """
      views.customize.show("skills"); await settle(); out.byname = body();
      els["cz-sort"].value = "recent"; els["cz-sort"].handlers.change(); out.recent = body();
      out.kept = localStorage.getItem("eki.customize.sort");
      els["cz-q"].value = "DIAGRAM"; els["cz-q"].handlers.input(); out.found = body();
      els["cz-q"].value = "nothing like it"; els["cz-q"].handlers.input(); out.none = body();
    """)
    by, rec = got["byname"], got["recent"]
    assert by.index(">alpha<") < by.index(">beta<") < by.index(">gamma<")
    assert rec.index(">alpha<") < rec.index(">gamma<") < rec.index(">beta<")
    assert got["kept"] == "recent"
    assert ">beta<" in got["found"] and ">gamma<" in got["found"] and ">alpha<" not in got["found"]
    assert "No skill matches." in got["none"]


def test_remove_asks_first(tmp_path):
    got = run(tmp_path, """
      views.customize.show("skills"); await settle();
      CONFIRM = false; await click({ act: "remove", name: "alpha" }); out.after_no = calls.length;
      CONFIRM = true; await click({ act: "remove", name: "alpha" });
      out.confirms = confirms; out.calls = calls;
    """)
    assert len(got["confirms"]) == 2 and "alpha" in got["confirms"][0]
    assert got["after_no"] == 1
    assert ["/api/skills/alpha/remove", {}] in got["calls"]


def test_a_connectors_switch_posts_off(tmp_path):
    got = run(tmp_path, """
      views.customize.show("connectors"); await settle(); out.html = body();
      await click({ act: "toggle", name: "brave", on: "1" }); out.calls = calls;
    """)
    assert 'class="cz-switch on"' in got["html"] and "••••yz" not in got["html"]   # folded until opened
    assert ["/api/connectors/brave/off", {}] in got["calls"]
    assert got["calls"][-1][0] == "/api/connectors"                               # and it loads again


def test_a_discover_card_asks_for_its_keys_and_posts_them(tmp_path):
    got = run(tmp_path, """
      views.customize.show("connectors"); await settle(); click({ mode: "discover" }); await settle();
      await click({ act: "install", id: "brave-search" });
      const d = dialog(); out.sheet = d.innerHTML;
      await submit(d); out.err = d.querySelector(".error").textContent; out.open1 = d.open;
      TYPED.BRAVE_API_KEY = "sekret-key-12"; await submit(d); out.open2 = d.open;
      out.calls = calls;
    """)
    assert 'type="password" data-env="BRAVE_API_KEY"' in got["sheet"]
    assert "needs BRAVE_API_KEY" in got["err"] and got["open1"] is True
    assert got["open2"] is False
    assert ["/api/connectors/discover/brave-search", {"env": {"BRAVE_API_KEY": "sekret-key-12"}}] in got["calls"]


def test_adding_a_skill_takes_a_folder_or_a_url(tmp_path):
    got = run(tmp_path, """
      views.customize.show("skills"); await settle();
      TYPED.url = "anthropics/skills#skills/pdf"; TYPED.folder = "";
      click({ cz: "add" }); const d = dialog(); out.sheet = d.innerHTML; await submit(d);
      out.calls = calls;
    """)
    assert 'list="cz-dl-folder"' in got["sheet"] and "git URL" in got["sheet"]
    assert ["/api/skills", {"source": "anthropics/skills#skills/pdf"}] in got["calls"]


def test_the_files_stay_small_and_register_the_view():
    for f in FILES:
        assert len((WEB / f).read_text().splitlines()) < 400, f
    js = (WEB / "customize.js").read_text()
    assert 'nav.register("customize"' in js and "#customize/${t}" in js and "Plugins" not in js
