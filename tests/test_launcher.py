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
