"""The menu on a chat in the sidebar (threadmenu.js): what it binds and sends, and that it loads."""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
SRC = (WEB / "threadmenu.js").read_text()


def test_binds_the_menu_and_its_keys():
    assert '"contextmenu"' in SRC and ".tmore" in SRC
    for key, act in {"c": "copy", "s": "session", "p": "pin", "u": "unread", "r": "rename",
                     "a": "archive", "d": "delete"}.items():
        assert f'{key}: "{act}"' in SRC
        assert f'"{key.upper()}"' in SRC          # shown on the right while the menu is open
    assert '"Escape"' in SRC


def test_copies_and_writes():
    assert "navigator.clipboard.writeText" in SRC and 'execCommand("copy")' in SRC
    assert '"X-Eki"' in SRC and '"DELETE"' in SRC
    assert 'confirm("Delete this conversation? Files it changed stay.")' in SRC
    assert "eki's own thread: drop its work from the Station" in SRC
    assert "side.loadThreads()" in SRC


def test_styles_and_size():
    css = (WEB / "chat.css").read_text()
    for sel in (".tmenu", ".tmsub", ".tmitem kbd", ".tmitem.red", ".tmitem[disabled]"):
        assert sel in css
    assert len(SRC.splitlines()) <= 400


STUB = """
const handlers = {};
const el = () => ({ innerHTML: "", style: {}, dataset: {}, classList: { add() {}, remove() {} }, appendChild() {},
  remove() {}, setAttribute() {}, querySelectorAll: () => [], contains: () => false, offsetWidth: 200, offsetHeight: 200 });
global.window = { addEventListener() {}, innerWidth: 800, innerHeight: 600 };
global.document = { readyState: "complete", getElementById: () => null, createElement: el, body: el(),
  addEventListener(k, f) { handlers[k] = f; } };
global.fetch = () => new Promise(() => {});
global.md = { esc: (s) => String(s) };
window.side = { list: () => [{ id: "t1", title: "x", own: true, sessions: [{ kind: "claude_code", label: "Claude Code", id: "s1" }] }],
  loadThreads: () => Promise.resolve() };
require(process.argv[2]);
let prevented = false;
const th = { dataset: { id: "t1" } };
handlers.contextmenu({ target: { closest: () => th }, clientX: 10, clientY: 10, preventDefault() { prevented = true; } });
if (!prevented || typeof window.threadmenu.open !== "function") process.exit(1);
handlers.keydown({ key: "Escape", preventDefault() {} });
"""


def test_loads_under_a_stub(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "threadmenu.js")], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
