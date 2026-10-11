"""The doc viewer in the side panel (docview.js via panel.js): rendered, outline, last change, live refresh."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# docview.js + panel.js need a page; this stub is just enough to load them, show a path and see what they did.
STUB = """
const els = {};
const mk = (id) => ({ id, innerHTML: "", textContent: "", href: "", hidden: true, dataset: {}, listeners: {},
  scrollTop: 0, scrollHeight: 0, clientHeight: 0, classList: { add() {}, remove() {} },
  addEventListener(ev, fn) { this.listeners[ev] = fn; },
  querySelector(sel) { return sel === ".doc-edit" && this.innerHTML.includes("doc-edit") ? (els.__edit = mk("edit")) : null; } });
const el = (id) => els[id] || (els[id] = mk(id));
const out = { fetched: [], events: [], editor: null };
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } };
global.Event = class { constructor(type) { this.type = type; } };
global.window = global;
global.addEventListener = () => {};
global.dispatchEvent = (e) => { if (e.type === "eki:doc") out.events.push(e.detail); };
global.document = { getElementById: el, addEventListener() {}, hidden: false };
let tick = null;
global.setInterval = (f) => { tick = f; return 1; }; global.clearInterval = () => { tick = null; };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
  render: (t, o) => "<md base=" + (o && o.base) + ">" + t + "</md>",
  outline: (t) => t.split("\\n").filter((l) => /^#+ /.test(l)).map((l) => ({ level: l.match(/^#+/)[0].length,
    text: l.replace(/^#+ /, ""), id: "h-" + l.replace(/^#+ /, "").toLowerCase().replace(/ /g, "-") })) };
const [docs, steps, editor] = JSON.parse(process.argv[4]);
if (editor) global.doceditor = { open: (b, d) => { out.editor = d.hash; }, flush: async () => {} };
let n = 0;
global.fetch = async (url) => {
  out.fetched.push(url);
  if (url.startsWith("/api/doc")) { const d = docs[Math.min(n++, docs.length - 1)];
    return { ok: !d.error, json: async () => d }; }
  return { ok: true, text: async () => "plain" };
};
require(process.argv[2]); require(process.argv[3]);
(async () => {
  for (const s of steps) {
    if (s[0] === "show") await panel.show(s[1]);
    if (s[0] === "poll") await docview.poll();
    if (s[0] === "edit") els.__edit.listeners.click();
  }
  out.body = el("panel-body").innerHTML; out.name = el("panel-name").textContent;
  out.path = el("panel-path").textContent; out.hidden = el("panel").hidden; out.current = docview.current();
  process.stdout.write(JSON.stringify(out));
})();
"""

DOC = {"path": "/Users/me/notes/play.md", "name": "play.md", "text": "# Play notes\n\nfun\n\n## Bugs\n\n- one",
       "mtime": 0, "hash": "a1", "size": 30, "editable": True, "reason": "",
       "writer": {"by": "run", "run": "r-1", "t": 0}}


def run(tmp_path, docs, steps, editor=True):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "panel.js"), str(WEB / "docview.js"),
                          json.dumps([docs, steps, editor])], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_page_links_docs_css_and_the_scripts_after_the_panel():
    page = (WEB / "index.html").read_text()
    assert '<link rel="stylesheet" href="/ui/docs.css">' in page
    at = [page.index(f'<script src="/ui/{s}.js"></script>') for s in ("panel", "docview", "doceditor", "docopen")]
    assert at == sorted(at)


def test_a_markdown_path_is_fetched_and_rendered_with_outline_and_meta(tmp_path):
    got = run(tmp_path, [DOC], [["show", "/Users/me/notes/play.md"]])
    assert got["fetched"] == ["/api/doc?path=%2FUsers%2Fme%2Fnotes%2Fplay.md"]
    body = got["body"]
    assert "<md base=/Users/me/notes>" in body and "fun" in body and 'class="answer pad doc"' in body
    assert body.count("data-anchor=") == 2 and 'data-anchor="h-bugs"' in body and ">Play notes<" in body
    assert "by an agent run <code>r-1</code>" in body and "edited " in body
    assert "doc-edit" in body and ">Edit<" in body
    assert got["name"] == "play.md" and got["path"] == "~/notes/play.md" and got["hidden"] is False
    assert got["events"] == [{"path": "/Users/me/notes/play.md"}] and got["current"] == "/Users/me/notes/play.md"


def test_by_you_and_no_writer(tmp_path):
    you = dict(DOC, writer={"by": "you", "run": None, "t": 0})
    assert "by you" in run(tmp_path, [you], [["show", "/Users/me/a.MARKDOWN"]])["body"]
    body = run(tmp_path, [dict(DOC, writer=None)], [["show", "/Users/me/a.md"]])["body"]
    assert "edited " in body and " by " not in body


def test_read_only_shows_the_reason_not_edit(tmp_path):
    ro = dict(DOC, editable=False, reason="Too large to edit (over 2 MB).")
    body = run(tmp_path, [ro], [["show", "/Users/me/big.md"]])["body"]
    assert "Too large to edit" in body and "doc-edit" not in body


def test_no_editor_loaded_hides_edit(tmp_path):
    assert "doc-edit" not in run(tmp_path, [DOC], [["show", "/Users/me/a.md"]], editor=False)["body"]


def test_an_error_is_shown_in_the_body(tmp_path):
    got = run(tmp_path, [{"error": "Outside your home folder."}], [["show", "/etc/x.md"]])
    assert "Outside your home folder." in got["body"] and got["events"] == []


def test_a_picture_still_takes_the_old_path(tmp_path):
    got = run(tmp_path, [DOC], [["show", "/Users/me/a.png"]])
    assert got["fetched"] == [] and "<img" in got["body"] and "/api/file?path=" in got["body"]


def test_a_changed_hash_on_the_next_poll_draws_again(tmp_path):
    new = dict(DOC, text="# Play notes\n\nchanged by the agent", hash="b2")
    got = run(tmp_path, [DOC, DOC, new], [["show", "/Users/me/notes/play.md"], ["poll"], ["poll"]])
    assert len(got["fetched"]) == 3 and "changed by the agent" in got["body"]


def test_polling_stops_while_editing(tmp_path):
    new = dict(DOC, text="from disk", hash="b2")
    got = run(tmp_path, [DOC, new], [["show", "/Users/me/a.md"], ["edit"], ["poll"]])
    assert got["editor"] == "a1" and len(got["fetched"]) == 1 and "from disk" not in got["body"]


def test_files_stay_small():
    for f in ("docview.js", "panel.js", "docs.css"):
        assert len((WEB / f).read_text().splitlines()) <= 400
