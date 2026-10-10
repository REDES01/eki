"""Model lists: what each program says of its models, into provider_models, never written to settings."""
import json
import sys
import threading

import pytest

from eki import db, housekeep, modellist
from eki.cli import main
from eki.providers.local import Local

FAKE_CLAUDE = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    assert sys.argv[1:] == ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    if msg.get("type") == "control_request" and msg["request"]["subtype"] == "initialize":
        print(json.dumps({"type": "system", "subtype": "other"}), flush=True)
        print(json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": msg["request_id"], "response": {"models": [
                {"value": "default", "displayName": "Default (recommended)", "description": "Opus 5.5",
                 "resolvedModel": "claude-opus-5-5", "supportsEffort": True,
                 "supportedEffortLevels": ["low", "medium", "high"]},
                {"value": "claude-sonnet-4-5", "displayName": "Sonnet", "description": "everyday",
                 "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
                {"value": "opus", "displayName": "Opus", "description": "hardest",
                 "resolvedModel": "claude-opus-5-5", "supportsEffort": True,
                 "supportedEffortLevels": ["low", "medium", "high", "max"]},
                {"value": "haiku", "displayName": "Haiku 4.5", "description": "quick",
                 "resolvedModel": "claude-haiku-4-5", "supportsEffort": False,
                 "supportedEffortLevels": ["low"]}]}}}), flush=True)
'''

FAKE_CODEX = r'''
import json, sys
assert sys.argv[-1] == "app-server"
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"userAgent": "fake"}}), flush=True)
    elif msg.get("method") == "initialized":
        open(sys.argv[0] + ".initialized", "w").close()
    elif msg.get("method") == "model/list":
        print(json.dumps({"jsonrpc": "2.0", "method": "some/notification", "params": {}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"data": [
            {"id": "gpt-5-codex", "displayName": "GPT-5 Codex", "description": "for code",
             "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "quick"},
                                           {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}],
             "defaultReasoningEffort": "medium", "isDefault": True},
            {"id": "gpt-5", "displayName": "GPT-5", "description": "broad",
             "supportedReasoningEfforts": [{"reasoningEffort": "minimal"}, {"reasoningEffort": "high"}],
             "defaultReasoningEffort": "high", "isDefault": False},
            {"id": "gpt-5-codex-mini", "displayName": "GPT-5 Codex Mini", "isDefault": False},
            {"id": "secret", "displayName": "Hidden", "hidden": True}]}}), flush=True)
'''


def fake_bin(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def progs(home, tmp_path):
    cfg = json.loads((home / "providers.json").read_text())
    cfg["claude"] = {"kind": "claude_code", "binary": fake_bin(tmp_path, "claude", FAKE_CLAUDE), "model": "sonnet"}
    cfg["codex"] = {"kind": "codex", "binary": fake_bin(tmp_path, "codex", FAKE_CODEX)}
    cfg["local"] = {"kind": "local", "base_url": "http://127.0.0.1:9"}
    (home / "providers.json").write_text(json.dumps(cfg, indent=2))
    return home / "providers.json"


def _fields(m):
    return {k: m[k] for k in ("model", "label", "description", "resolved", "efforts",
                              "default_effort", "is_default", "floating")}


def test_claude_list_from_the_initialize_reply(conn, progs):
    before = progs.read_text()
    assert modellist.listed(conn, "claude") is None
    rows = modellist.refresh(conn, "claude")
    assert [r["model"] for r in rows] == ["default", "claude-sonnet-4-5", "opus", "haiku"]
    got = modellist.listed(conn, "claude")
    assert [_fields(m) for m in got] == [
        {"model": "default", "label": "Default (recommended)", "description": "Opus 5.5",
         "resolved": "claude-opus-5-5", "efforts": ["low", "medium", "high"], "default_effort": None,
         "is_default": True, "floating": True},
        {"model": "claude-sonnet-4-5", "label": "Sonnet", "description": "everyday", "resolved": None,
         "efforts": ["low", "high"], "default_effort": None, "is_default": False, "floating": False},
        {"model": "opus", "label": "Opus", "description": "hardest", "resolved": "claude-opus-5-5",
         "efforts": ["low", "medium", "high", "max"], "default_effort": None,
         "is_default": False, "floating": True},
        {"model": "haiku", "label": "Haiku 4.5", "description": "quick", "resolved": "claude-haiku-4-5",
         "efforts": None, "default_effort": None, "is_default": False, "floating": True}]   # no supportsEffort
    assert modellist.read_at(conn, "claude") is not None
    assert progs.read_text() == before


def test_codex_list_from_model_list(conn, progs, tmp_path):
    before = progs.read_text()
    modellist.refresh(conn, "codex")
    assert (tmp_path / "codex.initialized").exists()
    got = modellist.listed(conn, "codex")
    assert [_fields(m) for m in got] == [
        {"model": "gpt-5-codex", "label": "GPT-5 Codex", "description": "for code", "resolved": None,
         "efforts": ["low", "medium", "high"], "default_effort": "medium", "is_default": True,
         "floating": False},
        {"model": "gpt-5", "label": "GPT-5", "description": "broad", "resolved": None,
         "efforts": ["minimal", "high"], "default_effort": "high", "is_default": False, "floating": False},
        {"model": "gpt-5-codex-mini", "label": "GPT-5 Codex Mini", "description": None, "resolved": None,
         "efforts": None, "default_effort": None, "is_default": False, "floating": False}]
    assert progs.read_text() == before


def test_local_list_takes_no_effort(conn, progs, monkeypatch):
    monkeypatch.setattr(Local, "models", lambda self, timeout=1.5: ["qwen3:32b", "gemma"])
    modellist.refresh(conn, "local")
    got = modellist.listed(conn, "local")
    assert [m["model"] for m in got] == ["qwen3:32b", "gemma"]
    assert all(m["efforts"] is None and m["description"] is None and not m["floating"] for m in got)
    assert conn.execute("SELECT COUNT(*) FROM provider_models WHERE provider='local'"
                        " AND efforts IS NOT NULL").fetchone()[0] == 0


def test_refresh_replaces_only_that_providers_rows(conn, progs, monkeypatch):
    monkeypatch.setattr(Local, "models", lambda self, timeout=1.5: ["a", "b"])
    modellist.refresh(conn, "local")
    modellist.refresh(conn, "claude")
    monkeypatch.setattr(Local, "models", lambda self, timeout=1.5: ["c"])
    modellist.refresh(conn, "local")
    assert [m["model"] for m in modellist.listed(conn, "local")] == ["c"]
    assert len(modellist.listed(conn, "claude")) == 4
    conn.execute("UPDATE provider_models SET description='stale', efforts=NULL WHERE provider='claude'")
    modellist.refresh(conn, "claude")                                      # a re-read replaces the rows
    got = modellist.listed(conn, "claude")
    assert len(got) == 4 and got[0]["description"] == "Opus 5.5" and got[0]["efforts"] == ["low", "medium", "high"]


def test_a_program_that_never_answers_is_killed(conn, progs, tmp_path):
    cfg = json.loads(progs.read_text())
    cfg["claude"]["binary"] = fake_bin(tmp_path, "mute", "import time\ntime.sleep(30)\n")
    progs.write_text(json.dumps(cfg))
    with pytest.raises(TimeoutError):
        modellist.refresh(conn, "claude", timeout=0.5)
    assert modellist.listed(conn, "claude") is None


def test_models_command_shows_what_each_model_offers(conn, progs, capsys):
    assert main(["providers", "models", "claude", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "default  Default (recommended)  (its default)  follows the newest (now claude-opus-5-5)" in out
    assert "  claude-sonnet-4-5  Sonnet\n      everyday\n      effort: low, high\n" in out
    assert "  haiku  Haiku 4.5  follows the newest (now claude-haiku-4-5)\n      quick\n" in out
    assert "suggested" not in out
    assert main(["providers", "models", "codex", "--refresh"]) == 0
    assert "effort: low, medium (default), high" in capsys.readouterr().out


def test_tier_command_is_gone(capsys):
    with pytest.raises(SystemExit):
        main(["providers", "tier", "claude", "fast", "haiku"])
    assert not hasattr(modellist, "suggest") and not hasattr(modellist, "SMALL")


def test_housekeep_skips_a_fresh_list_and_reads_a_stale_one(conn, progs, monkeypatch):
    monkeypatch.setattr(housekeep, "_list_tried", {})
    monkeypatch.setattr(Local, "models", lambda self, timeout=1.5: ["q"])
    modellist.refresh(conn, "claude")
    modellist.refresh(conn, "codex")
    modellist.refresh(conn, "local")
    assert modellist.stale(conn) == []
    assert housekeep._models(conn) == []
    conn.execute("UPDATE provider_models SET read_at=? WHERE provider='local'", (db.now() - 25 * 3600,))
    assert modellist.stale(conn) == ["local"]
    assert housekeep._models(conn) == ["models: reading local's list"]
    for t in threading.enumerate():
        if t.name == "eki-models-local":
            t.join(10)
    assert modellist.read_at(conn, "local") > db.now() - 60
    assert housekeep._models(conn) == []                                   # fresh again


def test_housekeep_skips_a_program_not_installed(conn, progs):
    cfg = json.loads(progs.read_text())
    cfg["claude"]["binary"] = "/nowhere/claude"
    cfg["local"]["off"] = True
    progs.write_text(json.dumps(cfg))
    assert modellist.stale(conn) == ["codex"]


def test_claude_default_is_what_the_program_says():
    m = lambda v, name="", desc="", **kw: {"value": v, "displayName": name, "description": desc, **kw}
    flagged = [m("default", "Default"), m("claude-opus-4-1", "Opus", isDefault=True)]
    assert modellist._claude_default(flagged)["value"] == "claude-opus-4-1"
    alias = [m("claude-opus-4-1", "Opus"), m("default", "Default (recommended)")]
    assert modellist._claude_default(alias)["value"] == "default"
    worded = [m("claude-opus-4-1", "Opus"), m("claude-sonnet-4-5", "Sonnet", "Recommended for most work")]
    assert modellist._claude_default(worded)["value"] == "claude-sonnet-4-5"
    assert modellist._claude_default([m("claude-opus-4-1", "Opus")]) is None
