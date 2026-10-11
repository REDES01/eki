"""The knowledge card's Documents part (cardknowledge.js + cardknowledgedocs.js) drawn in node with a stub
page: each document's head (name, a choicemenu.js read dropdown explaining each choice, about, ×), its file
rows, "+ document"; a read-only card draws none of the editing; × and a picked read save the whole list with
files and read removed (an old card turns into the docs shape)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

STUB = r"""
const fs = require("fs");
const [, , web, card, editable, act] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const parts = {};
const box = { innerHTML: "", dataset: {}, addEventListener() {} };
box.querySelector = (sel) => sel === ".ck" ? (box.innerHTML ? {} : null) : (parts[sel] = parts[sel] || { innerHTML: "" });
global.window = global; global.md = { esc, render: (t) => t };
global.document = { querySelector: () => ({}) };
global.confirm = () => true;
const docs = [
  { name: "decisions", about: "what we decided", read: "always", size: 12, changed: 0,
    files: [{ path: "design/DECISIONS.md", pattern: "design/DECISIONS.md", size: 12, changed: 0, missing: false }] },
  { name: "notes", about: "loose notes", read: "when-relevant", size: 0, changed: null,
    patterns: ["notes/*.md", "gone.md", "drafts/*.md"],
    files: [{ path: "notes/a.md", pattern: "notes/*.md", size: 2, changed: 0, missing: false },
            { path: "notes/b.md", pattern: "notes/*.md", size: 2, changed: 0, missing: false },
            { path: "gone.md", pattern: "gone.md", missing: true }] }];
global.fetch = async () => ({ ok: true, json: async () => ({ docs, files: docs.flatMap((d) => d.files) }) });
for (const f of ["choicemenu.js", "cardknowledgedocs.js", "cardknowledge.js"]) eval(fs.readFileSync(web + "/" + f, "utf8"));
const saved = [];
const ctx = { editable: editable === "1", problems: { "": "document notes: files must stay inside the project" },
              save: async (set) => { saved.push(set); } };
projectCardKinds.knowledge.draw(box, JSON.parse(card), "p1", "", ctx);
setTimeout(async () => {
  const h = { esc, size: String, ago: String, card: JSON.parse(card), docs, editable: ctx.editable,
              problems: {}, save: ctx.save };
  if (act === "remove") await cardKnowledgeDocs.act({ dataset: { ckd: "remove", i: "0" } }, box, h, () => {});
  if (act === "add") await cardKnowledgeDocs.act({ dataset: { ckd: "add" } }, box, h, () => {});
  console.log(JSON.stringify({ list: parts[".ck-list"].innerHTML, add: parts[".ck-add"].innerHTML,
    adder: cardKnowledgeDocs.adder(h), saved, kind: { title: projectCardKinds.knowledge.title, keys: projectCardKinds.knowledge.keys } }));
}, 30);
"""

CARD = {"name": "knowledge", "card": "/Users/x/proj/components/knowledge", "about": "the shelf",
        "choices": {"read": ["always", "when-relevant"]},
        "choice_help": {"read": {"always": "Every run reads it first.", "when-relevant": "The planner picks it."}}}


def draw(tmp_path, card=CARD, editable=True, act=""):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB), json.dumps(card), "1" if editable else "0", act],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_documents_render_with_read_dropdown_and_remove(tmp_path):
    got = draw(tmp_path)
    assert got["kind"] == {"title": "Documents", "keys": ["docs", "files", "read"]}
    html = got["list"]
    assert html.count('class="ck-doc"') == 2
    assert "decisions" in html and "what we decided" in html and "loose notes" in html
    assert 'data-key="ckread:0"' in html and 'data-key="ckread:1"' in html and "cm-btn" in html
    assert "Every run reads it first." in html and "The planner picks it." in html
    assert html.count('data-ckd="remove"') == 2
    assert 'data-file="/Users/x/proj/notes/b.md"' in html and 'data-ck-path="gone.md"' in html
    assert "files must stay inside the project" in html.split('class="ck-doc"')[2]   # beside notes
    assert 'data-ckd="add"' in got["add"] and "+ document" in got["add"]


def test_a_pool_card_is_read_only(tmp_path):
    got = draw(tmp_path, {**CARD, "ref": "knowledge"})
    assert 'data-ckd="remove"' not in got["list"] and got["add"] == ""
    assert 'data-key="ckread:0"' in got["list"] and "disabled" in got["list"]
    got = draw(tmp_path, editable=False)
    assert 'data-ckd="remove"' not in got["list"] and got["add"] == ""


def test_remove_saves_the_list_without_it_and_drops_the_old_keys(tmp_path):
    # the saved patterns are the stored ones, so drafts/*.md (matching nothing, no file entry) stays
    got = draw(tmp_path, act="remove")
    assert got["saved"] == [{"docs": [{"name": "notes", "files": ["notes/*.md", "gone.md", "drafts/*.md"], "about": "loose notes",
                                       "read": "when-relevant"}], "files": None, "read": None}]


def test_add_opens_the_form(tmp_path):
    form = draw(tmp_path, act="add")["adder"]
    for part in ("ck-add-name", "ck-add-files", "ck-add-about", 'data-key="ckread:new"', 'data-ckd="addsave"',
                 'data-ckd="addcancel"'):
        assert part in form, part


def test_small_and_parses():
    for f in ("cardknowledge.js", "cardknowledgedocs.js"):
        assert len((WEB / f).read_text().splitlines()) < 400, f
        if shutil.which("node"):
            out = subprocess.run(["node", "--check", str(WEB / f)], capture_output=True, text=True, timeout=20)
            assert out.returncode == 0, out.stderr
