# SPDX-License-Identifier: Apache-2.0
"""The Mac app follows the engine: checked when its source changes, rebuilt
after a healthy swap, never while you're using it."""
import json
import os
import plistlib
import shutil
from pathlib import Path

import pytest

from eki import appbuild, candidate


def tree(root: Path, swift: str = "let x = 1\n") -> Path:
    mac = root / "mac"
    mac.mkdir(parents=True, exist_ok=True)
    (mac / "App.swift").write_text(swift)
    # a stand-in build: an app folder with an executable, where it's told
    (mac / "build_app.sh").write_text(
        '#!/bin/sh\nset -e\nmkdir -p "$EKI_APP_PATH/Contents/MacOS"\n'
        'cp App.swift "$EKI_APP_PATH/Contents/MacOS/Eki"\n')
    (mac / "build_app.sh").chmod(0o755)
    return root


def test_the_source_hash_follows_the_swift(tmp_path):
    root = tree(tmp_path / "b")
    first = appbuild.sources_hash(root)
    assert first and appbuild.changed(root)
    appbuild._save(hash=first)
    assert not appbuild.changed(root)
    (root / "mac" / "App.swift").write_text("let x = 2\n")
    assert appbuild.sources_hash(root) != first and appbuild.changed(root)
    assert appbuild.sources_hash(tmp_path / "no-mac") == ""


def test_the_app_is_rebuilt_and_put_in_place_only_when_its_source_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(appbuild, "frontmost", lambda: False)
    monkeypatch.setattr(appbuild, "_running", lambda app: False)
    build, app = tree(tmp_path / "build"), tmp_path / "Eki.app"
    assert appbuild.install(build, app) == "the app was rebuilt from the running build"
    assert (app / "Contents" / "MacOS" / "Eki").read_text() == "let x = 1\n"
    assert not app.with_name("Eki.app.new").exists() and not app.with_name("Eki.app.old").exists()
    assert appbuild.install(build, app) == ""                        # unchanged: nothing to do
    (build / "mac" / "App.swift").write_text("let x = 2\n")
    assert appbuild.install(build, app).startswith("the app was rebuilt")
    assert (app / "Contents" / "MacOS" / "Eki").read_text() == "let x = 2\n"


def test_an_app_youre_using_is_replaced_but_left_open_to_restart_itself(tmp_path, monkeypatch):
    # waiting for it to leave the front never ended for an app kept in front
    build, app = tree(tmp_path / "build"), tmp_path / "Eki.app"
    appbuild.install(build, app)
    (build / "mac" / "App.swift").write_text("let x = 2\n")
    calls = []
    monkeypatch.setattr(appbuild, "_running", lambda app: True)
    monkeypatch.setattr(appbuild, "frontmost", lambda: True)
    monkeypatch.setattr(appbuild.subprocess, "run", _recording(calls))
    got = appbuild.install(build, app)
    assert "New version ready" in got and appbuild.state()["pending"] == ""
    assert (app / "Contents" / "MacOS" / "Eki").read_text() == "let x = 2\n"
    assert not any(c[0] in ("osascript", "open") for c in calls)       # never quit under you


def test_one_in_the_background_is_quit_and_reopened(tmp_path, monkeypatch):
    build, app = tree(tmp_path / "build"), tmp_path / "Eki.app"
    calls = []
    open_ = iter([True, False])
    monkeypatch.setattr(appbuild, "_running", lambda app: next(open_, False))
    monkeypatch.setattr(appbuild, "frontmost", lambda: False)
    monkeypatch.setattr(appbuild.subprocess, "run", _recording(calls))
    assert appbuild.install(build, app).endswith("and reopened")
    assert [c[0] for c in calls if c[0] != "./build_app.sh"] == ["osascript", "open"]


def _recording(calls):
    real = appbuild.subprocess.run

    def run(cmd, *a, **kw):
        calls.append(cmd)
        return real(cmd, *a, **kw) if cmd[0] == "./build_app.sh" else \
            appbuild.subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def stamp(app: Path, build: str, at: int) -> Path:
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"EkiBuild": build, "EkiBuiltAt": str(at)}, f)
    return app


def opened(app: Path, pid: int, build: str, at: int) -> None:
    appbuild.RUNNING.mkdir(parents=True, exist_ok=True)
    (appbuild.RUNNING / f"{pid}.json").write_text(json.dumps(
        {"pid": pid, "build": build, "built_at": at, "path": str(app)}))


def test_the_build_is_handed_to_the_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(appbuild, "frontmost", lambda: False)
    monkeypatch.setattr(appbuild, "_running", lambda app: False)
    build, app = tree(tmp_path / "build"), tmp_path / "Eki.app"
    (build / "mac" / "build_app.sh").write_text(
        '#!/bin/sh\nset -e\nmkdir -p "$EKI_APP_PATH/Contents/MacOS"\n'
        'cp App.swift "$EKI_APP_PATH/Contents/MacOS/Eki"\necho "$EKI_APP_BUILD" > "$EKI_APP_PATH/build"\n')
    appbuild.install(build, app)
    assert (app / "build").read_text().strip() == appbuild.sources_hash(build)[:7]


def test_the_real_build_script_stamps_the_build():
    script = (Path(__file__).resolve().parent.parent / "mac" / "build_app.sh").read_text()
    assert "<key>EkiBuild</key><string>$BUILD</string>" in script
    assert 'BUILD="${EKI_APP_BUILD:-dev}"' in script and "<key>EkiBuiltAt</key>" in script


def test_versions_say_what_runs_and_what_is_installed(tmp_path, monkeypatch):
    app = stamp(tmp_path / "Eki.app", "b7a8cf2", 1790351509)
    monkeypatch.setattr(appbuild, "_unstamped", lambda app: {})
    assert appbuild.versions_line(appbuild.versions(app)).startswith("app: not open, installed b7a8cf2 (built ")
    me = os.getpid()
    opened(app, me, "b7a8cf2", 1790351509)
    opened(tmp_path / "scratch.app", os.getppid(), "zzzzzzz", 1)         # a second copy elsewhere doesn't count
    v = appbuild.versions(app)
    assert not v["behind"] and appbuild.versions_line(v).endswith("up to date")
    stamp(app, "1a2b3c4", 1790400000)                            # rebuilt under it
    v = appbuild.versions(app)
    line = appbuild.versions_line(v)
    assert v["behind"] and "running b7a8cf2" in line and "installed 1a2b3c4" in line and "Restart" in line


def test_a_closed_app_is_forgotten(tmp_path, monkeypatch):
    app = stamp(tmp_path / "Eki.app", "b7a8cf2", 1)
    monkeypatch.setattr(appbuild, "_unstamped", lambda app: {})
    opened(app, 999999, "b7a8cf2", 1)
    assert appbuild.versions(app)["running"] == {}
    assert not list(appbuild.RUNNING.glob("*.json"))


def test_an_app_from_before_stamping_is_older_if_the_bundle_came_later(tmp_path, monkeypatch):
    app = stamp(tmp_path / "Eki.app", "1a2b3c4", 1790351509)     # put in place 09-26 00:51
    monkeypatch.setattr(appbuild, "_unstamped", lambda app: {"pid": 19619, "build": "", "opened": 1790190060})
    v = appbuild.versions(app)
    line = appbuild.versions_line(v)
    assert v["behind"] and line.startswith("app: running an unnamed build (open since ") and "quit and reopen" in line
    monkeypatch.setattr(appbuild, "_unstamped", lambda app: {"pid": 19619, "build": "", "opened": 1790400000})
    assert not appbuild.versions(app)["behind"]                   # reopened since: it runs what's there
    assert appbuild._seconds("1-02:03:04") == 93784 and appbuild._seconds("05:06") == 306


def test_a_build_that_fails_leaves_the_app_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(appbuild, "frontmost", lambda: False)
    monkeypatch.setattr(appbuild, "_running", lambda app: False)
    build, app = tree(tmp_path / "build"), tmp_path / "Eki.app"
    appbuild.install(build, app)
    (build / "mac" / "App.swift").write_text("let x = 3\n")
    (build / "mac" / "build_app.sh").write_text("#!/bin/sh\necho 'error: nope' >&2\nexit 1\n")
    assert appbuild.install(build, app).startswith("not rebuilt: error: nope")
    assert (app / "Contents" / "MacOS" / "Eki").read_text() == "let x = 1\n"


@pytest.mark.skipif(not shutil.which("swiftc"), reason="needs Xcode's command line tools")
def test_the_typecheck_names_the_error(tmp_path):
    good = tree(tmp_path / "good", "import SwiftUI\nstruct V: View { var body: some View { Text(\"hi\") } }\n")
    assert "compiles" in appbuild.typecheck(good)
    bad = tree(tmp_path / "bad", "import SwiftUI\nlet x: Int = \"no\"\n")
    with pytest.raises(RuntimeError, match=r"mac/App.swift:2.*error"):
        appbuild.typecheck(bad)


def test_the_candidate_check_compiles_the_app_only_when_it_changed(tmp_path, monkeypatch):
    root = tree(tmp_path / "c")
    seen = []
    monkeypatch.setattr(appbuild, "typecheck", lambda r: seen.append(r) or "the app compiles")
    report = candidate.Report(str(root))

    def step(name, fn):
        report.checks.append(candidate.Check(name, True, fn()))
        return True
    candidate.check_app(root, report, step, set())
    assert seen == [root] and report.checks[-1].name == "app" and not report.checks[-1].skipped
    appbuild._save(hash=appbuild.sources_hash(root))
    candidate.check_app(root, report, step, set())
    assert len(seen) == 1 and report.checks[-1].detail == "the app is unchanged"


def test_two_rebuilds_never_run_at_once(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(appbuild, "frontmost", lambda: False)
    monkeypatch.setattr(appbuild, "_running", lambda app: False)
    build, app = tree(tmp_path / "build"), tmp_path / "Eki.app"
    appbuild.STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(appbuild.STATE.with_suffix(".lock"), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert appbuild.install(build, app) == "waiting: another rebuild of the app is under way"
    assert appbuild.install(build, app).startswith("the app was rebuilt")
