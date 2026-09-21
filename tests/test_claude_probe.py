# SPDX-License-Identifier: Apache-2.0
"""Asking Claude Code for a reading — and refusing to answer for the user."""
import asyncio
import json

import pytest

from eki.quota import claude_probe


def run(coro):
    return asyncio.run(coro)


def test_trust_is_read_never_written(tmp_path, monkeypatch):
    config = tmp_path / ".claude.json"
    folder = tmp_path / "probe"
    folder.mkdir()
    monkeypatch.setattr(claude_probe, "CLAUDE_CONFIG", config)
    monkeypatch.setattr(claude_probe, "PROBE_DIR", folder)

    assert claude_probe.trusted(folder) is False       # no config at all
    config.write_text(json.dumps({"projects": {str(folder): {}}}))
    assert claude_probe.trusted(folder) is False       # known, not trusted
    config.write_text(json.dumps(
        {"projects": {str(folder): {"hasTrustDialogAccepted": True}}}))
    assert claude_probe.trusted(folder) is True
    # nothing here writes to Claude Code's config
    before = config.read_text()
    claude_probe.trusted(folder)
    assert config.read_text() == before


def test_an_untrusted_folder_stops_before_starting_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_probe, "CLAUDE_CONFIG", tmp_path / "missing.json")
    started = []
    monkeypatch.setattr(claude_probe, "_drive",
                        lambda *a, **kw: started.append(a) or (True, "nope"))
    with pytest.raises(claude_probe.NotTrusted) as caught:
        run(claude_probe.refresh())
    assert "trust" in str(caught.value).lower()
    assert started == []                               # no session was opened


def test_a_panel_without_plan_limits_is_an_error(monkeypatch):
    monkeypatch.setattr(claude_probe, "trusted", lambda *a, **kw: True)
    monkeypatch.setattr(claude_probe, "_drive",
                        lambda *a, **kw: (["Session", "Total cost: $0.0000"], ""))
    with pytest.raises(RuntimeError, match="no plan limits"):
        run(claude_probe.refresh())


def test_a_read_panel_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_probe, "trusted", lambda *a, **kw: True)
    monkeypatch.setattr(claude_probe, "USAGE", tmp_path / "claude-usage.json")
    monkeypatch.setattr(claude_probe, "QUOTA_DIR", tmp_path)
    monkeypatch.setattr(claude_probe, "_drive", lambda *a, **kw: ([
        "Current session", "██  4% used", "Resets 4:10pm (Asia/Tokyo)",
        "Current week (Fable)", "████ 17% used", "Resets Sep 21 at 11pm (Asia/Tokyo)",
    ], ""))
    assert "2 limits" in run(claude_probe.refresh())
    saved = claude_probe.reading()
    assert set(saved["rate_limits"]) == {"five_hour", "seven_day_fable"}


def test_the_session_it_opens_can_do_nothing(monkeypatch):
    # plan mode, no tools: a probe must not be able to touch the folder it
    # runs in, whatever the model decides to try
    assert "--permission-mode" in claude_probe.ARGS
    assert claude_probe.ARGS[claude_probe.ARGS.index("--permission-mode") + 1] == "plan"
    assert "--allowedTools" in claude_probe.ARGS
    assert claude_probe.ARGS[claude_probe.ARGS.index("--allowedTools") + 1] == ""
