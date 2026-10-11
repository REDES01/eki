"""Paths in tool lines and on cards open in the side panel: app.js runs a tool line's detail through
md.linkPaths (only when md.js has it), and the knowledge and instructions cards carry data-file
rows with the file's absolute path, which panel.js's document click opens in the side panel."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web import get, web  # noqa: F401  (the served window)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def test_tool_lines_link_paths_behind_a_guard(web):  # noqa: F811
    code, js = get(web + "/ui/app.js")
    assert code == 200
    line = js[js.index("function stepLine"):js.index("function mark")]
    assert 'typeof md.linkPaths === "function"' in line and "md.linkPaths(detail)" in line
    assert 'data-file="${md.esc(p)}"' in js                      # the files under an answer stay


def test_cards_render_data_file_rows(web):  # noqa: F811
    for f in ("cardknowledge.js", "projectinstructions.js"):
        code, js = get(web + "/ui/" + f)
        assert code == 200
        assert "data-file=" in js and "Open beside the chat" not in js, f
        assert 't.closest("[data-file]")' in js, f               # the card's own row click stays out


def test_project_files_already_open_in_the_panel(web):  # noqa: F811
    assert 'data-file="${esc(f)}"' in get(web + "/ui/project.js")[1]


RUN = r"""
const fs = require("fs");
const [, , file, card] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const box = { innerHTML: "", dataset: {}, addEventListener() {} };
const parts = {};
box.querySelector = (sel) => sel === ".ck" ? (box.innerHTML ? {} : null) : (parts[sel] = parts[sel] || { innerHTML: "" });
global.window = global; global.md = { esc, render: (t) => t };
global.document = { querySelector: () => ({}) };
global.fetch = async () => ({ ok: true, json: async () => ({ files: [
  { path: "design/DECISIONS.md", size: 10, changed: 0, author: "you", missing: false, pattern: "design/*.md" },
  { path: "gone.md", missing: true, pattern: "gone.md" }] }) });
eval(fs.readFileSync(file, "utf8"));
projectCardKinds.knowledge.draw(box, JSON.parse(card), "p1", "");
setTimeout(() => console.log(parts[".ck-list"].innerHTML), 20);
"""


@pytest.mark.parametrize("card,want", [
    ({"name": "notes", "card": "/Users/x/proj/components/notes"}, "/Users/x/proj/design/DECISIONS.md"),
    ({"name": "notes", "card": "/Users/x/proj/components/notes", "ref": "notes"}, "/Users/x/proj/design/DECISIONS.md"),
    ({"name": "notes", "card": ""}, None),
])
def test_a_knowledge_row_carries_its_absolute_path(tmp_path, card, want):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = tmp_path / "run.js"
    script.write_text(RUN)
    out = subprocess.run(["node", str(script), str(WEB / "cardknowledge.js"), json.dumps(card)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    html = out.stdout
    assert "design/DECISIONS.md" in html
    if want:
        assert f'data-file="{want}"' in html
        assert html.count("data-file=") == 1                    # a missing file opens in the card
    else:
        assert "data-file" not in html                          # no project folder known: read here


def test_small():
    for f in ("app.js", "cardknowledge.js", "projectinstructions.js"):
        assert len((WEB / f).read_text().splitlines()) <= 400, f
