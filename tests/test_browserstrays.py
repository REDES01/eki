"""Browsers in eki worktrees that hold no browser slot are noted; helpers, slot holders and
browsers elsewhere are not."""
from eki import browserslots, browserstrays, browsershim, paths

SHELL = "/Users/x/Library/Caches/ms-playwright/chromium_headless_shell-1187/chrome-mac/headless_shell"


def _ps():
    shim = browsershim.mirror() / "chromium_headless_shell-1187/chrome-mac/headless_shell"
    return "\n".join([
        f"  101 {SHELL} --headless --remote-debugging-pipe",
        f"  102 {SHELL} --type=renderer --enable-webgl",
        f"  103 {SHELL} --headless",
        f"  104 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --flag",
        f"  105 {SHELL} --headless",
        f"  106 {shim} --headless",
        "  107 node tools/shot.mjs --chrome",
        "  108 /bin/zsh -c cd ~/chromium-notes",
    ])


def _lsof():
    work = paths.home() / "work"
    rows = {101: work / "abc", 102: work / "abc", 103: work / "def", 104: "/", 105: "/tmp/elsewhere",
            106: work / "abc", 107: work / "abc"}
    return "\n".join(f"p{pid}\nfcwd\nn{cwd}" for pid, cwd in rows.items())


def test_counts_strays_and_ignores_slot_holders_helpers_and_elsewhere(monkeypatch):
    monkeypatch.setattr(browserslots, "pids", lambda: {103})
    assert browserstrays.strays(_ps(), _lsof()) == [101]
    assert browserstrays.note(_ps(), _lsof()) == [
        "browsers: 1 open in eki worktrees outside the browser slots (pids 101) — "
        "a script with a hard-coded Playwright cache path"]


def test_two_strays_are_one_line(monkeypatch):
    monkeypatch.setattr(browserslots, "pids", lambda: set())
    line, = browserstrays.note(_ps(), _lsof())
    assert line.startswith("browsers: 2 open in eki worktrees") and "(pids 101, 103)" in line


def test_nothing_to_say(monkeypatch):
    monkeypatch.setattr(browserslots, "pids", lambda: set())
    assert browserstrays.note("  1 /sbin/launchd\n", "") == []
    assert browserstrays.note(_ps(), "") == []


def test_the_helpers_are_parsed():
    assert browserstrays.cwds("p7\nfcwd\nn/a b/c\np8\nfcwd\nn/d\n") == {7: "/a b/c", 8: "/d"}
