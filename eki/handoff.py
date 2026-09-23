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

A real tool call first: a server that speaks OpenAI's `tools` (mlx_lm.server)
is offered one function, `hand_off(target, brief)`, and the model calls it the
way it was trained to — structured, and wherever it lands in the answer, even
after a sentence ("I'll look at the project first…"). A server without tool
calling gets the marker instead, the same way a local model asks eki for a
skill (`[[skill:name]]`); it is caught anywhere in the answer too, not only
at its start (a sentence before it hid three goal runs, 2026-09-24).
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .adapters.base import ToolCall

PREFIX = "[[handoff:"
TOOL = "hand_off"
MARK = re.compile(r"\[\[handoff:\s*([A-Za-z0-9_.-]*)\s*(?:\|\s*(.*?))?\]\]", re.S)


class HandOff(Exception):
    """The model handed the request over: to `target` ("" = eki picks), with `brief`."""

    def __init__(self, target: str, brief: str):
        super().__init__(f"handoff to {target or 'a harness'}")
        self.target, self.brief = target, brief


def tool(targets: List[Tuple[str, str]]) -> Dict[str, Any]:
    """The one function a model without tools is given, in OpenAI's shape."""
    names = [key for key, _ in targets]
    return {"type": "function", "function": {
        "name": TOOL,
        "description": ("Hand this request to one that has tools — files, commands, the screen, the "
                        "web, building software. Call it instead of saying what you would do."),
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "enum": names,
                       "description": "; ".join(f"{k}: {what}" for k, what in targets)},
            "brief": {"type": "string",
                      "description": ("A few sentences in the person's language: what they want, "
                                      "and anything already agreed in this conversation.")}},
            "required": ["target", "brief"]}}}


def instructions(targets: List[Tuple[str, str]], as_tool: bool = False) -> str:
    """The system prompt a model without tools gets: what it can't do, and
    the one thing it can do about that."""
    rows = "\n".join(f"- {key}: {what}" for key, what in targets)
    head = (
        "You are answering on this Mac without any tools: you cannot read or change files, run "
        "commands, look at the screen, use the web, or build and run software. Answer whatever "
        "you can answer well yourself — questions, explanations, writing, translation, rewrites "
        "of what you wrote.\n\n"
        "When the request needs any of those tools, or is more than you can do well (a real "
        "program, an app or game, a change to someone's project, current facts), don't attempt "
        "it and don't pretend to have done it. ")
    if as_tool:
        return head + (
            f"Hand it over instead: call {TOOL} right away, without writing anything first and "
            "without asking whether you should. The brief is a few sentences in the person's "
            "language: what they want, and anything already agreed in this conversation that "
            f"they'll need.\n\nWho can take it:\n{rows}")
    return head + (
        "Hand it over instead: reply with exactly\n"
        "[[handoff: <one of the names below> | <a brief for them>]]\n"
        "and nothing else. The brief is a few sentences in the person's language: what they "
        "want, and anything already agreed in this conversation that they'll need.\n\n"
        f"Who can take it:\n{rows}")


def called(call: ToolCall) -> Optional[HandOff]:
    """The hand_off call, as a HandOff; any other call is not ours."""
    if call.name != TOOL:
        return None
    args = call.arguments or {}
    brief = args.get("brief") or call.raw or ""
    return HandOff(str(args.get("target") or ""), " ".join(str(brief).split()))


def parse(text: str) -> Optional[HandOff]:
    """The marker, wherever it is in `text`."""
    m = MARK.search(text or "")
    if not m:
        return None
    return HandOff(m.group(1) or "", " ".join((m.group(2) or "").split()))


def _partial(text: str, word: str) -> int:
    """How much of the end of `text` could still become `word`."""
    for n in range(min(len(text), len(word) - 1), 0, -1):
        if word.startswith(text[-n:]):
            return n
    return 0


def _scan(buf: str, in_think: bool, final: bool) -> Tuple[str, str, bool, Optional[HandOff]]:
    """Pass on what can't be part of a marker; keep back what might be.
    Returns (text to pass on, text kept back, inside <think>?, a handoff)."""
    out: List[str] = []
    while buf:
        if in_think:
            i = buf.find("</think>")
            if i < 0:
                keep = 0 if final else _partial(buf, "</think>")
                out.append(buf[:len(buf) - keep])
                buf = buf[len(buf) - keep:]
                break
            out.append(buf[:i + 8])
            buf, in_think = buf[i + 8:], False
            continue
        t, m = buf.find("<think>"), buf.find(PREFIX)
        if t >= 0 and (m < 0 or t < m):
            out.append(buf[:t + 7])
            buf, in_think = buf[t + 7:], True
            continue
        if m >= 0:
            out.append(buf[:m])
            rest = buf[m:]
            if "]]" in rest or final:
                got = parse(rest if "]]" in rest else rest + "]]")
                if got is not None:
                    return "".join(out), "", in_think, got
                # "[[handoff:" that isn't one: pass it on and look further
                out.append(rest[:len(PREFIX)])
                buf = rest[len(PREFIX):]
                continue
            return "".join(out), rest, in_think, None
        keep = 0 if final else max(_partial(buf, PREFIX), _partial(buf, "<think>"))
        out.append(buf[:len(buf) - keep])
        buf = buf[len(buf) - keep:]
        break
    return "".join(out), buf, in_think, None


async def watch(stream: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Pass a model's answer through, and raise HandOff when it hands over:
    by calling hand_off, or by the marker anywhere outside its thinking —
    only a possible marker's first characters are ever held back. What it
    said before handing over has been passed on; the harness's answer
    follows it."""
    buf, in_think = "", False
    async for chunk in stream:
        if isinstance(chunk, ToolCall):
            got = called(chunk)
            if got is not None:
                if buf:
                    yield buf
                raise got
            yield chunk
            continue
        if not isinstance(chunk, str):
            yield chunk
            continue
        out, buf, in_think, got = _scan(buf + chunk, in_think, final=False)
        if out:
            yield out
        if got is not None:
            raise got
    out, buf, in_think, got = _scan(buf, in_think, final=True)
    if out:
        yield out
    if got is not None:
        raise got
