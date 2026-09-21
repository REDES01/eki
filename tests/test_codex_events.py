"""Parser tests written against events captured from a real run.

The first parser for this stream was written from guesswork and matched
nothing — so these fixtures are verbatim lines from codex-cli 0.154.0, and the
non-fatal `error` item is the one that run actually emitted alongside a
perfectly good answer.
"""
import json

import pytest

from hub.adapters import _codex_events as events

# verbatim from a successful `codex exec --json` run
REAL_RUN = [
    '{"type":"thread.started","thread_id":"01a0c0c1-9e35-7530-9912-54626303388f"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"error","message":'
    '"Code Mode is unavailable because failed to spawn code-mode host."}}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message",'
    '"text":"CODEX OK"}}',
    '{"type":"turn.completed","usage":{"input_tokens":13490,"output_tokens":7}}',
]


def parsed():
    return [json.loads(line) for line in REAL_RUN]


def test_session_id_from_thread_started():
    assert events.session_id(parsed()[0]) == "01a0c0c1-9e35-7530-9912-54626303388f"


def test_session_id_absent_elsewhere():
    assert all(events.session_id(e) is None for e in parsed()[1:])


def test_real_run_yields_exactly_the_answer():
    texts = [t for t, _ in map(events.read, parsed()) if t]
    assert texts == ["CODEX OK"]


def test_non_fatal_error_item_is_reported_but_not_as_text():
    text, err = events.read(parsed()[1])
    assert text is None
    assert "Code Mode is unavailable" in err


def test_turn_events_are_silent():
    assert events.read(json.loads('{"type":"turn.started"}')) == (None, None)
    assert events.read(parsed()[4]) == (None, None)


@pytest.mark.parametrize("event,expected", [
    ({"type": "agent_message", "text": "hi"}, "hi"),
    ({"type": "assistant_message", "message": "hi"}, "hi"),
    ({"type": "agent_message.delta", "delta": "hi"}, "hi"),
    ({"type": "response.output_text.delta", "delta": {"text": "hi"}}, "hi"),
])
def test_older_and_newer_shapes_still_read(event, expected):
    assert events.read(event)[0] == expected


@pytest.mark.parametrize("event", [
    {},
    {"type": "item.started", "item": {"type": "agent_message"}},
    {"type": "item.completed", "item": {"type": "command_execution"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": ""}},
    {"type": "something.we.have.never.seen"},
])
def test_unknown_or_empty_is_ignored_not_guessed(event):
    assert events.read(event) == (None, None)


def test_top_level_error():
    assert events.read({"type": "error", "message": "boom"})[1] == "boom"


def test_long_error_is_truncated():
    _, err = events.read({"type": "error", "message": "x" * 900})
    assert len(err) == 300
