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
                                          "codex": root / "agents" / "skills",
                                          "gemini": root / "gemini" / "skills"})
    monkeypatch.setattr(skills, "LEGACY", [root / "codex" / "skills"])
    # every CLI counts as installed unless a test says otherwise; none of
    # this Mac's own programs decide what a test sees
    monkeypatch.setattr(skills, "present", lambda backend: True)
    monkeypatch.setattr(learn, "LOG", root / "eki" / "learn.json")
    monkeypatch.setattr(learn, "WORKDIR", root / "eki" / "learn")
    monkeypatch.setattr(learn, "ABSORBED", root / "eki" / "learn" / "absorbed")
    # Claude Code's own memory: a test must never read or move your notes
    monkeypatch.setattr(learn, "CLAUDE_PROJECTS", root / "claude" / "projects")
    # the tool registry and the CLIs' files it is rendered into
    from eki import mcpregistry
    monkeypatch.setattr(mcpregistry, "PATH", root / "eki" / "mcp.json")
    monkeypatch.setattr(mcpregistry, "CODEX_CONFIG", root / "codex" / "config.toml")
    monkeypatch.setattr(mcpregistry, "GEMINI_SETTINGS", root / "gemini" / "settings.json")
    monkeypatch.setattr(mcpregistry, "GEMINI_OWNED", root / "eki" / "mcp-gemini.json")
    # the CLIs' standing context: CLAUDE.md, AGENTS.md
    from eki import standing
    monkeypatch.setattr(standing, "HOME", root / "eki" / "context")
    monkeypatch.setattr(standing, "IMPORTED", root / "eki" / "context" / "imported")
    monkeypatch.setattr(standing, "CLAUDE_HOME", root / "claude")
    monkeypatch.setattr(standing, "CODEX_HOME", root / "codex")
    # eki's memory: a test never reads or writes your notes
    from eki import notes
    monkeypatch.setattr(notes, "HOME", root / "eki" / "memory")
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
    # eki's own source, as far as a test knows, is nowhere: no test makes a
    # branch or a worktree in the real repo (a test that wants self-work
    # gives the engine a throwaway repo of its own)
    monkeypatch.setenv("EKI_SOURCE", str(root / "no-source"))
    monkeypatch.setattr(builds, "BUILDS", root / "eki" / "builds")
    monkeypatch.setattr(builds, "SELF_HOME", root / "eki" / "self")
    # what this Mac's Eki.app was built from; no test rebuilds or quits it
    from eki import appbuild
    monkeypatch.setattr(appbuild, "STATE", root / "eki" / "app.json")
    # eki's changes to itself, and the loop's items and notes
    monkeypatch.setattr(selfwork, "HOME", root / "eki" / "self")
    monkeypatch.setattr(selfwork, "SHOTS", root / "eki" / "shots")
    monkeypatch.setattr(selfloop, "HOME", root / "eki" / "self")
    from eki import digest
    monkeypatch.setattr(digest, "HOME", root / "eki" / "self")
    from eki import steps
    monkeypatch.setattr(steps, "HOME", root / "eki" / "self")
    from eki import pipeline
    monkeypatch.setattr(pipeline, "HOME", root / "eki" / "self")
    # the programs eki starts as workers, and what they print
    from eki import workers
    monkeypatch.setattr(workers, "HOME", root / "eki" / "work")
    monkeypatch.setattr(workers, "_held", {})
    monkeypatch.setattr(workers, "_orphan_since", {})
    monkeypatch.setattr(builds, "SUPERVISOR", root / "eki" / "bin" / "eki-supervisor")
    # the restart drill boots real engines: only the tests about it run one;
    # and its result is kept in the test's home, not this Mac's
    monkeypatch.setenv("EKI_CHECK_SKIP", "restart")
    from eki import drill
    monkeypatch.setattr(drill, "RESULT", root / "eki" / "self" / "drill.json")
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


@pytest.fixture(autouse=True)
def no_workers_left(private_skills):
    """A worker outlives the engine on purpose (eki/workers.py) — and would
    outlive the test, so each one a test started is stopped when it ends."""
    yield
    from eki import workers
    for w in workers.scan():
        if w.alive(strict=False):
            w.signal(9)
