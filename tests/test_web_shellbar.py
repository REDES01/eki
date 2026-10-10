"""The bar that offers to restart the Mac window when eki has rebuilt Eki.app."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "shellbar.js").read_text()
CODE = "\n".join(l for l in JS.splitlines() if not l.lstrip().startswith("//"))


def test_index_loads_it_after_nav():
    html = (WEB / "index.html").read_text()
    assert '<script src="/ui/shellbar.js"></script>' in html
    assert html.index("/ui/nav.js") < html.index("/ui/shellbar.js")


def test_text_and_restart_message():
    assert "eki's window has an update" in JS
    assert "Restart" in JS
    assert "postMessage({ restart: location.href })" in JS


def test_only_in_the_mac_shell():
    assert 'dataset.shell !== "mac"' in JS
    assert "window.ekiShell" in JS
    assert "/api/shell" in JS


def _callbacks(src, timer):
    """The source of each callback handed to setTimeout/setInterval, as far as text allows:
    a named function's body, or the inline arrow/function up to its closing paren."""
    out = []
    for m in re.finditer(timer + r"\(\s*([A-Za-z_$][\w$]*)\s*,", src):
        body = re.search(r"function " + re.escape(m.group(1)) + r"\s*\([^)]*\)\s*\{(.*?)\n  \}", src, re.S)
        out.append(body.group(1) if body else m.group(1))
    for m in re.finditer(timer + r"\(\s*(\(|function)", src):
        out.append(src[m.start():m.start() + 400])
    return out


def test_no_timer_leads_to_a_restart():
    assert "setTimeout" not in CODE
    assert CODE.count("setInterval") == 1
    cbs = _callbacks(CODE, "setInterval")
    assert cbs and all("postMessage" not in c and "restart" not in c for c in cbs)
    # restart() is only wired to the button's click
    calls = [m.start() for m in re.finditer(r"\brestart\b(?!:)", CODE)]
    uses = [CODE[i - 40:i + 10] for i in calls if not CODE[i - 9:i].endswith("function ")]
    assert uses and all('addEventListener("click", restart' in u for u in uses)


# Run the script against a stub page: the bar shows only when the installed app differs.
STUB = """
const made = [];
const el = () => { const e = { hidden: false, innerHTML: "", id: "", listeners: {},
  querySelector() { return e.btn || (e.btn = { addEventListener(k, f) { e.listeners[k] = f; } }); } }; made.push(e); return e; };
const sent = [];
const ticks = [];
global.location = { href: "http://127.0.0.1/ui/#station" };
global.window = { ekiShell: JSON.parse(process.argv[4]), addEventListener() {},
  webkit: { messageHandlers: { eki: { postMessage: (m) => sent.push(m) } } } };
global.document = { documentElement: { dataset: { shell: process.argv[5] } }, createElement: el,
  body: { appendChild() {} } };
global.setInterval = (f, ms) => ticks.push(ms);
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(process.argv[3])) });
require(process.argv[2]);
setImmediate(() => setImmediate(() => {
  const bar = made[0];
  if (bar && !bar.hidden) bar.listeners.click();
  process.stdout.write(JSON.stringify({ made: made.length, shown: !!bar && !bar.hidden, sent, ticks }));
}));
"""


def run(tmp_path, api, shell={"commit": "aaa"}, mode="mac"):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "shellbar.js"), json.dumps(api), json.dumps(shell), mode],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    return json.loads(out)


def test_shows_and_restarts_on_click(tmp_path):
    r = run(tmp_path, {"installed": "bbb", "state": "installed"})
    assert r["shown"] and r["ticks"] == [60000]
    assert r["sent"] == [{"restart": "http://127.0.0.1/ui/#station"}]


def test_hidden_when_same_building_or_none(tmp_path):
    for api in ({"installed": "aaa", "state": "installed"}, {"installed": "bbb", "state": "building"},
                {"installed": None, "state": None}):
        r = run(tmp_path, api)
        assert not r["shown"] and r["sent"] == []


def test_nothing_outside_the_mac_shell(tmp_path):
    assert run(tmp_path, {"installed": "bbb"}, mode="web") == {"made": 0, "shown": False, "sent": [], "ticks": []}
    assert run(tmp_path, {"installed": "bbb"}, shell=None)["ticks"] == []
