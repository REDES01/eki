import pytest

from eki import transient

TRANSIENT = [
    "API Error: Connection dropped (ECONNRESET)",
    'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    "API Error: 500 Internal server error",
    "API Error: 503 upstream connect error",
    "request to https://api.anthropic.com/v1/messages failed, reason: socket hang up",
    "TypeError: fetch failed",
    "connect ETIMEDOUT 160.79.104.10:443",
    "stream error: network error",
    "API Error: Request rejected (429) · rate limited",
    "api error: connection reset by peer",
]

LASTING = [
    "",
    None,
    "fake failure",
    'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n'
    "ValueError: bad value",
    "no output for 600s",
    "checks failed",
    "API Error: 400 invalid_request_error",
    "API Error: 401 authentication",
    "exit 5000 while building",
]


@pytest.mark.parametrize("text", TRANSIENT)
def test_transient_errors(text):
    assert transient.looks_transient(text)


@pytest.mark.parametrize("text", LASTING)
def test_lasting_errors(text):
    assert not transient.looks_transient(text)


def test_backoff_then_give_up():
    assert [transient.delay(n) for n in range(4)] == [30, 120, 300, None]
    assert transient.delay(10) is None
