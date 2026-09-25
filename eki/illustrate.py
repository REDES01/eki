# SPDX-License-Identifier: Apache-2.0
"""A model with no tools of its own can ask for a picture mid-answer
(ROADMAP, Stage 6).

Claude Code and Codex already ask eki for a picture themselves — `eki image`
from a shell, `eki_image` among their tools (Stage 4). A local model
answering in a chat has neither, so eki gives it the same request as words:
where a picture belongs in what it is writing, it writes

    [[image: a lighthouse at dusk, oil painting, warm light]]

and carries on writing. eki holds the answer at that point, has the image
model draw it — a run of its own under this one, the same one `eki_image`
starts — and puts the picture in the answer where the marker was. What
comes after the marker follows the picture. A picture that can't be made
leaves a short line in its place; the answer is never lost for it.

Words rather than a function call on purpose: a call ends the model's turn,
and a picture in the middle of an answer should not.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional, Tuple

from .handoff import _partial

PREFIX = "[[image:"
MARK = re.compile(r"\[\[image:\s*(.*?)\]\]", re.S)
#: pictures one answer may ask for; past this the markers are dropped
MOST = 4


def instructions() -> str:
    """What the model is told, beside what it can't do."""
    return (
        "You can put a picture in your answer. Where one would help — the person asked for a "
        "picture, an illustration, a scene or a character to look at — write, on a line of its "
        "own,\n[[image: <what the picture shows, its style and mood, in English, one or two "
        "sentences>]]\nand carry on writing. eki draws it with the image model on this Mac and "
        "puts it there. Don't describe the picture again after it, don't write a link or a file "
        f"name, and use no more than {MOST} in one answer. Most answers need none.")


def _scan(buf: str, in_think: bool, final: bool) -> Tuple[str, str, bool, Optional[str]]:
    """Pass on what can't be part of a marker; keep back what might be.
    Returns (text to pass on, text kept back, inside <think>?, a picture
    asked for — its words; the text after it is what was kept back)."""
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
            got = MARK.match(rest)
            if got:
                return "".join(out), rest[got.end():], in_think, " ".join(got.group(1).split())
            if final:
                # never closed: the words were meant for a picture, not the reader
                return "".join(out), "", in_think, " ".join(rest[len(PREFIX):].split())
            return "".join(out), rest, in_think, None
        keep = 0 if final else max(_partial(buf, PREFIX), _partial(buf, "<think>"))
        out.append(buf[:len(buf) - keep])
        buf = buf[len(buf) - keep:]
        break
    return "".join(out), buf, in_think, None


async def watch(stream: AsyncIterator[Any], draw: Callable[[str], Awaitable[str]]
                ) -> AsyncIterator[Any]:
    """Pass a model's answer through, drawing each picture it asks for:
    `draw(words)` gives what goes in the marker's place (the picture, as
    markdown, or a line saying why not). Anything that isn't text passes
    through untouched."""
    buf, in_think, asked = "", False, 0

    async def picture(words: str) -> str:
        nonlocal asked
        asked += 1
        if not words or asked > MOST:
            return ""
        return await draw(words)

    async for chunk in stream:
        if not isinstance(chunk, str):
            yield chunk
            continue
        buf += chunk
        while True:
            out, buf, in_think, words = _scan(buf, in_think, final=False)
            if out:
                yield out
            if words is None:
                break
            made = await picture(words)
            if made:
                yield made
    while True:
        out, buf, in_think, words = _scan(buf, in_think, final=True)
        if out:
            yield out
        if words is None:
            break
        made = await picture(words)
        if made:
            yield made
