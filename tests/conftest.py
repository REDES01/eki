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
