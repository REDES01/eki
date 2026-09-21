# SPDX-License-Identifier: Apache-2.0
"""Measuring an image model: draw a fixed set, have a model that can see
check each picture against what was asked.

Text models are scored by exact answers; a picture has none. The next
best thing is what GenEval does: prompts whose success is a list of
yes/no facts — is there a cat, is it left of the dog, is the cup blue,
are there three apples — and a vision model answering those, one picture
at a time. The facts are plain enough that a judge's opinion barely
enters; the score is the share of facts that hold across the set. One
slot, "image/medium", on the same registry as everything else.

The judge is the cheapest model here that can see — Claude Code on a
subscription, or an API with vision. It costs a little of that window,
so eki runs this on its own only when that window is nearly idle.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Union

from .adapters.base import Backend, BackendError, Message

SLOT = "image/medium"

#: (prompt, facts a judge can check with yes/no)
ITEMS: List[Dict[str, Any]] = [
    {"prompt": "a photo of a red bicycle leaning against a white wall",
     "facts": ["There is a bicycle", "The bicycle is red", "There is a white wall"]},
    {"prompt": "three green apples on a wooden table, nothing else",
     "facts": ["There are apples", "There are exactly three apples", "The apples are green", "They are on a wooden table"]},
    {"prompt": "a black cat sitting to the left of a brown dog, both facing the camera",
     "facts": ["There is a cat", "There is a dog", "The cat is to the left of the dog", "The cat is black"]},
    {"prompt": "a blue coffee cup and a yellow plate on a kitchen counter",
     "facts": ["There is a cup", "The cup is blue", "There is a plate", "The plate is yellow"]},
    {"prompt": "a wooden sign that says OPEN hanging on a shop door",
     "facts": ["There is a sign", "The sign shows the word OPEN, spelled correctly", "There is a door"]},
    {"prompt": "a single orange balloon floating above a green field under a clear sky",
     "facts": ["There is exactly one balloon", "The balloon is orange", "There is a green field", "The sky is clear"]},
    {"prompt": "a close-up of a hand holding a small silver key",
     "facts": ["There is a hand", "The hand is holding a key", "The key is silver-coloured", "The hand has five fingers"]},
    {"prompt": "two identical white mugs side by side on a dark tray",
     "facts": ["There are exactly two mugs", "The mugs are white", "There is a dark tray"]},
    {"prompt": "a watercolour painting of a lighthouse on a cliff at sunset",
     "facts": ["There is a lighthouse", "It stands on a cliff", "It looks like a watercolour painting", "The sky suggests sunset"]},
    {"prompt": "a robot reading a book in a library, cartoon style",
     "facts": ["There is a robot", "The robot is holding or reading a book", "The setting is a library", "The style is cartoon-like"]},
    {"prompt": "a glass of water in front of a window, with a small plant beside it on the right",
     "facts": ["There is a glass of water", "There is a window behind it", "There is a plant", "The plant is to the right of the glass"]},
    {"prompt": "a street at night with a yellow taxi and wet pavement reflecting lights",
     "facts": ["It is night", "There is a taxi", "The taxi is yellow", "The pavement looks wet"]},
]

_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_BOOLS = re.compile(r"\[\s*(?:true|false)(?:\s*,\s*(?:true|false))*\s*\]", re.I)


def judge_prompt(path: str, facts: List[str]) -> str:
    lines = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
    return (f"Read the image file at {path} and look at it carefully.\n"
            f"For each statement below, say whether it is true of the picture.\n{lines}\n"
            f"Reply with only a JSON array of {len(facts)} booleans, in order, nothing else.")


async def verdicts(judge: Backend, path: str, facts: List[str]) -> List[bool]:
    text = ""
    async for piece in judge.stream([Message("user", judge_prompt(path, facts))]):
        text += piece
    m = _BOOLS.search(text)
    if not m:
        raise BackendError(f"the judge didn't answer in booleans: {text[:120]!r}")
    got = json.loads(m.group(0).lower())
    return [bool(x) for x in got][:len(facts)] + [False] * max(0, len(facts) - len(got))


async def run(subject: Backend, judge: Callable[[], Optional[Backend]],
              items: Optional[List[Dict[str, Any]]] = None
              ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
    """Draw each item with `subject`, check it with the judge; yields
    lines and finally {"slot", "score", "n"}."""
    grader = judge()
    if grader is None:
        raise BackendError("no model here can look at pictures — a judge needs vision "
                           "(Claude Code, or an API provider with vision)")
    items = items or ITEMS
    held = 0
    facts_total = 0
    done = 0
    for item in items:
        text = ""
        try:
            async for piece in subject.stream([Message("user", item["prompt"])]):
                text += piece
        except BackendError as e:
            yield f"  ✗ {item['prompt'][:50]}… — {e}\n"
            facts_total += len(item["facts"])
            done += 1
            continue
        m = _IMAGE.search(text)
        path = m.group(1).replace("%20", " ") if m else ""
        if not path or not os.path.exists(path):
            yield f"  ✗ {item['prompt'][:50]}… — no picture came back\n"
            facts_total += len(item["facts"])
            done += 1
            continue
        try:
            got = await verdicts(grader, path, item["facts"])
        except BackendError as e:
            yield f"  ? {item['prompt'][:50]}… — judge: {e}\n"
            continue
        held += sum(got)
        facts_total += len(got)
        done += 1
        misses = [f for f, ok in zip(item["facts"], got) if not ok]
        yield (f"  {'✓' if not misses else '·'} {item['prompt'][:50]}… {sum(got)}/{len(got)}"
               + (f" — missed: {'; '.join(misses)}" if misses else "") + "\n")
    if not facts_total:
        raise BackendError("nothing could be judged")
    yield {"slot": SLOT, "score": round(held / facts_total, 3), "n": done}
