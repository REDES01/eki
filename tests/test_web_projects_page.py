"""The Projects page (projectspage.js): cards with a two-line description and a quiet folder line, search, sort;
the "Create a project" sheet (newproject.js): the body it posts, the API's error inline, "Choose…" only in the
Mac shell; and a project's ⋯ menu (projectmenu.js): "Delete project…", its confirm, the DELETE it sends."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough of a page: elements remember their listeners, querySelector hands out one element per selector.
STUB = r"""
const mk = (id) => {
  const e = { id, value: "", innerHTML: "", textContent: "", hidden: false, disabled: false, dataset: {}, l: {},
    sel: {}, opened: false, closed: false,
    classList: { s: new Set(), toggle(c, on) { on = on === undefined ? !this.s.has(c) : on; on ? this.s.add(c) : this.s.delete(c); return on; },
                 remove(c) { this.s.delete(c); }, add(c) { this.s.add(c); } },
    addEventListener(t, f) { this.l[t] = f; }, appendChild() {}, insertBefore() {}, focus() {},
    querySelector(s) { return this.sel[s] || (this.sel[s] = mk(s)); }, querySelectorAll: () => [],
    showModal() { this.opened = true; }, close() { this.closed = true; } };
  return e;
};
const els = {};
const store = {};
global.window = { addEventListener() {} };
global.localStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } };
global.document = { getElementById: (id) => id === "projects-page" ? null : els[id] || (els[id] = mk(id)), createElement: () => mk(null),
  body: mk("body"), head: mk("head"), addEventListener() {} };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
global.setInterval = () => 0; global.clearInterval = () => {};
const went = [], posted = [], deleted = [];
global.nav = { register() {}, go: (h) => went.push(h) };
const input = JSON.parse(process.argv[3]);
global.fetch = async (url, opts) => {
  if (opts && opts.method === "DELETE") { deleted.push([url, opts]); return DELETE_ANSWER; }
  if (opts && opts.method === "POST") { posted.push([url, JSON.parse(opts.body)]); return input.answer; }
  return { ok: true, json: async () => ({ projects: input.projects || [], user_home: "/Users/me" }) };
};
if (input.shell) window.ekiShell = { canPickFolder: () => true, pickFolder: async () => "/Users/me/x" };
for (const f of process.argv[2].split(",")) require(f);
const tick = () => new Promise((r) => setTimeout(r, 0));
(async () => {
  const out = {};
  if (input.do === "cards") out.html = window.projectsPage.cards(input.projects, input.opts);
  if (input.do === "page") {
    if (input.sort) store["eki.projects.sort"] = input.sort;
    window.projectsPage.show(); await tick(); await tick();
    out.first = els["pp-cards"].innerHTML;
    els["pp-q"].value = input.q; els["pp-q"].l.input();
    out.searched = els["pp-cards"].innerHTML;
    els["pp-sort"].l.click({ target: { closest: () => ({ dataset: { sort: "name" } }) } });
    out.stored = store["eki.projects.sort"];
  }
  if (input.do === "sheet") {
    const d = window.newProject.open();
    out.html = d.innerHTML; out.opened = d.opened;
    const f = input.fields;
    d.querySelector("#np-name").value = f.name; d.querySelector("#np-folder").value = f.folder || "";
    d.querySelector("#np-desc").value = f.description || "";
    await d.querySelector("form").l.submit({ preventDefault() {} });
    out.posted = posted; out.err = d.querySelector("#np-err").textContent; out.went = went; out.closed = d.closed;
  }
  if (input.do === "menu") {
    out.eki = window.projectMenu.button(input.projects[0]); out.ekiItems = window.projectMenu.items(input.projects[0]);
    out.p1 = window.projectMenu.button(input.projects[1]);
    out.menu = window.projectMenu.open(input.projects[1], null, () => {}).innerHTML;
    out.sentences = input.projects.map(window.projectMenu.sentence);
  }
  if (input.do === "confirm") {
    let done = null;
    const d = window.projectMenu.confirm(input.projects[1], (got) => { done = got; });
    out.html = d.innerHTML; out.opened = d.opened;
    if (input.click === "cancel") d.querySelector("#pm-cancel").l.click();
    else await d.querySelector("#pm-delete").l.click();
    out.deleted = deleted; out.closed = d.closed; out.err = d.querySelector("#pm-err").textContent; out.done = done;
  }
  if (input.do === "card") {
    const opened = [];
    window.projectMenu.open = (p, anchor, done) => opened.push([p.id, done]);
    window.projectsPage.show(); await tick(); await tick();
    out.html = els["pp-cards"].innerHTML;
    const ev = { stopped: false, prevented: false, target: { closest: () => ({ dataset: { pm: "p1" } }) },
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    els["pp-cards"].l.click(ev);
    out.prevented = ev.prevented; out.stopped = ev.stopped; out.opened = opened.map((o) => o[0]);
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


OK_DELETE = "{ ok: true, json: async () => ({ id: 'p1', stopped: 2 }) }"


def run(tmp_path, files, data, delete_answer=OK_DELETE):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB.replace("return DELETE_ANSWER;", f"return ({delete_answer});"))
    out = subprocess.run(["node", str(stub), ",".join(str(WEB / f) for f in files), json.dumps(data)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


NOW = time.time()
PROJECTS = [
    {"id": "eki", "name": "eki", "description": None, "folder": "/Users/me/eki", "chats": 3, "last_active": NOW - 50},
    {"id": "p1", "name": "tokenbar", "description": "A menu bar meter for tokens", "folder": "/Users/me/tokenbar",
     "chats": 1, "last_active": NOW - 10},
    {"id": "p2", "name": "Atlas", "description": "Maps of old <roads>", "folder": None, "chats": 0,
     "last_active": NOW - 900},
]


def test_a_card_per_project_with_the_clamp_and_the_quiet_line(tmp_path):
    html = run(tmp_path, ["projectspage.js"], {"do": "cards", "projects": PROJECTS,
                                                "opts": {"home": "/Users/me", "sort": "recent"}})["html"]
    assert html.count('class="card pp-card"') == 3 and html.count('class="clamp pp-desc"') == 3
    assert "~/tokenbar · 1 chat<" in html and "~/eki · 3 chats<" in html and "no folder · 0 chats" in html
    assert 'href="#project/p1"' in html and "Maps of old &lt;roads>" in html and "Active " in html
    assert html.index("tokenbar") < html.index("eki itself") < html.index("Atlas")    # newest activity first


def test_ekis_card_has_its_fixed_description_and_no_edit(tmp_path):
    html = run(tmp_path, ["projectspage.js"], {"do": "cards", "projects": PROJECTS[:1], "opts": {}})["html"]
    assert "eki itself — its own source" in html and "Edit" not in html
    js = (WEB / "project.js").read_text()
    assert 'p.id === "eki" ? ""' in js                  # the project page gives eki no Edit link


@pytest.mark.parametrize("q, names", [("token", ["tokenbar"]), ("ROADS", ["Atlas"]), ("its own", ["eki"]),
                                      ("nothing", [])])
def test_search_filters_by_name_and_description(tmp_path, q, names):
    html = run(tmp_path, ["projectspage.js"], {"do": "cards", "projects": PROJECTS, "opts": {"q": q}})["html"]
    for p in PROJECTS:
        assert (f"<h3>{p['name']}</h3>" in html) == (p["name"] in names)
    if not names:
        assert "No project matches." in html


def test_sort_by_name_orders_the_cards(tmp_path):
    html = run(tmp_path, ["projectspage.js"], {"do": "cards", "projects": PROJECTS, "opts": {"sort": "name"}})["html"]
    assert html.index("<h3>Atlas") < html.index("<h3>eki") < html.index("<h3>tokenbar")


def test_the_page_loads_searches_as_you_type_and_remembers_the_sort(tmp_path):
    got = run(tmp_path, ["projectspage.js"], {"do": "page", "projects": PROJECTS, "q": "atlas", "sort": "name"})
    assert got["first"].index("<h3>Atlas") < got["first"].index("<h3>tokenbar")    # the remembered sort
    assert "<h3>Atlas" in got["searched"] and "<h3>tokenbar" not in got["searched"]
    assert got["stored"] == "name"
    js = (WEB / "projectspage.js").read_text()
    for name in ('class="page-title">Projects', 'class="search"', 'class="seg"', "Recent activity",
                 'class="btn-primary" id="pp-new">New project', 'class="cards"'):
        assert name in js, name


def test_the_sheet_posts_the_documented_body_and_opens_the_project(tmp_path):
    stub_answer = "{ ok: true, json: async () => ({ id: 'ab12' }) }"
    got = run_sheet(tmp_path, stub_answer, {"name": " Atlas ", "folder": "~/code/atlas", "description": " Maps "})
    assert got["posted"] == [["/api/projects", {"name": "Atlas", "folder": "~/code/atlas", "description": "Maps"}]]
    assert got["went"] == ["project/ab12"] and got["closed"] and got["err"] == ""
    bare = run_sheet(tmp_path, stub_answer, {"name": "Atlas"})
    assert bare["posted"] == [["/api/projects", {"name": "Atlas"}]]          # no folder, no description: left out


def test_the_sheet_shows_the_apis_error(tmp_path):
    got = run_sheet(tmp_path, "{ ok: false, statusText: 'Bad Request', json: async () => ({ error: 'project Atlas exists' }) }",
                    {"name": "Atlas"})
    assert got["err"] == "project Atlas exists" and got["went"] == [] and not got["closed"]


def test_choose_shows_only_in_the_mac_shell(tmp_path):
    plain = run_sheet(tmp_path, "{ ok: true, json: async () => ({ id: 'x' }) }", {"name": "a"})
    assert plain["opened"] and "Choose…" not in plain["html"]
    for name in ("Create a project", "What are you working on?", "What are you trying to achieve?", "Use a folder",
                 'list="np-folders"', "Cancel", "Create project"):
        assert name in plain["html"], name
    mac = run_sheet(tmp_path, "{ ok: true, json: async () => ({ id: 'x' }) }", {"name": "a"}, shell=True)
    assert "Choose…" in mac["html"]
    js = (WEB / "newproject.js").read_text()
    assert '"/api/folders?prefix=" + encodeURIComponent(prefix)' in js and "ekiShell.pickFolder()" in js


def run_sheet(tmp_path, answer_js, fields, shell=False):
    # the POST answer is a JS object (it has a json() method), so it goes into the stub's source
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "sheet.js"
    stub.write_text(STUB.replace("return input.answer;", f"return ({answer_js});"))
    data = {"do": "sheet", "fields": fields, "shell": shell}
    out = subprocess.run(["node", str(stub), str(WEB / "newproject.js"), json.dumps(data)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_menu_offers_delete_but_not_for_eki(tmp_path):
    got = run(tmp_path, ["projectmenu.js"], {"do": "menu", "projects": PROJECTS})
    assert got["eki"] == "" and got["ekiItems"] == ""                       # eki's own project: no ⋯, no Delete
    assert 'data-pm="p1"' in got["p1"] and "⋯" in got["p1"] and "Delete project…" in got["menu"]
    assert got["sentences"][1] == ("Stops its work, archives its 1 chats, removes eki's worktrees. "
                                   "Your folder /Users/me/tokenbar is not touched.")
    assert got["sentences"][2].endswith("archives its 0 chats, removes eki's worktrees. Your folder no folder is not touched.")


def test_delete_sends_delete_with_x_eki_and_closes(tmp_path):
    got = run(tmp_path, ["projectmenu.js"], {"do": "confirm", "projects": PROJECTS})
    assert got["opened"] and "Your folder /Users/me/tokenbar is not touched." in got["html"]
    assert 'id="pm-cancel">Cancel' in got["html"] and 'id="pm-delete">Delete' in got["html"]
    assert got["deleted"] == [["/api/projects/p1", {"method": "DELETE", "headers": {"X-Eki": "1"}}]]
    assert got["closed"] and got["err"] == "" and got["done"] == {"id": "p1", "stopped": 2}


def test_delete_shows_the_apis_error(tmp_path):
    got = run(tmp_path, ["projectmenu.js"], {"do": "confirm", "projects": PROJECTS},
              delete_answer="{ ok: false, statusText: 'Bad Request', json: async () => ({ error: 'eki itself can not be deleted' }) }")
    assert got["err"] == "eki itself can not be deleted" and not got["closed"] and got["done"] is None


def test_cancel_sends_nothing(tmp_path):
    got = run(tmp_path, ["projectmenu.js"], {"do": "confirm", "projects": PROJECTS, "click": "cancel"})
    assert got["deleted"] == [] and got["closed"] and got["done"] is None


def test_a_cards_menu_opens_without_following_the_link(tmp_path):
    got = run(tmp_path, ["projectmenu.js", "projectspage.js"], {"do": "card", "projects": PROJECTS})
    assert got["html"].count('class="pm-more"') == 2 and 'data-pm="eki"' not in got["html"]
    assert got["prevented"] and got["stopped"] and got["opened"] == ["p1"]
    js = (WEB / "projectspage.js").read_text()
    assert "window.projectMenu.open(p, b, () => load())" in js                     # a delete reloads the list
