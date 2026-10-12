"""The "Create a project" sheet (newproject.js): it opens on two starts. "Open a folder" uses the Mac shell's picker
at once (canPickFolder), else a path field with the recent project folders as rows; the name follows the folder. A
folder that is a project already says so and Create opens it. "Start new" asks for a name and shows the folder it
will make (GET /api/projectstart?name=), "Change…" picks another parent, and nothing is made until Create. A folder
eki keeps no history of yet comes back as {ask: {...}}: the offer shows in place of the error line, with "Keep a
history" (history "start") and "Chat only" (history "none"); a bad fit gets only "Chat only". What stays out is in a
closed details fold, and outside it the sheet says nothing of git."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
SHEET = ",".join(str(WEB / f) for f in ("newprojectgh.js", "newproject.js"))

# Just enough of a page: one element per selector, listeners remembered. GETs are answered by the first matching
# prefix in input.gets, POSTs from input.answers in turn. input.steps drives the sheet; the elements named in
# input.show are reported after.
STUB = r"""
const mk = (id) => ({ id, value: "", innerHTML: "", textContent: "", hidden: false, disabled: false, placeholder: "",
  l: {}, sel: {}, opened: false, closed: false, className: "",
  addEventListener(t, f) { (this.l[t] = this.l[t] || []).push(f); }, appendChild() {}, focus() {},
  fire(t, e) { return Promise.all((this.l[t] || []).map((f) => f(e))); },
  querySelector(s) { return this.sel[s] || (this.sel[s] = mk(s)); }, querySelectorAll: () => [],
  showModal() { this.opened = true; }, close() { this.closed = true; } });
const input = JSON.parse(process.argv[3]);
const picks = [];
global.window = { addEventListener() {} };
if ("pick" in input) window.ekiShell = { canPickFolder: () => true,
  pickFolder: async () => { picks.push(1); return input.pick; } };
global.document = { createElement: () => mk(null), body: mk("body"), addEventListener() {} };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
const posted = [], gets = [], went = [];
global.nav = { go: (h) => went.push(h) };
global.fetch = async (url, opts) => {
  if (opts && opts.method === "POST") {
    posted.push([url, JSON.parse(opts.body)]);
    const d = input.answers[posted.length - 1];
    return { ok: true, json: async () => d };
  }
  gets.push(url);
  const hit = Object.keys(input.gets || {}).find((k) => url.startsWith(k));
  return { ok: true, json: async () => (hit ? input.gets[hit] : {}) };
};
for (const f of process.argv[2].split(",")) require(f);
const target = (data) => ({ closest: (s) => (s === "[data-" + Object.keys(data)[0] + "]" ? { dataset: data } : null) });
(async () => {
  const d = window.newProject.open();
  const q = (s) => d.querySelector(s);
  const out = { starts: !q("#np-starts").hidden };
  for (const [what, a, b] of input.steps || []) {
    if (what === "click") await Promise.all((q("form").l.click || []).map((f) => f({ target: target(a) })));
    if (what === "set") { q(a).value = b; await q(a).fire("input", {}); }
    if (what === "look") await window.newProject.look();
    if (what === "change") await window.newProject.change();
    if (what === "create") await q("form").fire("submit", { preventDefault() {} });
    if (what === "history") await q("#np-ask").fire("click", { target: target({ history: a }) });
    if (what === "ask") window.newProject.ask(a);
  }
  out.els = {};
  for (const s of input.show || []) {
    const e = q(s);
    out.els[s] = { value: e.value, text: e.textContent, html: e.innerHTML, hidden: e.hidden };
  }
  Object.assign(out, { posted, gets, went, picks: picks.length, closed: d.closed, html: d.innerHTML });
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""

SURVEY = {"state": "plain", "fit": True, "why": "",
          "offer": "eki keeps a history of this folder so several agents can work on it at once and any change can be undone.",
          "left_out": ["take1.wav", "renders/"], "ignore": ["*.wav", "renders/"]}
START = {"folder": "/Users/p/Projects/atlas", "base": "/Users/p/Projects", "recent": ["/Users/p/code/one", "/Users/p/two"],
         "exists": False, "project": None}


def run(tmp_path, data):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "sheet.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), SHEET, json.dumps(data)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_served_sheet_names_its_words_and_no_git(tmp_path):
    js = (WEB / "newproject.js").read_text()
    for name in ("Open a folder", "Start new", "Change…", "What should this become?",
                 "The first thing the project's agents read", "sort recycling against the clock",
                 "Already a project — open it", "Recent folders", "/api/projectstart?name=", "/api/projectstart?folder=",
                 "Keep a history", "Chat only", 'data-history="start"', 'data-history="none"',
                 "Kept out of the history (offered as the project's media pool)"):
        assert name in js, name
    assert "git init" not in js and "eki makes ~/name" not in js
    code = re.sub(r"^\s*//.*$", "", js, flags=re.M)                 # the header comment may name them
    for word in ("git", "commit", "branch", "worktree"):
        assert not re.search(rf"\b{word}\b", code, re.I), word


def test_it_opens_on_the_two_starts(tmp_path):
    got = run(tmp_path, {})
    assert got["starts"] and 'id="np-body" hidden' in got["html"] and 'id="np-create" hidden' in got["html"]
    assert "Open a folder" in got["html"] and "Start new" in got["html"]


def test_start_new_shows_the_folder_it_will_make_and_posts_the_name(tmp_path):
    got = run(tmp_path, {"gets": {"/api/projectstart?name=": START}, "answers": [{"id": "ab12", "folder": START["folder"]}],
                         "steps": [["click", {"start": "new"}], ["set", "#np-name", "atlas"], ["look"],
                                   ["set", "#np-desc", "A game"], ["create"]],
                         "show": ["#np-path", "#np-note", "#np-starts", "#np-new-at", "#np-open-at"]})
    els = got["els"]
    assert els["#np-starts"]["hidden"] and not els["#np-new-at"]["hidden"] and els["#np-open-at"]["hidden"]
    assert "/api/projectstart?name=atlas" in got["gets"]
    assert els["#np-path"]["text"] == "/Users/p/Projects/atlas" and els["#np-note"]["hidden"]
    assert got["posted"] == [["/api/projects", {"name": "atlas", "description": "A game", "lands": "here"}]]
    assert got["went"] == ["project/ab12"] and got["closed"]


def test_start_new_makes_nothing_before_create_and_warns_when_the_folder_is_there(tmp_path):
    got = run(tmp_path, {"gets": {"/api/projectstart?name=": dict(START, exists=True)},
                         "steps": [["click", {"start": "new"}], ["set", "#np-name", "atlas"], ["look"]],
                         "show": ["#np-note"]})
    assert got["posted"] == [] and not got["els"]["#np-note"]["hidden"]
    assert "is already there" in got["els"]["#np-note"]["html"]


def test_change_picks_another_parent(tmp_path):
    got = run(tmp_path, {"pick": "/Users/p/games", "answers": [{"id": "cd34"}],
                         "gets": {"/api/projectstart?folder=": dict(START, folder="/Users/p/games/atlas")},
                         "steps": [["click", {"start": "new"}], ["set", "#np-name", "atlas"], ["change"], ["create"]],
                         "show": ["#np-path"]})
    assert got["picks"] == 1
    assert "/api/projectstart?folder=%2FUsers%2Fp%2Fgames%2Fatlas" in got["gets"]
    assert got["els"]["#np-path"]["text"] == "/Users/p/games/atlas"
    assert got["posted"] == [["/api/projects", {"name": "atlas", "folder": "/Users/p/games/atlas", "lands": "here"}]]


def test_change_without_the_picker_shows_a_path_field(tmp_path):
    got = run(tmp_path, {"gets": {"/api/projectstart?": START},
                         "steps": [["click", {"start": "new"}], ["change"], ["set", "#np-parent", "/srv"],
                                   ["set", "#np-name", "x"], ["look"]],
                         "show": ["#np-parent-row"]})
    assert not got["els"]["#np-parent-row"]["hidden"]
    assert "/api/projectstart?folder=%2Fsrv%2Fx" in got["gets"]


def test_open_a_folder_uses_the_picker_at_once_in_the_shell(tmp_path):
    got = run(tmp_path, {"pick": "/Users/p/code/beat-box", "answers": [{"id": "ef56", "existing": False}],
                         "gets": {"/api/projectstart?folder=": dict(START, folder="/Users/p/code/beat-box", exists=True)},
                         "steps": [["click", {"start": "open"}], ["create"]],
                         "show": ["#np-folder", "#np-name", "#np-recent"]})
    assert got["picks"] == 1
    assert got["els"]["#np-folder"]["value"] == "/Users/p/code/beat-box" and got["els"]["#np-name"]["value"] == "beat-box"
    assert got["els"]["#np-recent"]["html"] == ""
    assert got["posted"] == [["/api/projects", {"name": "beat-box", "folder": "/Users/p/code/beat-box", "lands": "here"}]]
    assert got["went"] == ["project/ef56"]


def test_open_a_folder_cancelled_in_the_picker_stays_on_the_starts(tmp_path):
    got = run(tmp_path, {"pick": None, "steps": [["click", {"start": "open"}]], "show": ["#np-starts"]})
    assert got["picks"] == 1 and not got["els"]["#np-starts"]["hidden"] and got["posted"] == []


def test_open_a_folder_in_a_browser_offers_a_path_field_and_recent_folders(tmp_path):
    got = run(tmp_path, {"gets": {"/api/projectstart?folder=": START, "/api/folders": {"folders": ["/Users/p/code/one"]}},
                         "steps": [["click", {"start": "open"}], ["click", {"recent": "0"}]],
                         "show": ["#np-open-at", "#np-recent", "#np-folder", "#np-name", "#np-choose"]})
    els = got["els"]
    assert got["picks"] == 0 and not els["#np-open-at"]["hidden"]
    assert "Recent folders" in els["#np-recent"]["html"] and "/Users/p/two" in els["#np-recent"]["html"]
    assert 'data-recent="0"' in els["#np-recent"]["html"]
    assert els["#np-folder"]["value"] == "/Users/p/code/one" and els["#np-name"]["value"] == "one"
    assert 'id="np-choose"' not in got["html"]


def test_a_typed_folder_completes_and_names_the_project(tmp_path):
    got = run(tmp_path, {"gets": {"/api/projectstart?folder=": START, "/api/folders": {"folders": ["/Users/p/code/one"]}},
                         "steps": [["click", {"start": "open"}], ["set", "#np-folder", "/Users/p/code/o"]],
                         "show": ["#np-folders", "#np-name"]})
    assert "/api/folders?prefix=%2FUsers%2Fp%2Fcode%2Fo" in got["gets"]
    assert '<option value="/Users/p/code/one">' in got["els"]["#np-folders"]["html"]
    assert got["els"]["#np-name"]["value"] == "o"


def test_a_folder_that_is_a_project_opens_it(tmp_path):
    got = run(tmp_path, {"pick": "/Users/p/code/one",
                         "gets": {"/api/projectstart?folder=": dict(START, folder="/Users/p/code/one", exists=True, project="p1")},
                         "steps": [["click", {"start": "open"}], ["create"]],
                         "show": ["#np-note", "#np-create"]})
    assert "Already a project — open it" in got["els"]["#np-note"]["html"]
    assert got["els"]["#np-create"]["text"] == "Open it"
    assert got["posted"] == [] and got["went"] == ["project/p1"] and got["closed"]


def test_a_post_that_answers_existing_lands_on_that_project(tmp_path):
    got = run(tmp_path, {"answers": [{"id": "p1", "folder": "/x", "existing": True}],
                         "steps": [["click", {"start": "open"}], ["set", "#np-folder", "/x"], ["create"]]})
    assert got["posted"] == [["/api/projects", {"name": "x", "folder": "/x", "lands": "here"}]] and got["went"] == ["project/p1"]


def test_start_new_needs_a_name(tmp_path):
    got = run(tmp_path, {"steps": [["click", {"start": "new"}], ["create"]], "show": ["#np-err"]})
    assert got["posted"] == [] and got["els"]["#np-err"]["text"] == "Give it a name."


def test_a_bad_fit_offers_only_chat_only(tmp_path):
    why = "This folder is kept in sync by iCloud Drive, so eki works in it directly: one agent, no queue."
    got = run(tmp_path, {"steps": [["ask", dict(SURVEY, fit=False, offer=why, left_out=[])]], "show": ["#np-ask"]})
    box = got["els"]["#np-ask"]
    assert "Chat only" in box["html"] and "Keep a history" not in box["html"]
    assert why in box["html"] and "<details" not in box["html"] and not box["hidden"]


def test_the_offer_keeps_the_fields_and_keep_a_history_posts_start(tmp_path):
    got = run(tmp_path, {"answers": [{"ask": SURVEY}, {"id": "ab12", "folder": "/x", "left_out": []}],
                         "steps": [["click", {"start": "open"}], ["set", "#np-name", "Atlas"],
                                   ["set", "#np-folder", "~/Music/atlas"], ["create"], ["history", "start"]]})
    assert got["posted"] == [["/api/projects", {"name": "Atlas", "folder": "~/Music/atlas", "lands": "here"}],
                             ["/api/projects", {"name": "Atlas", "folder": "~/Music/atlas", "lands": "here", "history": "start"}]]
    assert got["went"] == ["project/ab12"] and got["closed"]


def test_the_offer_shows_what_is_kept_out_folded(tmp_path):
    got = run(tmp_path, {"steps": [["ask", SURVEY]], "show": ["#np-ask", "#np-err"]})
    html = got["els"]["#np-ask"]["html"]
    assert SURVEY["offer"] in html and "Keep a history" in html and "Chat only" in html
    assert "<details" in html and "<details open" not in html
    assert "<li>take1.wav</li>" in html and html.index("<details") < html.index("take1.wav")
    assert got["els"]["#np-err"]["text"] == ""


def test_chat_only_posts_none(tmp_path):
    got = run(tmp_path, {"answers": [{"ask": SURVEY}, {"id": "cd34"}],
                         "steps": [["click", {"start": "new"}], ["set", "#np-name", "Atlas"], ["create"], ["history", "none"]]})
    assert got["posted"][1][1] == {"name": "Atlas", "lands": "here", "history": "none"} and got["went"] == ["project/cd34"]
