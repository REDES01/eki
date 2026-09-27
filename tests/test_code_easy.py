"""A `code` request narrowed to `code-easy` (rung 2) by one more prompt check."""
import json
import sys

import pytest

from eki import cli, providers, routing, store
from eki.routing.check import check, narrow
from eki.routing import table


class Judge:
    """A checker that answers with scripted text, and remembers what it was asked."""
    def __init__(self, text="", fail=False):
        self.text, self.fail, self.asked = text, fail, []
        self.cfg = {}

    def available(self):
        return True, ""

    def complete(self, messages, **kw):
        self.asked.append(messages)
        if self.fail:
            raise OSError("connection refused")
        return {"text": self.text}


@pytest.fixture
def rung2(home, monkeypatch):
    cfg = json.loads((home / "providers.json").read_text())
    cfg.update({"lm": {"kind": "local", "base_url": "http://127.0.0.1:1", "model": "small"},
                "claude": {"kind": "fake"}, "codex": {"kind": "fake"},
                "codex-local": {"kind": "codex_local", "local": "lm", "binary": sys.executable}})
    (home / "providers.json").write_text(json.dumps(cfg))
    got = json.loads((home / "routing.json").read_text())
    got["checker"] = "lm"
    got["rows"] = [{"key": "general", "title": "all", "targets": ["fake"]},
                   {"key": "code", "title": "tools", "needs": ["tools"], "targets": ["claude", "codex"]}]
    (home / "routing.json").write_text(json.dumps(got))
    return home


def judge(monkeypatch, j):
    real = providers.get
    monkeypatch.setattr(providers, "get", lambda name: j if name == "lm" else real(name))
    return j


def ask(conn, prompt, cwd, **kw):
    tid = store.create_thread(conn, prompt[:20], cwd)
    rid = store.create_run(conn, tid, prompt, **kw)
    return routing.decide(conn, store.run(conn, rid))


def test_easy_goes_to_codex_local_with_the_full_why(rung2, conn, tmp_path, monkeypatch):
    j = judge(monkeypatch, Judge('{"row": "code-easy", "why": "a rename"}'))
    d = ask(conn, "rename parse to parse_line", str(tmp_path))
    assert (d.provider, d.row) == ("codex-local", "code-easy")
    assert d.why == "rule: has a folder → code; check: easy → code-easy → codex-local"
    menu = j.asked[0][0]["content"]
    assert "code-easy:" in menu and "- code:" in menu


def test_not_easy_stays_on_code(rung2, conn, tmp_path, monkeypatch):
    judge(monkeypatch, Judge('{"row": "code", "why": "a feature"}'))
    d = ask(conn, "add oauth login", str(tmp_path))
    assert (d.provider, d.row) == ("claude", "code")
    assert d.why == "rule: has a folder → code; check: not easy → code → claude"


@pytest.mark.parametrize("j", [Judge(fail=True), Judge("no idea"), Judge('{"row": "web"}')])
def test_a_check_that_cant_run_stays_on_code(rung2, conn, tmp_path, monkeypatch, j):
    judge(monkeypatch, j)
    d = ask(conn, "rename parse to parse_line", str(tmp_path))
    assert (d.provider, d.row) == ("claude", "code")
    assert d.why == "rule: has a folder → code → claude"


def test_no_checker_stays_on_code(rung2, conn, tmp_path):
    got = json.loads((rung2 / "routing.json").read_text())
    got["checker"] = "none"
    (rung2 / "routing.json").write_text(json.dumps(got))
    assert narrow("rename x", checker="none") is None
    d = ask(conn, "rename parse to parse_line", str(tmp_path))
    assert d.row == "code" and d.provider == "claude"


def test_a_preset_row_is_never_narrowed(rung2, conn, tmp_path, monkeypatch):
    j = judge(monkeypatch, Judge('{"row": "code-easy", "why": "tiny"}'))
    d = ask(conn, "rename parse to parse_line", str(tmp_path), row="code")
    assert d.row == "code" and d.provider == "claude"
    assert j.asked == []


def test_the_general_check_never_offers_code_easy(rung2, monkeypatch):
    j = judge(monkeypatch, Judge('{"row": "code-easy", "why": "tiny"}'))
    key, why = check("what's a good name for a cat", checker="lm")
    assert key == "general" and "gave no row" in why
    assert "code-easy" not in j.asked[0][0]["content"]


def test_without_codex_local_there_is_no_row_and_no_narrowing(home, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "codex_installed", lambda cfg: False)
    j = judge(monkeypatch, Judge('{"row": "code-easy", "why": "tiny"}'))
    assert "code-easy" not in {r["key"] for r in table.rows()}
    assert narrow("rename x", checker="lm") is None
    d = ask(conn, "rename parse to parse_line", str(tmp_path))
    assert d.row == "code" and d.why == "rule: has a folder → code → fake"
    assert j.asked == []


def test_explain_and_eki_route_show_the_step(rung2, conn, tmp_path, monkeypatch, capsys):
    judge(monkeypatch, Judge('{"row": "code-easy", "why": "a flag"}'))
    e = routing.explain(conn, "add a --verbose flag", cwd=str(tmp_path))
    assert e["row"] == "code-easy" and e["why"] == "rule: has a folder → code; check: easy"
    assert [t["name"] for t in e["targets"]] == ["codex-local", "claude", "codex"]
    assert cli.main(["route", "-C", str(tmp_path), "add a verbose flag"]) == 0
    out = capsys.readouterr().out
    assert "why:   rule: has a folder → code; check: easy" in out
    assert "row:   code-easy — A small, well-defined code change" in out
