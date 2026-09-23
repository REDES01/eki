# SPDX-License-Identifier: Apache-2.0
"""A model with no tools of its own can hand the thread to one that has them
(docs/routing.md).

eki is a light router that makes full use of the harnesses that exist —
Claude Code, Codex. A thread stays with the model that is answering; the
only thing a model without tools gets from eki is one: hand this over.
Asked to write a haiku, the local model writes it; asked to make it about
snow, it does, in a couple of seconds, with the haiku in its context. Asked
to build a game around it, it answers

    [[handoff: claude_code | They want a small RPG built around the haiku …]]

and eki moves the thread to Claude Code with that brief, so it starts from
what was said instead of from nothing.

The marker, not the local server's tool calling: the same way a local model
asks eki for a skill (`[[skill:name]]`), and it works on any server.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

PREFIX = "[[handoff:"
MARK = re.compile(r"^\s*\[\[handoff:\s*([A-Za-z0-9_.-]*)\s*(?:\|\s*(.*?))?\]\]", re.S)


class HandOff(Exception):
    """The model handed the request over: to `target` ("" = eki picks), with `brief`."""

    def __init__(self, target: str, brief: str):
        super().__init__(f"handoff to {target or 'a harness'}")
        self.target, self.brief = target, brief


def instructions(targets: List[Tuple[str, str]]) -> str:
    """The system prompt a model without tools gets: what it can't do, and
    the one thing it can do about that."""
    rows = "\n".join(f"- {key}: {what}" for key, what in targets)
    return (
        "You are answering on this Mac without any tools: you cannot read or change files, run "
        "commands, look at the screen, use the web, or build and run software. Answer whatever "
        "you can answer well yourself — questions, explanations, writing, translation, rewrites "
        "of what you wrote.\n\n"
        "When the request needs any of those tools, or is more than you can do well (a real "
        "program, an app or game, a change to someone's project, current facts), don't attempt "
        "it and don't pretend to have done it. Hand it over instead: reply with exactly\n"
        "[[handoff: <one of the names below> | <a brief for them>]]\n"
        "and nothing else. The brief is a few sentences in the person's language: what they "
        "want, and anything already agreed in this conversation that they'll need.\n\n"
        f"Who can take it:\n{rows}")


def could_be(text: str) -> bool:
    """Whether the start of an answer may still turn into the marker."""
    t = (text or "").lstrip()
    if not t:
        return True
    if len(t) < len(PREFIX):
        return PREFIX.startswith(t)
    return t.startswith(PREFIX) and "]]" not in t


def parse(text: str) -> Optional[HandOff]:
    m = MARK.match(text or "")
    if not m:
        return None
    return HandOff(m.group(1) or "", " ".join((m.group(2) or "").split()))


async def watch(stream: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Pass a model's answer through, holding back its start until it's
    clearly not a handoff; raise HandOff when it is. Thinking (<think>…)
    and events pass untouched."""
    held = ""
    deciding = True
    in_think = False
    async for chunk in stream:
        if not deciding or not isinstance(chunk, str):
            yield chunk
            continue
        if in_think:
            yield chunk
            if "</think>" in chunk:
                in_think = False
                rest = chunk.split("</think>", 1)[1]
                # what followed the tag in this chunk was already passed on
                if rest.strip():
                    deciding = False
            continue
        held += chunk
        t = held.lstrip()
        if t.startswith("<think>"):
            if "</think>" not in t:
                yield held
                held, in_think = "", True
                continue
            head, _, after = held.partition("</think>")
            yield head + "</think>"
            held = after
            if not held.strip():
                held = ""
                continue
        elif t and "<think>".startswith(t):
            continue
        if could_be(held):
            got = parse(held)
            if got is not None:
                raise got
            continue
        got = parse(held)
        if got is not None:
            raise got
        deciding = False
        yield held
        held = ""
    if held:
        got = parse(held)
        if got is not None:
            raise got
        yield held
