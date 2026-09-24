# SPDX-License-Identifier: Apache-2.0
"""The engine runs under a small app called eki, so macOS asks in eki's name —
not python's — for the engine and everything it starts."""
import plistlib
from pathlib import Path

from eki import agent, launcher


def test_without_the_app_the_engine_runs_as_python(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "APP", tmp_path / "eki.app")
    assert not launcher.current()
    args = agent.plist_for(tmp_path)["ProgramArguments"]
    assert args[0].endswith("/python") and args[1:] == ["-m", "eki.cli", "serve"]


def test_with_it_launchd_starts_eki_which_starts_the_engine(tmp_path, monkeypatch):
    app = tmp_path / "eki.app"
    monkeypatch.setattr(launcher, "APP", app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    launcher.binary().write_text("")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"EkiLauncherSource": launcher._hash()}))
    assert launcher.current()
    args = agent.plist_for(tmp_path)["ProgramArguments"]
    assert args[0] == str(launcher.binary()) and args[1].endswith("/python")


def test_a_changed_source_means_a_rebuild(tmp_path, monkeypatch):
    app = tmp_path / "eki.app"
    monkeypatch.setattr(launcher, "APP", app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    launcher.binary().write_text("")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"EkiLauncherSource": "an-older-one"}))
    assert not launcher.current()


def test_no_swift_compiler_no_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "APP", tmp_path / "eki.app")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.build() is None


# ---- an older engine left on the port ----------------------------------------------------

LISTENER = ("import signal, socket, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"      # as stuck as the one of 2026-09-24
            "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen(); time.sleep(60)\n")


def _hold_port(*words):
    import socket
    import subprocess
    import sys
    import time
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-c", LISTENER, str(port), *words])
    deadline = time.time() + 10
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)
    return port, proc


def test_an_older_engine_on_the_port_is_stopped_before_binding():
    """2026-09-24: an engine outlived its launcher, kept the port, and every
    new one died with "address already in use". A stuck one that doesn't
    answer and ignores TERM too."""
    from eki import service
    port, proc = _hold_port("eki.cli", "serve")
    try:
        assert service.take_port("127.0.0.1", port, grace=1.0) == [proc.pid]
        assert proc.wait(timeout=5) is not None
        assert not service._port_taken("127.0.0.1", port)
    finally:
        proc.kill()


def test_something_else_on_the_port_is_left_alone():
    from eki import service
    port, proc = _hold_port("not-eki")
    try:
        assert service.take_port("127.0.0.1", port, grace=1.0) == []
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
