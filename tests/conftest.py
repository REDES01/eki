# SPDX-License-Identifier: Apache-2.0
"""Nothing a test does reaches your own skill store, eki's learning log, or
Claude Code's memory."""
import pytest


@pytest.fixture(autouse=True)
def private_skills(tmp_path, monkeypatch):
    from eki import learn, skills
    root = tmp_path / "_home"
    monkeypatch.setattr(skills, "STORE", root / "eki" / "skills")
    monkeypatch.setattr(skills, "SIDECAR", root / "eki" / "skills.json")
    monkeypatch.setattr(skills, "VIEWS", {"claude": root / "claude" / "skills",
                                          "codex": root / "agents" / "skills"})
    monkeypatch.setattr(skills, "LEGACY", [root / "codex" / "skills"])
    monkeypatch.setattr(learn, "LOG", root / "eki" / "learn.json")
    monkeypatch.setattr(learn, "WORKDIR", root / "eki" / "learn")
    monkeypatch.setattr(learn, "ABSORBED", root / "eki" / "learn" / "absorbed")
    # Claude Code's own memory: a test must never read or move your notes
    monkeypatch.setattr(learn, "CLAUDE_PROJECTS", root / "claude" / "projects")
    from eki import workspace
    monkeypatch.setattr(workspace, "EKI_HOME", root / "eki")
    monkeypatch.setattr(workspace, "ROOT", root / "eki" / "worktrees")
    from eki import table, capacity
    monkeypatch.setattr(table, "PATH", root / "eki" / "routing.json")
    monkeypatch.setattr(capacity, "PATH", root / "eki" / "capacity.json")
    from eki import watch
    monkeypatch.setattr(watch, "HOME", root / "eki" / "watch")
    monkeypatch.setattr(watch, "STATE", root / "eki" / "watch" / "state.json")
    monkeypatch.setattr(watch, "_CACHE", {"mtime": None, "state": {}})
    from eki import observe
    monkeypatch.setattr(observe, "HOME", root / "eki" / "observe")
    # eki's settings: a test that changes one never writes this Mac's file
    from eki import settings
    monkeypatch.setattr(settings, "PATH", root / "eki" / "settings.json")
    from eki import builds, selfloop, selfwork
    monkeypatch.setattr(builds, "BUILDS", root / "eki" / "builds")
    monkeypatch.setattr(builds, "SELF_HOME", root / "eki" / "self")
    # eki's changes to itself, and the loop's items and notes
    monkeypatch.setattr(selfwork, "HOME", root / "eki" / "self")
    monkeypatch.setattr(selfloop, "HOME", root / "eki" / "self")
    monkeypatch.setattr(builds, "SUPERVISOR", root / "eki" / "bin" / "eki-supervisor")
    from eki import goals, shift, launcher
    # no test builds, or sees, this Mac's eki.app
    monkeypatch.setattr(launcher, "APP", root / "eki" / "bin" / "eki.app")
    monkeypatch.setattr(goals, "PATH", root / "eki" / "goals" / "goals.json")
    monkeypatch.setattr(goals, "LOG", root / "eki" / "goals" / "log.jsonl")
    # no test keeps this Mac awake
    monkeypatch.setattr(shift.Awake, "hold", lambda self, display=False: None)
    # nor reads who's at its keyboard, or leaves marks for this Mac's engine
    monkeypatch.setattr(shift, "idle_seconds", lambda: None)
    monkeypatch.setattr(shift, "screen_locked", lambda: False)
    monkeypatch.setattr(shift, "INPUT_MARK", root / "eki" / "run" / "input-at")


@pytest.fixture(autouse=True)
def private_models(tmp_path, monkeypatch, request):
    """eki's record of which servers it started, and the check of a live
    process, never reach this Mac's real servers from a test."""
    from eki import models
    monkeypatch.setattr(models.ModelManager, "STARTED_FILE", tmp_path / "_home" / "started.json")
    if "real_processes" not in request.keywords:
        monkeypatch.setattr(models, "started_by_eki", lambda port: False)
