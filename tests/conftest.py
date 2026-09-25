"""Every test runs in a throwaway EKI_HOME. The suite refuses to touch your real one."""
import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("EKI_HOME", str(h))
    monkeypatch.setenv("EKI_AGENTS_SKILLS", str(tmp_path / "agents-skills"))
    monkeypatch.setenv("EKI_MACHINE", "ok")
    real = Path("~").expanduser()
    assert not str(h).startswith(str(real / ".eki")), "tests must never use the real eki home"
    (h / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"}, "fake2": {"kind": "fake"},
        "bare": {"kind": "fake", "harness": False}}))
    (h / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "general", "title": "all", "targets": ["fake", "fake2"]},
        {"key": "code", "title": "tools", "targets": ["fake", "fake2"]}]}))
    yield h


@pytest.fixture
def conn(home):
    from eki import db
    return db.connect()


def run_inline(conn, rid):
    """What the engine does, without the engine: mark it starting, run the worker here."""
    from eki import store, worker
    store.update_run(conn, rid, state="starting")
    worker.main(rid)
    return store.run(conn, rid)
