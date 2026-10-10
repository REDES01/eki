"""The Mac shell zooms the page with WKWebView.pageZoom (View → Zoom In/Out/Actual Size),
and opens a folder panel for the page through window.ekiShell (eki/web/shell.js)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parent.parent / "mac" / "main.swift"
SHELL_JS = Path(__file__).resolve().parent.parent / "eki" / "web" / "shell.js"


def src():
    return MAIN.read_text()


def test_view_menu_has_zoom_items():
    s = src()
    assert re.search(r'item\("Actual Size", #selector\(\w+\), "0"\)', s)
    assert re.search(r'item\("Zoom In", #selector\(zoomIn\), "\+"\)', s)
    assert re.search(r'item\("Zoom Out", #selector\(zoomOut\), "-"\)', s)
    assert re.search(r'item\("Zoom In", #selector\(zoomIn\), "="\)', s)
    assert "isHidden = true" in s and "allowsKeyEquivalentWhenHidden = true" in s
    assert "func validateMenuItem(" in s


def test_zoom_is_page_zoom_and_remembered():
    s = src()
    assert '"eki.zoom"' in s
    assert "pageZoom" in s
    steps = re.search(r"zoomSteps: \[Double\] = \[([^\]]*)\]", s).group(1)
    nums = [float(x) for x in steps.split(",")]
    assert nums[0] == 0.75 and nums[-1] == 2.0 and nums == sorted(nums) and 1.0 in nums
    assert "setProperty('--eki-zoom'" in s


def test_zoom_applied_before_first_load():
    s = src()
    launch = s[s.index("func applicationDidFinishLaunching"):s.index("func debug(")]
    assert launch.index("pageZoom") < launch.index("load()")


def test_drag_area_divides_by_page_zoom():
    s = src()
    body = s[s.index("func inDragArea"):s.index("func onWindowButton")]
    assert "pageZoom" in body


def test_pick_folder_opens_a_panel_and_answers_through_ekishell():
    s = src()
    body = s[s.index("func pickFolder("):]
    body = body[:body.index("\n    }\n")]
    assert "NSOpenPanel()" in body
    assert "canChooseDirectories = true" in body
    assert "canChooseFiles = false" in body
    assert "canCreateDirectories = true" in body
    assert "beginSheetModal(for: window)" in body
    assert "JSONSerialization.data" in body
    assert "window.ekiShell.picked(" in body


def test_drag_rects_change_only_on_a_drag_message():
    s = src()
    body = s[s.index("func userContentController"):s.index("func pickFolder(")]
    assert 'body["pickFolder"]' in body
    guard = body.index('guard body["drag"] != nil else { return }')
    assert guard < body.index('dragRects = rects("drag")')
    assert guard < body.index('noDragRects = rects("nodrag")')


def test_shell_js_detects_the_user_agent_the_shell_sets():
    # canPickFolder() looks for the name the shell adds to WKWebView's user agent
    name = re.search(r'applicationNameForUserAgent = "(\w+)"', src()).group(1)
    assert name == "EkiMac"
    assert "/" + name + "/.test(" in SHELL_JS.read_text()


NODE_STUB = r"""
const fs = require("fs");
const posted = [];
global.window = {};
window.navigator = {userAgent: process.argv[3] || ""};
if (process.argv[4] === "webkit") {
  window.webkit = {messageHandlers: {eki: {postMessage: (m) => posted.push(m)}}};
}
if (process.argv[5]) window.ekiShell = {commit: process.argv[5]};
eval(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  const s = window.ekiShell;
  const out = {can: s.canPickFolder()};
  if (s.commit) out.commit = s.commit;
  const p = s.pickFolder();
  if (posted.length) {
    s.picked("not-this-one", "/nope");
    s.picked(posted[0].pickFolder, "/Users/me/work");
  }
  out.path = await p;
  out.posted = posted;
  console.log(JSON.stringify(out));
})();
"""


def run_shell_js(tmp_path, ua, webkit, commit=""):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(NODE_STUB)
    r = subprocess.run(["node", str(stub), str(SHELL_JS), ua, "webkit" if webkit else "", commit],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_shell_js_without_webkit_cannot_pick(tmp_path):
    out = run_shell_js(tmp_path, "Mozilla/5.0", webkit=False)
    assert out == {"can": False, "path": None, "posted": []}
    # a webkit handler in some other browser isn't the Mac shell
    assert run_shell_js(tmp_path, "Mozilla/5.0", webkit=True)["can"] is False


def test_shell_js_posts_pick_folder_and_resolves_with_path(tmp_path):
    out = run_shell_js(tmp_path, "Mozilla/5.0 EkiMac", webkit=True)
    assert out["can"] is True
    assert len(out["posted"]) == 1
    assert set(out["posted"][0]) == {"pickFolder"}
    assert isinstance(out["posted"][0]["pickFolder"], str)
    assert out["path"] == "/Users/me/work"


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="swiftc is not installed")
def test_shell_typechecks(tmp_path):
    r = subprocess.run(["swiftc", "-typecheck", str(MAIN)], capture_output=True, text=True,
                       cwd=tmp_path, timeout=300)
    assert r.returncode == 0, r.stderr


def test_shell_hands_its_commit_to_the_page():
    s = src()
    launch = s[s.index("func applicationDidFinishLaunching"):s.index("func debug(")]
    assert '"EkiCommit"' in launch
    assert "window.ekiShell = {commit:" in launch
    assert "WKUserScript(" in launch and "atDocumentStart" in launch
    assert "JSONEncoder().encode(sha)" in launch      # the sha goes in JSON-escaped


def test_restart_message_handled_before_drag_rects():
    s = src()
    body = s[s.index("func userContentController"):s.index("// Restart on the page's Restart")]
    assert 'body["restart"]' in body
    assert body.index('body["restart"]') < body.index("dragRects")
    assert "return }" in body[body.index('body["restart"]'):body.index("dragRects")]


def test_restart_relaunches_after_exit_on_the_same_page():
    s = src()
    body = s[s.index("func restart("):s.index("// The menu bar item")]
    assert "kill -0" in body                           # waits for this pid to exit
    assert '"/bin/sh"' in body and "-c" in body
    assert "open \\(q(Bundle.main.bundlePath)) --args --eki-url \\(q(href))" in body
    assert "waitUntilExit" not in body                 # detached
    assert "NSApp.terminate" in body
    assert body.index("p.run()") < body.index("NSApp.terminate")


def test_eki_url_used_only_when_it_is_an_eki_page():
    s = src()
    fn = s[s.index("func launchURL()"):s.index("final class Shell")]
    assert '"--eki-url"' in fn
    assert "hasPrefix(home.absoluteString)" in fn
    assert "return home" in fn
    assert "web.load(URLRequest(url: startURL" in s
    assert "startURL = home" in s                      # later loads are home again


def test_shell_js_keeps_the_commit_the_shell_injected(tmp_path):
    # main.swift puts {commit} on window.ekiShell before the page runs; shellbar.js reads it
    assert 'window.ekiShell = {commit:' in src()
    out = run_shell_js(tmp_path, "Mozilla/5.0 EkiMac", webkit=True, commit="abc123")
    assert out["commit"] == "abc123" and out["path"] == "/Users/me/work"
