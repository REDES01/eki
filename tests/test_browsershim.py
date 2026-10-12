"""The browser shim: the mirror follows Playwright's folder, and its wrapped
executables wait for a browser slot before the real browser starts."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from eki import browsershim, browserslots

FAKE = """#!/bin/sh
echo "$$ $@" > "$MARKER"
exec sleep "${HOLD:-30}"
"""

HOLD = """
import fcntl, os, sys
fds = []
for p in sys.argv[1:]:
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    fds.append(fd)
print("held", flush=True)
sys.stdin.read()
"""


@pytest.fixture
def real(tmp_path, monkeypatch):
    r = tmp_path / "pw-cache"
    r.mkdir()
    monkeypatch.setenv("EKI_PLAYWRIGHT_CACHE", str(r))
    return r


def browser(folder, version="1187"):
    """A fake chromium_headless_shell-<version> like Playwright's on a Mac."""
    top = folder / f"chromium_headless_shell-{version}"
    exe_dir = top / "chrome-headless-shell-mac-arm64"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "chrome-headless-shell"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    (exe_dir / "libGLESv2.dylib").write_text("lib")
    (exe_dir / "headless_command_resources.pak").write_text("pak")
    (top / "INSTALLATION_COMPLETE").write_text("")
    return top


def wrapped(version="1187"):
    return (browsershim.mirror() / f"chromium_headless_shell-{version}"
            / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")


def launch(tmp_path, name="marker", hold="30"):
    marker = tmp_path / name
    env = {**os.environ, "MARKER": str(marker), "HOLD": hold}
    child = subprocess.Popen([str(wrapped()), "--headless", "--remote-debugging-pipe"],
                             env=env, stderr=subprocess.PIPE, text=True)
    return child, marker


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def hold(*names):
    paths = [str(browserslots.folder() / n) for n in names]
    child = subprocess.Popen([sys.executable, "-c", HOLD, *paths],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


def stop(child):
    if child.stdin:
        child.stdin.close()
    if child.poll() is None:
        child.kill()
    child.wait(timeout=10)


def test_mirror_symlinks_all_but_the_executable(home, real):
    top = browser(real)
    (real / ".links").mkdir()
    (real / "ffmpeg-1011").mkdir()
    assert browsershim.ensure() == browsershim.mirror()
    m = browsershim.mirror()
    assert (m / ".links").is_symlink() and (m / "ffmpeg-1011").is_symlink()
    mtop = m / top.name
    assert mtop.is_dir() and not mtop.is_symlink()
    assert (mtop / "INSTALLATION_COMPLETE").is_symlink()
    inner = mtop / "chrome-headless-shell-mac-arm64"
    assert inner.is_dir() and not inner.is_symlink()
    assert (inner / "libGLESv2.dylib").is_symlink()
    assert os.readlink(inner / "libGLESv2.dylib") == str(top / "chrome-headless-shell-mac-arm64"
                                                         / "libGLESv2.dylib")
    exe = wrapped()
    assert not exe.is_symlink() and exe.is_file()
    assert exe.stat().st_mode & 0o777 == 0o755
    text = exe.read_text()
    assert text.startswith(f"#!{sys.executable}")
    assert "import eki" not in text and "from eki" not in text
    assert str(top / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell") in text


def test_framework_folders_are_not_walked(home, real):
    top = real / "chromium-1187"
    fw = top / "chrome-mac" / "Chromium.app" / "Contents" / "Frameworks" / "X.framework"
    fw.mkdir(parents=True)
    (fw / "chrome").write_text("#!/bin/sh\n")
    (fw / "chrome").chmod(0o755)
    macos = top / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "Chromium").write_text("#!/bin/sh\n")
    (macos / "Chromium").chmod(0o755)
    assert browsershim.executables(top) == [macos / "Chromium"]
    browsershim.ensure()
    contents = browsershim.mirror() / "chromium-1187" / "chrome-mac" / "Chromium.app" / "Contents"
    assert (contents / "Frameworks").is_symlink()
    assert not (contents / "MacOS" / "Chromium").is_symlink()


def test_wrapper_runs_the_browser_holding_a_slot(home, real, tmp_path):
    browser(real)
    browsershim.ensure()
    child, marker = launch(tmp_path)
    try:
        assert wait_for(marker.exists)
        pid, *args = marker.read_text().split()
        assert int(pid) == child.pid                   # exec'd: the browser keeps the pid
        assert args == ["--headless", "--remote-debugging-pipe"]
        assert browserslots.held() == 1
        assert browserslots.pids() == {child.pid}
    finally:
        stop(child)
    assert wait_for(lambda: browserslots.held() == 0)


def test_wrapper_waits_while_the_slots_are_held(home, real, tmp_path):
    data = json.loads((home / "routing.json").read_text())
    data["machine"] = {"browsers": 2}
    (home / "routing.json").write_text(json.dumps(data))
    browser(real)
    browsershim.ensure()
    others = [hold("0.lock"), hold("1.lock")]
    child, marker = launch(tmp_path)
    try:
        time.sleep(1.5)
        assert not marker.exists() and child.poll() is None
        stop(others[0])
        assert wait_for(marker.exists)
        assert browserslots.held() == 2
        assert child.pid in browserslots.pids()
    finally:
        stop(child)
        for o in others:
            stop(o)
    err = child.stderr.read()
    lines = [ln for ln in err.splitlines() if ln.strip()]
    assert lines == ["eki: waiting for a browser slot — 2 of 2 browsers open "
                     "(Settings → Machine → Browsers at once); "
                     "Playwright's launch timeout still counts"]


def test_mirror_follows_new_and_removed_versions(home, real):
    old = browser(real, "1180")
    browsershim.ensure()
    assert wrapped("1180").is_file()
    browser(real, "1187")
    browsershim.ensure()
    assert wrapped("1187").is_file() and not wrapped("1187").is_symlink()
    assert "chrome-headless-shell" in wrapped("1187").read_text()
    shutil.rmtree(old)
    browsershim.ensure()
    assert not (browsershim.mirror() / old.name).exists()
    assert wrapped("1187").is_file()


def test_ensure_is_cheap_when_nothing_changed(home, real, monkeypatch):
    browser(real)
    browsershim.ensure()
    called = []
    monkeypatch.setattr(browsershim, "_sync", lambda *a: called.append(a))
    browsershim.ensure()
    assert called == []


def test_an_install_into_the_mirror_moves_into_real(home, real):
    browsershim.ensure()
    browser(browsershim.mirror(), "1200")              # an agent's `npx playwright install`
    browsershim.ensure()
    assert (real / "chromium_headless_shell-1200" / "INSTALLATION_COMPLETE").exists()
    assert not (real / "chromium_headless_shell-1200").is_symlink()
    exe = wrapped("1200")
    assert exe.is_file() and not exe.is_symlink()
    assert str(real / "chromium_headless_shell-1200") in exe.read_text()


def test_ensure_never_fails_the_caller(home, real, monkeypatch):
    def boom(*a):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(browsershim, "_sync", boom)
    browser(real)
    assert browsershim.ensure() == browsershim.mirror()
