"""Model lists: each program's own list into provider_models, tiers suggested, never written."""
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
                {"value": "default", "displayName": "Default (recommended)", "description": "Opus"},
                {"value": "claude-sonnet-4-5", "displayName": "Sonnet", "description": "everyday"},
                {"value": "claude-opus-4-1", "displayName": "Opus", "description": "hardest"},
                {"value": "haiku", "displayName": "Haiku 4.5", "description": "quick"}]}}}), flush=True)
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
            {"id": "gpt-5-codex", "displayName": "GPT-5 Codex", "isDefault": True},
            {"id": "gpt-5", "displayName": "GPT-5", "isDefault": False},
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


def test_claude_list_from_the_initialize_reply(conn, progs):
    before = progs.read_text()
    assert modellist.listed(conn, "claude") is None
    rows = modellist.refresh(conn, "claude")
    assert [r["model"] for r in rows] == ["default", "claude-sonnet-4-5", "claude-opus-4-1", "haiku"]
    got = modellist.listed(conn, "claude")
    assert got[0] == {"model": "default", "label": "Default (recommended)", "is_default": True}
    assert modellist.read_at(conn, "claude") is not None
    assert modellist.suggest("claude", "claude_code", got) == {
        "fast": "haiku", "default": "claude-sonnet-4-5", "top": "claude-opus-4-1"}
    assert progs.read_text() == before


def test_codex_list_from_model_list(conn, progs, tmp_path):
    before = progs.read_text()
    modellist.refresh(conn, "codex")
    assert (tmp_path / "codex.initialized").exists()
    got = modellist.listed(conn, "codex")
    assert [(m["model"], m["is_default"]) for m in got] == [
        ("gpt-5-codex", True), ("gpt-5", False), ("gpt-5-codex-mini", False)]
    assert modellist.suggest("codex", "codex", got) == {
        "default": "gpt-5-codex", "fast": "gpt-5-codex-mini", "top": "gpt-5"}
    assert progs.read_text() == before


def test_local_list_and_no_suggestion(conn, progs, monkeypatch):
    monkeypatch.setattr(Local, "models", lambda self, timeout=1.5: ["qwen3-30b", "gemma"])
    modellist.refresh(conn, "local")
    got = modellist.listed(conn, "local")
    assert [m["model"] for m in got] == ["qwen3-30b", "gemma"]
    assert modellist.suggest("local", "local", got) == {}


def test_refresh_replaces_only_that_providers_rows(conn, progs, monkeypatch):
    monkeypatch.setattr(Local, "models", lambda self, timeout=1.5: ["a", "b"])
    modellist.refresh(conn, "local")
    modellist.refresh(conn, "claude")
    monkeypatch.setattr(Local, "models", lambda self, timeout=1.5: ["c"])
    modellist.refresh(conn, "local")
    assert [m["model"] for m in modellist.listed(conn, "local")] == ["c"]
    assert len(modellist.listed(conn, "claude")) == 4


def test_a_program_that_never_answers_is_killed(conn, progs, tmp_path):
    cfg = json.loads(progs.read_text())
    cfg["claude"]["binary"] = fake_bin(tmp_path, "mute", "import time\ntime.sleep(30)\n")
    progs.write_text(json.dumps(cfg))
    with pytest.raises(TimeoutError):
        modellist.refresh(conn, "claude", timeout=0.5)
    assert modellist.listed(conn, "claude") is None


def test_tier_command_writes_models_and_refuses_unlisted(conn, progs, capsys):
    assert main(["providers", "tier", "claude", "fast", "haiku"]) == 1     # never read yet
    modellist.refresh(conn, "claude")
    assert main(["providers", "tier", "claude", "fast", "haiku"]) == 0
    assert main(["providers", "tier", "claude", "top", "suggested"]) == 0
    assert main(["providers", "tier", "claude", "top", "claude-opus-9"]) == 1
    assert main(["providers", "tier", "claude", "huge", "haiku"]) == 1
    entry = json.loads(progs.read_text())["claude"]
    assert entry["models"] == {"fast": "haiku", "top": "claude-opus-4-1"}
    assert entry["model"] == "sonnet" and entry["binary"]                  # the rest kept
    assert "isn't in claude's list" in capsys.readouterr().err


def test_models_command_lists_picked_and_suggested(conn, progs, capsys):
    assert main(["providers", "models", "claude", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "default  sonnet   (suggested claude-sonnet-4-5)" in out
    assert "fast     -   (suggested haiku)" in out
    assert "Default (recommended)  (its default)" in out


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
