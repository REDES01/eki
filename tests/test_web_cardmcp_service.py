"""A service card with an "mcp" field in the open card: cardmcp.js registers a part for the service kind
that draws "script: …" (run, or call on its serve, or provider) and "MCP: …" (command and args, or url)
with env keys as they reach the page (masked) and the Test button; "mcp" is kept out of the form. A
service card without "mcp" draws as before: the part hides itself and its title."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web_cardworkflow import PANEL_STUB

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

STUB = r"""
const fs = require("fs");
const [, , part, args] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}) };
eval(fs.readFileSync(part, "utf8"));
const head = { hidden: false, classList: { contains: (c) => c === "pp-h" } };
const box = { innerHTML: "", dataset: {}, hidden: false, previousElementSibling: head, addEventListener() {} };
const card = JSON.parse(args);
const k = window.projectCardKinds[card.kind];
k.draw(box, card, "p1", "", { save() {}, problems: {}, editable: true });
process.stdout.write(JSON.stringify({ html: box.innerHTML, hidden: box.hidden, headHidden: head.hidden,
  title: k.title, keys: k.keys || null }));
k.close();
"""

MCP = {"command": "uvx", "args": ["ableton-mcp"], "env": {"TOKEN": "••••23"}}


def service(**spec):
    return {"name": "ableton", "kind": "service", "editable": True, "spec": {"kind": "service", **spec}}


def run(tmp_path, c):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "cardmcp.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_a_service_with_mcp_shows_the_script_the_mcp_and_test(tmp_path):
    got = run(tmp_path, service(run=["node", "tools/music/auto.mjs", "--all-new"], mcp=MCP))
    html = got["html"]
    assert '<span class="dim">script:</span> <code>node tools/music/auto.mjs --all-new</code>' in html
    assert '<span class="dim">MCP:</span> <code>uvx ableton-mcp</code>' in html
    assert "<code>TOKEN=••••23</code>" in html and "data-cm-test" in html and ">Test</button>" in html
    assert not got["hidden"] and not got["headHidden"]
    assert got["title"] == "Script and MCP" and got["keys"] == ["mcp"]


@pytest.mark.parametrize("spec, want", [
    ({"serve": ["srv", "--port", "9"], "call": ["ask"]},
     '<code>ask</code> <span class="dim">on the server</span> <code>srv --port 9</code>'),
    ({"provider": "comfyui"}, '<span class="dim">eki\'s</span> <code>comfyui</code>'),
    ({"run": "tts --out x.wav"}, "<code>tts --out x.wav</code>"),
])
def test_the_script_line_for_each_form(tmp_path, spec, want):
    html = run(tmp_path, service(mcp={"url": "http://127.0.0.1:9/mcp"}, **spec))["html"]
    assert want in html and '<span class="dim">MCP:</span> <code>http://127.0.0.1:9/mcp</code>' in html


def test_a_service_without_mcp_has_no_section(tmp_path):
    got = run(tmp_path, service(run=["tts"]))
    assert got["html"] == "" and got["hidden"] and got["headHidden"]


def test_the_mcp_card_draws_as_before(tmp_path):
    got = run(tmp_path, {"name": "dice", "kind": "mcp", "spec": {"kind": "mcp", "server": MCP}})
    assert '<span class="dim">command</span> <code>uvx ableton-mcp</code>' in got["html"]
    assert "script:" not in got["html"] and got["title"] == "Server" and got["keys"] is None
    assert not got["hidden"]


def panel(tmp_path, c):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "panel.js"
    stub.write_text(PANEL_STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "cardmcp.js"), str(WEB / "projectcardpanel.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_in_the_panel_mcp_is_drawn_by_the_part_and_kept_out_of_the_form(tmp_path):
    out = panel(tmp_path, service(run=["node", "auto.mjs"], mcp=MCP, about="Live"))
    assert '<h4 class="pp-h">Script and MCP</h4>' in out["frame"] and '<h4 class="pp-h">Run' in out["frame"]
    assert "MCP:" in out["kind"] and "Test</button>" in out["kind"]
    assert 'data-key="mcp"' not in out["settings"] and 'data-key="run"' in out["settings"]


def test_in_the_panel_a_service_without_mcp_keeps_its_form(tmp_path):
    out = panel(tmp_path, service(run=["tts"]))
    assert out["kind"] == "" and 'data-key="run"' in out["settings"]
