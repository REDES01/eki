"""`eki verdict`: a judge run files its verdicts; prose never counts."""
import io
import json

from eki import cli, verdicts
from test_verdicts import good, judge_run


def _stdin(monkeypatch, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def test_files_one_with_eki_run_set(conn, monkeypatch, capsys):
    rid = judge_run(conn)
    assert cli.main(["verdict", json.dumps(good())]) == 2           # no EKI_RUN: nobody to file for
    monkeypatch.setenv("EKI_RUN", rid)
    assert cli.main(["verdict", json.dumps(good())]) == 0
    assert "filed c1 passed" in capsys.readouterr().out
    _stdin(monkeypatch, json.dumps(good(check_id="c2")))
    assert cli.main(["verdict", "-"]) == 0
    assert set(verdicts.outcome(conn, rid)) == {"c1", "c2"}
    assert cli.main(["verdict", json.dumps(good())]) == 1           # once per check
    assert "already filed" in capsys.readouterr().err


def test_lines_files_every_verdict_line_and_ignores_prose(conn, monkeypatch, capsys):
    rid = judge_run(conn)
    monkeypatch.setenv("EKI_RUN", rid)
    _stdin(monkeypatch, "I played it. VERDICT: passed, looks great\n"
                        f"VERDICT: {json.dumps(good())}\nsome prose\n"
                        f"VERDICT: {json.dumps(good(check_id='c2'))}\n")
    assert cli.main(["verdict", "--lines"]) == 0
    assert set(verdicts.outcome(conn, rid)) == {"c1", "c2"}
    capsys.readouterr()
    assert cli.main(["verdict", "--checks"]) == 0
    assert "filed: passed" in capsys.readouterr().out


def test_lines_exits_1_on_a_rejected_line_or_none(conn, monkeypatch, capsys):
    rid = judge_run(conn)
    monkeypatch.setenv("EKI_RUN", rid)
    _stdin(monkeypatch, f"VERDICT: {json.dumps(good())}\nVERDICT: {json.dumps(good(check_id='c2', evidence=''))}\n")
    assert cli.main(["verdict", "--lines"]) == 1
    assert "evidence is empty" in capsys.readouterr().err
    assert set(verdicts.outcome(conn, rid)) == {"c1"}
    _stdin(monkeypatch, "all good, nothing to say\n")
    assert cli.main(["verdict", "--lines"]) == 1
    assert "no `VERDICT: {json}` line" in capsys.readouterr().err


def test_checks_lists_what_is_left(conn, monkeypatch, capsys):
    rid = judge_run(conn, checks=("spec", "c1"))
    monkeypatch.setenv("EKI_RUN", rid)
    assert cli.main(["verdict", "--checks"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("spec") and "not filed" in out[0] and "the item's spec" in out[0]
