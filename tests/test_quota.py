"""Usage limits: parsing, the board, and the status-line bridge.

The first test is the bug that shipped once in tokenbar: a source reporting
percent sends 1.0 for one percent, and guessing the scale showed 100% the
instant a weekly window reset.
"""
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from eki.quota import QuotaBoard, Reading, Window
from eki.quota import claude as claude_quota
from eki.quota import claude_bridge, statusline_bridge
from eki.quota import codex as codex_quota

NOW = 1_800_000_000


# ---- Claude, from the status-line JSON -----------------------------------

def test_one_percent_is_one_percent():
    reading = claude_quota.parse(
        {"observed_at": NOW, "rate_limits": {
            "seven_day": {"used_percentage": 1.0, "resets_at": NOW + 3600}}}, NOW)
    assert reading.windows[0].used == pytest.approx(0.01)


def test_both_windows_are_read_and_labelled():
    reading = claude_quota.parse(
        {"observed_at": NOW - 60, "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": NOW + 600},
            "seven_day": {"used_percentage": 41.2, "resets_at": NOW + 86400}}}, NOW)
    assert [(w.label, round(w.used, 3)) for w in reading.windows] == \
        [("5H", 0.235), ("WEEK", 0.412)]
    assert reading.observed_at == NOW - 60


def test_a_window_that_already_reset_is_not_shown():
    """Stale is worse than absent: 100% of a period that has ended is a lie."""
    reading = claude_quota.parse(
        {"observed_at": NOW - 7200, "rate_limits": {
            "five_hour": {"used_percentage": 100, "resets_at": NOW - 5},
            "seven_day": {"used_percentage": 40, "resets_at": NOW + 86400}}}, NOW)
    assert [w.label for w in reading.windows] == ["WEEK"]


def test_a_spend_limit_is_money_not_a_window():
    reading = claude_quota.parse(
        {"observed_at": NOW, "rate_limits": {
            "spend_limit": {"used_percentage": 130, "resets_at": NOW - 5}}}, NOW)
    assert reading.windows[0].kind == "credits"
    assert reading.windows[0].used == pytest.approx(1.3)   # may pass 100%


# ---- Codex, from its app-server ------------------------------------------

def test_codex_windows_are_sorted_by_length_not_name():
    payload = {"rateLimits": {
        "primary": {"usedPercent": 3, "windowDurationMins": 10080, "resetsAt": NOW + 999},
        "secondary": {"usedPercent": 60, "windowDurationMins": 300, "resetsAt": NOW + 99}}}
    reading = codex_quota.parse(payload, NOW)
    assert [(w.label, w.used) for w in reading.windows] == [("5H", 0.6), ("WEEK", 0.03)]


@pytest.mark.parametrize("seconds,label", [
    (5 * 3600, "5H"), (7 * 86400, "WEEK"), (30 * 86400, "MONTH"),
    (3 * 86400, "3D"), (None, "fallback"),
])
def test_windows_are_named_by_their_real_length(seconds, label):
    from eki.quota import label_for
    assert label_for(seconds, "fallback") == label


def test_a_lone_thirty_day_codex_window_is_a_month():
    payload = {"rateLimits": {"primary": {"usedPercent": 11, "windowDurationMins": 43200}}}
    assert [w.label for w in codex_quota.parse(payload, NOW).windows] == ["MONTH"]


def test_codex_unrecognised_shape_fails_loudly():
    with pytest.raises(ValueError):
        codex_quota.parse({"nothing": "here"}, NOW)


# ---- the board -----------------------------------------------------------

def board_with(*readings):
    board = QuotaBoard([], ceiling=0.99)
    for r in readings:
        board.latest[r.provider] = r
    return board


def test_a_spent_window_parks_its_provider():
    board = board_with(Reading("claude", [Window("five_hour", "5H", 1.0)]))
    assert board.exhausted() == {"claude": "5H at 100%"}


def test_a_full_credit_meter_does_not():
    board = board_with(Reading("claude", [Window("spend", "SPEND", 1.4, kind="credits")]))
    assert board.exhausted() == {}


@pytest.mark.asyncio
async def test_a_failing_source_keeps_its_last_reading_and_says_why():
    class Flaky(claude_quota.ClaudeStatusLine):
        calls = 0

        async def fetch(self):
            Flaky.calls += 1
            if Flaky.calls == 1:
                return Reading("claude", [Window("five_hour", "5H", 0.2)], observed_at=NOW)
            raise RuntimeError("file vanished")

    source = Flaky("claude")
    first = await source.read(force=True)
    second = await source.read(force=True)
    assert second.windows == first.windows
    assert second.observed_at == NOW                  # not re-stamped as fresh
    assert "vanished" in second.error


# ---- the bridge ----------------------------------------------------------

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    quota = tmp_path / "quota"
    settings = tmp_path / "claude" / "settings.json"
    for mod in (statusline_bridge, claude_bridge):
        monkeypatch.setattr(mod, "QUOTA_DIR", quota)
        monkeypatch.setattr(mod, "READING", quota / "claude.json")
        monkeypatch.setattr(mod, "CHAIN", quota / "statusline-chain.json")
    monkeypatch.setattr(claude_bridge, "SETTINGS", settings)
    return settings


def run_bridge(monkeypatch, capsys, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    statusline_bridge.main()
    return capsys.readouterr().out.strip()


def test_bridge_records_limits_and_prints_a_line(isolated, monkeypatch, capsys):
    out = run_bridge(monkeypatch, capsys, {
        "model": {"id": "opus", "display_name": "Opus"},
        "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW}}})
    assert out == "Opus · 5h 12%"
    saved = claude_bridge.reading()
    assert saved["rate_limits"]["five_hour"]["used_percentage"] == 12
    assert abs(saved["observed_at"] - time.time()) < 5


def test_bridge_records_that_it_ran_even_without_limits(isolated, monkeypatch, capsys):
    # the reading exists but is empty, which is how "Claude Code ran and told
    # us nothing" is told apart from "Claude Code hasn't run"
    run_bridge(monkeypatch, capsys, {"model": {"display_name": "Opus"}})
    saved = claude_bridge.reading()
    assert saved is not None and saved["rate_limits"] == {}
    reading = claude_quota.parse(saved, int(time.time()))
    assert reading.windows == [] and "reported no limits" in reading.note


def test_install_keeps_other_settings_and_uninstall_restores(isolated):
    isolated.parent.mkdir(parents=True)
    isolated.write_text(json.dumps({"theme": "dark"}))
    claude_bridge.install(python="/usr/bin/python3")
    settings = json.loads(isolated.read_text())
    assert settings["theme"] == "dark"
    assert claude_bridge.MARKER in settings["statusLine"]["command"]
    assert claude_bridge.installed()

    claude_bridge.uninstall()
    assert json.loads(isolated.read_text()) == {"theme": "dark"}


def test_an_existing_status_line_is_chained_not_replaced(isolated, monkeypatch, capsys):
    isolated.parent.mkdir(parents=True)
    theirs = {"type": "command", "command": "echo mine"}
    isolated.write_text(json.dumps({"statusLine": theirs}))
    claude_bridge.install(python="/usr/bin/python3")

    # what the user sees is still their own line
    assert run_bridge(monkeypatch, capsys, {"rate_limits": {}}) == "mine"

    claude_bridge.uninstall()
    assert json.loads(isolated.read_text())["statusLine"] == theirs


def test_install_refuses_a_settings_file_it_cannot_parse(isolated):
    isolated.parent.mkdir(parents=True)
    isolated.write_text("{ not json")
    with pytest.raises(RuntimeError):
        claude_bridge.install(python="/usr/bin/python3")
    assert isolated.read_text() == "{ not json"
