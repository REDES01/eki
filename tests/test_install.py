# SPDX-License-Identifier: Apache-2.0
"""Installed by Homebrew: eki runs the same, but doesn't change its own code,
and `eki agent` hands the engine to `brew services`."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

import eki
from eki import agent, builds, selfwork

ROOT = Path(__file__).resolve().parents[1]


def test_the_version_comes_from_the_version_file():
    assert eki.__version__ == (ROOT / "VERSION").read_text().strip()


def test_a_checkout_may_change_its_code_a_package_may_not(tmp_path, monkeypatch):
    assert builds.cant_change_code(ROOT) == ""
    monkeypatch.setenv("EKI_INSTALL", "homebrew")
    why = builds.cant_change_code(tmp_path)
    assert "installed by Homebrew" in why and "git clone https://github.com/REDES01/eki" in why
    with pytest.raises(ValueError, match="Homebrew"):
        builds.make(tmp_path)
    with pytest.raises(selfwork.SelfWorkError, match="Homebrew"):
        selfwork.propose("fix it", root=tmp_path, ask=lambda *a, **k: {})


def test_agent_hands_the_engine_to_brew_services(monkeypatch):
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        out = '[{"name": "eki", "loaded": true, "running": true, "status": "started"}]' \
            if "info" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setenv("EKI_INSTALL", "homebrew")
    monkeypatch.setattr(agent.subprocess, "run", fake)
    assert agent.installed()
    assert "brew services" in agent.install(Path("/nowhere"))
    assert ["brew", "services", "restart", "eki"] in calls          # already running: restarted
    assert agent.status() == "run by `brew services` · started"
    assert agent.restart() == "restarted"
    agent.uninstall()
    assert calls[-1] == ["brew", "services", "stop", "eki"]
    assert not any("launchctl" in c[0] for c in calls)


def _formula():
    spec = importlib.util.spec_from_file_location("make_formula", ROOT / "packaging/homebrew/make_formula.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def w(name):
    return {"filename": name, "url": f"https://files/{name}", "digests": {"sha256": "0" * 64}}


def test_the_formula_picks_the_right_wheel_per_platform():
    mf = _formula()
    files = [w("pydantic_core-2.46.5-cp312-cp312-macosx_11_0_arm64.whl"),
             w("pydantic_core-2.46.5-cp312-cp312-macosx_10_12_x86_64.whl"),
             w("pydantic_core-2.46.5-cp313-cp313-macosx_11_0_arm64.whl"),
             w("pydantic_core-2.46.5-cp312-cp312-musllinux_1_1_x86_64.whl"),
             w("pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
             w("pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl")]
    plat = {tag: want for tag, _, want in mf.PLATFORMS}
    assert mf.pick(files, plat["macos_arm"])["filename"].endswith("cp312-cp312-macosx_11_0_arm64.whl")
    assert "x86_64" in mf.pick(files, plat["macos_intel"])["filename"]
    assert set(plat) == {"macos_arm", "macos_intel"}                     # a Mac tool
    assert mf.pick(files, None) is None                              # nothing pure
    abi3 = [w("cryptography-50.0.1-cp311-abi3-macosx_10_9_universal2.whl")]
    assert mf.pick(abi3, plat["macos_arm"]) is not None               # abi3 from 3.11 runs on 3.12
    pure = [w("httpx-0.28.1-py3-none-any.whl")]
    assert mf.pick(pure, None)["filename"].endswith("py3-none-any.whl")
