"""`eki notify`: the settings shown with the topic masked, the topic set and cleared, a test push."""
import json

import pytest

from eki import cli, notify

SECRET = "xz-phone-s3cret_topic"


def _routing(home):
    return json.loads((home / "routing.json").read_text())


@pytest.fixture
def sent(monkeypatch):
    got = []

    def fake(kind, title, body, click=None):
        got.append((kind, title, body, click))
        return fake.ok
    fake.ok = True
    monkeypatch.setattr(notify, "send", fake)
    return got, fake


def _out(capsys):
    o = capsys.readouterr()
    return o.out + o.err


def test_show_off_by_default(home, capsys):
    assert cli.main(["notify"]) == 0
    out = _out(capsys)
    assert "notify: off" in out and "https://ntfy.sh" in out and "goal_done, needs_you" in out


def test_topic_set_masks_and_keeps_other_keys(home, capsys):
    before = _routing(home)
    before["self"] = {"keep": 1}
    before["notify"] = {"server": "https://ntfy.example", "events": ["needs_you"]}
    (home / "routing.json").write_text(json.dumps(before))
    assert cli.main(["notify", "topic", SECRET]) == 0
    after = _routing(home)
    assert after["notify"] == {"server": "https://ntfy.example", "events": ["needs_you"], "topic": SECRET}
    assert after["rows"] == before["rows"] and after["self"] == {"keep": 1} and after["checker"] == "none"
    assert cli.main(["notify"]) == 0
    out = _out(capsys)
    assert "notify: on" in out and notify.masked(SECRET) in out and "needs_you" in out
    assert SECRET not in out
    assert not list(home.glob(".routing.json.tmp-*"))


@pytest.mark.parametrize("argv", [["notify", "topic", ""], ["notify", "off"]])
def test_topic_clear(home, capsys, argv):
    assert cli.main(["notify", "topic", SECRET]) == 0
    assert cli.main(argv) == 0
    after = _routing(home)
    assert after["notify"]["topic"] == "" and after["rows"]
    out = _out(capsys)
    assert "notify: off" in out and SECRET not in out


@pytest.mark.parametrize("bad", ["has space", "a/b", "x" * 65, "ü"])
def test_bad_topic_refused(home, capsys, bad):
    before = (home / "routing.json").read_text()
    assert cli.main(["notify", "topic", bad]) != 0
    assert (home / "routing.json").read_text() == before
    assert "1–64" in _out(capsys)


def test_topic_needs_a_name(home, capsys):
    assert cli.main(["notify", "topic"]) != 0


def test_test_when_off(home, capsys, sent):
    got, _ = sent
    assert cli.main(["notify", "test"]) == 1
    assert "not sent — off" in _out(capsys) and got == []


def test_test_sent(home, capsys, sent):
    got, _ = sent
    cli.main(["notify", "topic", SECRET])
    assert cli.main(["notify", "test"]) == 0
    out = _out(capsys)
    assert out.rstrip().endswith("sent") and SECRET not in out
    assert got == [("test", "eki · test", "a test from eki", None)]


def test_test_failed(home, capsys, sent):
    _, fake = sent
    fake.ok = False
    cli.main(["notify", "topic", SECRET])
    assert cli.main(["notify", "test"]) == 1
    out = _out(capsys)
    assert "not sent — failed" in out and SECRET not in out
