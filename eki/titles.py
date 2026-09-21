# SPDX-License-Identifier: Apache-2.0
"""A name for a thread, instead of its first line.

The first message makes a poor title — "hi", "one sentence: what does the
word eki mean in Japanese?" — so once a thread has an answer in it, a small
local model is asked for a few words that say what it's about. Local only:
a title isn't worth a paid call, and the model that labels requests for the
router is exactly the right size for it. If nothing local is running, the
first line stays.

The user's own title always wins. A model's title can be replaced by a later
model's title (the thread may have changed subject), never a user's.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from .adapters.base import Backend, Message

PROMPT = ("Write a title for this conversation: 3 to 6 words, plain words, no quotes, "
          "no trailing period, in the language the user wrote in. Reply with the title only.")
MAX_CHARS = 60
TIMEOUT = 20.0


def clean(text: str) -> str:
    """The model's answer, trimmed to a title."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    line = next((l.strip() for l in text.strip().splitlines() if l.strip()), "")
    line = re.sub(r"^(title:\s*)", "", line, flags=re.I)
    line = line.strip(" \"'“”‘’`*#_-.:")
    if len(line) > MAX_CHARS:
        line = line[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return line


def transcript(turns: List[Dict[str, Any]], limit: int = 1200) -> str:
    """The thread so far, short enough to be cheap: the opening and the latest."""
    parts = []
    for t in turns:
        if t["role"] not in ("user", "assistant"):
            continue
        body = t["content"].strip().replace("\n", " ")
        parts.append(f"{t['role']}: {body[:400]}")
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[: limit // 2] + "\n…\n" + text[-limit // 2:]
    return text


async def suggest(backend: Backend, turns: List[Dict[str, Any]]) -> Optional[str]:
    """Ask `backend` for a title. None if it had nothing usable to say."""
    messages = [Message("system", PROMPT),
                Message("user", transcript(turns) + "\n\nTitle:")]
    parts: List[str] = []

    async def collect() -> None:
        async for chunk in backend.stream(messages, max_tokens=24, temperature=0.2):
            parts.append(chunk)

    try:
        await asyncio.wait_for(collect(), timeout=TIMEOUT)
    except (asyncio.TimeoutError, Exception):       # noqa: BLE001
        if not parts:
            return None
    finally:
        try:
            await backend.close()
        except Exception:                           # noqa: BLE001
            pass
    title = clean("".join(parts))
    return title if 2 <= len(title) <= MAX_CHARS else None
