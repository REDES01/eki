"""The "Create a project" sheet (newproject.js) and a folder eki keeps no history of yet: the API answers
{ask: {...}} and the sheet shows the offer in place of the error line, with "Keep a history" (history "start")
and "Chat only" (history "none"); a folder where a history is a bad fit gets only "Chat only". What stays out is
in a closed details fold, and outside it the sheet says nothing of git."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough of a page: one element per selector, listeners remembered, POST answers taken from the input in turn.
STUB = r"""
const mk = (id) => ({ id, value: "", innerHTML: "", textContent: "", hidden: false, disabled: false, l: {}, sel: {},
  opened: false, closed: false,
  addEventListener(t, f) { this.l[t] = f; }, appendChild() {}, focus() {},
  querySelector(s) { return this.sel[s] || (this.sel[s] = mk(s)); }, querySelectorAll: () => [],
  showModal() { this.opened = true; }, close() { this.closed = true; } });
global.window = { addEventListener() {} };
global.document = { createElement: () => mk(null), body: mk("body"), addEventListener() {} };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
const input = JSON.parse(process.argv[3]);
const posted = [], went = [];
global.nav = { go: (h) => went.push(h) };
global.fetch = async (url, opts) => {
  posted.push([url, JSON.parse(opts.body)]);
  const d = input.answers[posted.length - 1];
  return { ok: true, json: async () => d };
};
require(process.argv[2]);
const btn = (h) => ({ closest: () => ({ dataset: { history: h } }) });
(async () => {
  const d = window.newProject.open();
  d.querySelector("#np-name").value = "Atlas"; d.querySelector("#np-folder").value = "~/Music/atlas";
  const out = {};
  if (input.do === "ask") window.newProject.ask(input.survey);
  if (input.do === "flow") {
    await d.querySelector("form").l.submit({ preventDefault() {} });
    out.asked = !d.closed && d.querySelector("#np-create").hidden;
    await d.querySelector("#np-ask").l.click({ target: btn(input.pick) });
  }
  const box = d.querySelector("#np-ask");
  Object.assign(out, { html: box.innerHTML, hidden: box.hidden, err: d.querySelector("#np-err").textContent,
                       posted, went, closed: d.closed });
  console.log(JSON.stringify(out));
})();
"""

SURVEY = {"state": "plain", "fit": True, "why": "",
          "offer": "eki keeps a history of this folder so several agents can work on it at once and any change can be undone.",
          "left_out": ["take1.wav", "renders/"], "ignore": ["*.wav", "renders/"]}


def test_the_served_sheet_names_both_choices_and_no_git_init():
    js = (WEB / "newproject.js").read_text()
    for name in ("Keep a history", "Chat only", 'data-history="start"', 'data-history="none"',
                 "Kept out of the history (offered as the project's media pool)", "Optional — eki makes ~/name"):
        assert name in js, name
    assert "git init" not in js
    code = re.sub(r"^\s*//.*$", "", js, flags=re.M)                 # the header comment may name them
    for word in ("git", "commit", "branch", "worktree"):
        assert not re.search(rf"\b{word}\b", code, re.I), word


def run(tmp_path, data):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "sheet.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "newproject.js"), json.dumps(data)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_bad_fit_offers_only_chat_only(tmp_path):
    why = "This folder is kept in sync by iCloud Drive, so eki works in it directly: one agent, no queue."
    got = run(tmp_path, {"do": "ask", "survey": dict(SURVEY, fit=False, offer=why, left_out=[])})
    assert "Chat only" in got["html"] and "Keep a history" not in got["html"]
    assert why in got["html"] and "<details" not in got["html"] and not got["hidden"]


def test_the_offer_keeps_the_fields_and_keep_a_history_posts_start(tmp_path):
    got = run(tmp_path, {"do": "flow", "pick": "start", "answers": [{"ask": SURVEY},
                                                                 {"id": "ab12", "folder": "/x", "left_out": []}]})
    assert got["asked"]
    assert got["posted"] == [["/api/projects", {"name": "Atlas", "folder": "~/Music/atlas"}],
                             ["/api/projects", {"name": "Atlas", "folder": "~/Music/atlas", "history": "start"}]]
    assert got["went"] == ["project/ab12"] and got["closed"]


def test_the_offer_shows_what_is_kept_out_folded(tmp_path):
    got = run(tmp_path, {"do": "ask", "survey": SURVEY})
    html = got["html"]
    assert SURVEY["offer"] in html and "Keep a history" in html and "Chat only" in html
    assert "<details" in html and "<details open" not in html
    assert "<li>take1.wav</li>" in html and html.index("<details") < html.index("take1.wav")
    assert got["err"] == ""


def test_chat_only_posts_none(tmp_path):
    got = run(tmp_path, {"do": "flow", "pick": "none", "answers": [{"ask": SURVEY}, {"id": "cd34"}]})
    assert got["posted"][1][1]["history"] == "none" and got["went"] == ["project/cd34"]
