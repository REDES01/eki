"""stopkind: what kind of stop a failed or lost run is, over real messages."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from eki import stopkind
from eki.stopkind import Failure, Stop

TOKYO = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 10, 12, 22, 0, tzinfo=TOKYO).timestamp()    # a Monday, 22:00 in Tokyo


def at(*args) -> float:
    return datetime(*args, tzinfo=TOKYO).timestamp()


def kind(error, state="failed", n=0, provider="", **kw) -> Stop:
    return stopkind.classify(Failure(state=state, error=error, n=n, provider=provider, now=NOW,
                                     tz="Asia/Tokyo", **kw))


@pytest.fixture
def tokyo(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


QUOTA = [
    ("You've hit your session limit · resets 4:30am (Asia/Tokyo)", at(2026, 10, 13, 4, 30), False),
    ("You've hit your weekly limit · resets Oct 16, 9am (Asia/Tokyo)", at(2026, 10, 16, 9, 0), True),
    ("You've hit your weekly limit · resets Thu 9am (Asia/Tokyo)", at(2026, 10, 15, 9, 0), True),
    ("You've hit your usage limit. Upgrade to Plus to continue using Codex "
     "(https://openai.com/chatgpt/pricing), or try again at 11:30 PM.", at(2026, 10, 12, 23, 30), False),
    ("You've hit your usage limit. Upgrade to Pro (https://openai.com/chatgpt/pricing), "
     "or try again at Oct 16th, 2026 9:43 AM.", at(2026, 10, 16, 9, 43), True),
    ("You've hit your usage limit. Upgrade to Pro (https://openai.com/chatgpt/pricing) "
     "or try again in 3 days 2 hours 5 minutes.", NOW + 3 * 86400 + 2 * 3600 + 300, True),
]


@pytest.mark.parametrize("text,when,long_wait", QUOTA)
def test_quota_reads_the_reset_time(text, when, long_wait):
    s = kind(text)
    assert (s.kind, s.retry_at, s.long_wait) == ("quota", when, long_wait)
    assert "resets" in s.fix


def test_quota_fix_says_when_it_resets():
    assert kind(QUOTA[0][0]).fix == "session limit resets 4:30am Asia/Tokyo"


def test_a_time_with_no_zone_is_in_tz_and_a_past_one_rolls_over():
    s = kind("You've hit your usage limit, try again at 9:00 AM.")
    assert s.retry_at == at(2026, 10, 13, 9, 0)


def test_reported_reset_wins_and_long_wait_counts_from_since():
    s = kind("usage limit reached", state="limited", reset_at=NOW + 3600, since=NOW - 12 * 3600)
    assert (s.kind, s.retry_at, s.long_wait) == ("quota", NOW + 3600, True)


def test_a_limit_with_no_time_backs_off_and_caps_at_8h():
    waits = [kind("Claude AI usage limit reached", state="limited", n=n).retry_at - NOW for n in range(7)]
    assert waits == [1800, 3600, 7200, 14400, 28800, 28800, 28800]


ENVIRONMENT = [
    ("Invalid API key · Please run /login", "claude", "/login"),
    ("API Error: 401 {\"type\":\"error\",\"error\":{\"type\":\"authentication_error\"}}", "claude", "/login"),
    ("Error: Not logged in. Run `codex login` first.", "codex", "codex login"),
    ("stream error: not logged in; run codex login", "", "codex login"),
    ("FileNotFoundError: [Errno 2] No such file or directory: '/Users/x/.eki/work.noindex/ab12/wt'",
     "claude", "folder /Users/x/.eki/work.noindex/ab12/wt is missing"),
    ("PermissionError: [Errno 1] Operation not permitted: '/Users/x/Documents'", "claude", "Operation not permitted"),
    ("FileNotFoundError: [Errno 2] No such file or directory: 'claude'", "claude", "`claude`"),
    ("zsh: command not found: codex", "codex", "`codex`"),
    ("Error: unknown provider 'openrouter' in config", "codex", "config"),
    ("API Error: 404 model: claude-nope does not exist", "claude", "config"),
]


@pytest.mark.parametrize("text,provider,names", ENVIRONMENT)
def test_a_broken_setup_is_environment_and_names_the_fix(text, provider, names):
    s = kind(text, provider=provider)
    assert (s.kind, s.retry_at) == ("environment", None)
    assert names in s.fix


def test_the_same_breakage_gives_the_same_fix():
    one = kind("[pid 4312] 2026-10-12T10:00:01Z FileNotFoundError: [Errno 2] No such file or directory: '/x/wt'")
    two = kind("[pid 977] 2026-10-13T03:12:44Z FileNotFoundError: [Errno 2] No such file or directory: '/x/wt'")
    assert one.fix == two.fix == "folder /x/wt is missing — restore it or `eki project …`"
    a = kind("12:00:01 Invalid API key · Please run /login (request req_01ab)", provider="claude")
    b = kind("18:30:09 Invalid API key · Please run /login (request req_99zz)", provider="claude")
    assert a.fix == b.fix == "Claude Code isn't signed in — run `claude` and /login"


TRANSIENT = [
    "API Error: Connection dropped (ECONNRESET)",
    'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    "API Error: 529 overloaded",
    "API Error: 500 Internal server error",
    "request to https://api.anthropic.com/v1/messages failed, reason: socket hang up",
    "TypeError: fetch failed",
    "connect ETIMEDOUT 160.79.104.10:443",
    "stream error: network error",
    'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nRuntimeError: boom',
    "claude exited 1",
    "Segmentation fault: 11",
]


@pytest.mark.parametrize("text", TRANSIENT)
def test_a_passing_fault_is_transient_then_judgment(text):
    waits = [kind(text, n=n) for n in range(4)]
    assert [s.kind for s in waits] == ["transient"] * 3 + ["judgment"]
    assert [s.retry_at - NOW for s in waits[:3]] == list(stopkind.TRANSIENT_WAITS)


def test_didnt_come_up_is_transient_even_when_limited():
    assert kind("didn't come up", state="limited").kind == "transient"


JUDGMENT = [
    "", "fake failure", "checks failed", "no output for 600s",
    "FAILED tests/test_x.py::test_y - assert 1 == 2\n1 failed, 200 passed in 31.2s",
    "E   FileNotFoundError: [Errno 2] No such file or directory: '/tmp/x'\n2 failed, 9 passed",
]


@pytest.mark.parametrize("text", JUDGMENT)
def test_everything_else_is_judgment(text):
    assert kind(text) == Stop("judgment", None, "")


def test_lost_is_free_until_the_cap_then_transient_then_judgment():
    for n in range(stopkind.LOST_CAP):
        assert kind("", state="lost", n=n) == Stop("lost", NOW, "")
    assert kind("", state="lost", n=10) == Stop("transient", NOW + 60, "")
    assert kind("", state="lost", n=13).kind == "judgment"


def test_record_carries_n_and_since_within_a_kind():
    q = kind("usage limit reached", state="limited")
    one = stopkind.record(None, q, NOW)
    assert (one["kind"], one["n"], one["since"], one["fix"]) == ("quota", 1, NOW, q.fix)
    two = stopkind.record(one, q, NOW + 100)
    assert (two["n"], two["since"]) == (2, NOW)
    other = stopkind.record(two, kind("socket hang up"), NOW + 200)
    assert (other["kind"], other["n"], other["since"]) == ("transient", 1, NOW + 200)


def test_losses_past_the_cap_keep_counting_and_end():
    rec, kinds = None, []
    for i in range(14):
        s = stopkind.after(rec, Failure(state="lost", now=NOW + i))
        kinds.append(s.kind)
        rec = stopkind.record(rec, s, NOW + i)
    assert kinds == ["lost"] * 10 + ["transient"] * 3 + ["judgment"]


def test_after_counts_only_the_same_kind():
    rec = stopkind.record(None, Stop("quota", NOW, "", False), NOW)
    rec = dict(rec, n=4)
    assert stopkind.after(rec, Failure(state="failed", error="socket hang up", now=NOW)).retry_at == NOW + 60
    assert stopkind.after(rec, Failure(state="limited", error="usage limit", now=NOW)).retry_at == NOW + 28800


def test_describe_gives_one_line(tokyo):
    q = stopkind.record(None, kind(QUOTA[0][0]), NOW)
    assert stopkind.describe(dict(q, provider="claude"), NOW) == \
        "quota — waits for claude, retries 04:30 (session limit resets 4:30am Asia/Tokyo)"
    t = dict(stopkind.record(None, Stop("transient", at(2026, 10, 12, 14, 5), ""), NOW), n=2)
    assert stopkind.describe(t, NOW) == "transient — try 2 of 3 at 14:05"
    e = stopkind.record(None, kind("Invalid API key · Please run /login", provider="claude"), NOW)
    hold = dict(e, retry_at=at(2026, 10, 12, 15, 0), run="r1")
    assert stopkind.describe(hold, NOW) == \
        "environment — fix: Claude Code isn't signed in — run claude and /login; rechecks 15:00"
    assert stopkind.describe(dict(kind("", state="lost").__dict__, n=3), NOW) == "lost — resumed (3 in a row)"
    assert stopkind.describe({"kind": "judgment", "n": 1}, NOW).startswith("judgment")
