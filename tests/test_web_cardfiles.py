"""A card's files open in the side panel: in a project card, cardknowledge.js's existing file rows and
projectinstructions.js's file and Read first rows are [data-file] rows with the absolute path, which
panel.js opens in the doc viewer with "← back to <card>". A missing file and a pool card stay in the card."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
KNOW = (WEB / "cardknowledge.js").read_text()
INSTR = (WEB / "projectinstructions.js").read_text()


def test_both_emit_data_file_for_project_files():
    assert 'data-file="${esc(panelFile(f))}"' in KNOW
    assert 'data-file="${esc(root() + "/" + rel)}"' in INSTR
    for js in (KNOW, INSTR):
        assert 't.closest("[data-file]")' in js              # the card's own click handler stays out
        assert "Open in panel" not in js and "Open beside the chat" not in js


def test_knowledge_has_no_inline_viewer_for_existing_project_files():
    assert "ck-beside" not in KNOW
    assert "!f.missing && root()" in KNOW                     # only those rows go to the panel
    assert "data-ck-path=" in KNOW                            # a missing file and a pool card's stay here
    assert "Edit to write it" in KNOW


def test_instructions_keeps_its_parts_and_drops_close():
    for name in ('"/instructions"', '"/link-claude"', '"/read?path="', "Read first", "Link CLAUDE.md", "Edit",
                 "metaKey", "ctrlKey", "preventDefault", '"focusout"', "md.render(", "pj-err",
                 '"unlinked"', "window.projectInstructions = { open, update, close }"):
        assert name in INSTR, name
    assert 'data-pi="close"' not in INSTR and "I.ctx.close()" not in INSTR
    assert "pi-beside" not in INSTR
    assert len(INSTR.splitlines()) < 250


def test_both_parse():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    for f in ("cardknowledge.js", "projectinstructions.js"):
        out = subprocess.run(["node", "--check", str(WEB / f)], capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr


# cardknowledge.js needs a page; this stub is just enough to draw the file list and read it back.
STUB = r"""
const fs = require("fs");
const [, , file, card] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const box = { innerHTML: "", dataset: {}, addEventListener() {} };
const parts = {};
box.querySelector = (sel) => sel === ".ck" ? (box.innerHTML ? {} : null) : (parts[sel] = parts[sel] || { innerHTML: "" });
global.window = global; global.md = { esc, render: (t) => t };
global.document = { querySelector: () => ({}) };
global.fetch = async () => ({ ok: true, json: async () => ({ files: [
  { path: "notes/plan.md", size: 10, changed: 0, author: "you", missing: false, pattern: "notes/*.md" },
  { path: "gone.md", missing: true, pattern: "gone.md" }] }) });
eval(fs.readFileSync(file, "utf8"));
projectCardKinds.knowledge.draw(box, JSON.parse(card), "p1", "");
setTimeout(() => console.log(parts[".ck-list"].innerHTML), 20);
"""


def rows(tmp_path, card):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "cardknowledge.js"), json.dumps(card)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    plan, gone = out.stdout.split('<div class="pj-item')[1:]
    return plan, gone


def test_an_existing_project_file_row_opens_in_the_panel(tmp_path):
    plan, gone = rows(tmp_path, {"name": "notes", "card": "/Users/x/proj/components/notes"})
    assert 'data-file="/Users/x/proj/notes/plan.md"' in plan and "data-ck-path" not in plan
    assert "data-file" not in gone and 'data-ck-path="gone.md"' in gone   # missing: written in the card


@pytest.mark.parametrize("path", ["/Users/x/.eki/components/notes", "/Users/x/.eki/pool/notes", ""])
def test_a_pool_card_keeps_its_files_in_the_card(tmp_path, path):
    plan, gone = rows(tmp_path, {"name": "notes", "card": path})
    assert "data-file" not in plan + gone
    assert 'data-ck-path="notes/plan.md"' in plan
