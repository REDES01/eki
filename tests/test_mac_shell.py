"""The Mac shell zooms the page with WKWebView.pageZoom (View → Zoom In/Out/Actual Size)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parent.parent / "mac" / "main.swift"


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
