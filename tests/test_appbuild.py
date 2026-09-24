# SPDX-License-Identifier: Apache-2.0
"""The Mac app follows the engine: checked when its source changes, rebuilt
after a healthy swap, never while you're using it."""
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


def test_while_you_use_the_app_it_waits_and_remembers(tmp_path, monkeypatch):
    monkeypatch.setattr(appbuild, "_running", lambda app: False)
    build, app = tree(tmp_path / "build"), tmp_path / "Eki.app"
    monkeypatch.setattr(appbuild, "frontmost", lambda: True)
    assert appbuild.install(build, app).startswith("waiting")
    assert appbuild.state()["pending"] == str(build) and not app.exists()
    monkeypatch.setattr(appbuild, "frontmost", lambda: False)
    assert appbuild.install(build, app).startswith("the app was rebuilt")
    assert appbuild.state()["pending"] == ""


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
