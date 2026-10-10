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
