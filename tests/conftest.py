# SPDX-License-Identifier: Apache-2.0
"""Nothing a test does reaches your own skill store or eki's learning log."""
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
