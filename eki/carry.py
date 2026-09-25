# SPDX-License-Identifier: Apache-2.0
"""What a model is given of a thread it answers in (docs/routing.md).

The store holds the whole thread, whichever model said what. Replaying all
of it works until the thread outgrows the model reading it, and a program
that keeps its own session (Claude Code, Codex) already remembers more than
the store does — the files it read, the commands it ran. So:

- **A program with its own session for this thread resumes it.** It is told
  only what others said since it last answered, never the thread again.
- **Anything else reads the thread from the store** — a local model every
  turn, a program joining for the first time once. When it fits the
  reader's budget it gets it whole. When it doesn't, the older part is
  folded into a summary and the latest turns follow word for word.
- **A local model writes the summary**: free, on this Mac, the same one
  that names threads. Each new summary folds in the one before, so a long
  thread is summarized a piece at a time, never all at once. With nothing
  local up, an excerpt stands in — the start and end of each turn — and
  says it is one.
- **The summary is kept in the thread** (the store's `summaries`), with the
  last turn it covers and who wrote it: `eki summary <thread>` prints it,
  and the next reader starts from it instead of summarizing again.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from .adapters.base import Backend, Message

#: a model's thread may take this share of its context; the rest is the
#: instructions, the new message and the answer
SHARE = 0.5
#: characters per token, near enough for sizing
CHARS_PER_TOKEN = 4
#: a turn longer than this is shown by its start and end
CLIP = 1500
#: how much of the thread one summarizing pass reads, at most
PASS_CHARS = 16000
#: passes before the oldest part is left out
MAX_PASSES = 4
TIMEOUT = 90.0

PROMPT = ("You keep the notes for a long conversation between a person and AI models. Another "
          "model will carry the conversation on from your notes, without seeing what they "
          "replace. Write them: what the person wants, what was decided or agreed, names, "
          "numbers, file paths, what was made and where it is, and what is still open. Short "
          "paragraphs or bullets, in the language of the conversation, at most 300 words. "
          "Nothing that isn't in the conversation. Reply with the notes only.")


def budget(context_tokens: int) -> int:
    """How many characters of the thread a model with this context reads."""
    return int(max(context_tokens, 2000) * CHARS_PER_TOKEN * SHARE)


def said(turns: List[Dict[str, Any]], before: int) -> List[Dict[str, Any]]:
    """The conversation before turn `before`: who asked, who answered."""
    return [t for t in turns if t["id"] < before and t["role"] in ("user", "assistant")
            and (t.get("content") or "").strip()]


def clip(text: str, limit: int = CLIP) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + " […] " + text[-half:]


def render(turns: List[Dict[str, Any]], names: Dict[str, str], limit: int = CLIP) -> str:
    """The turns as a transcript another model can read."""
    lines = []
    for t in turns:
        who = "Person" if t["role"] == "user" else \
            names.get(t.get("backend") or "", t.get("backend") or "Model")
        lines.append(f"{who}: {clip(t['content'], limit)}")
    return "\n\n".join(lines)


def size(turns: List[Dict[str, Any]]) -> int:
    return sum(min(len(t["content"].strip()), CLIP + 5) + 40 for t in turns)


def missed(turns: List[Dict[str, Any]], key: str, before: int) -> List[Dict[str, Any]]:
    """What was said since `key` last answered — for a program resuming its
    own session, which remembers the rest. Nothing when no one else spoke."""
    talk = said(turns, before)
    last = max((i for i, t in enumerate(talk) if t["role"] == "assistant"
                and t.get("backend") == key), default=-1)
    since = talk[last + 1:]
    if not any(t["role"] == "assistant" and t.get("backend") != key for t in since):
        return []
    return since


def plan(turns: List[Dict[str, Any]], summary: Optional[Dict[str, Any]],
         room: int) -> Tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(start from the summary?, turns to fold into a new one, turns word for word).

    The whole thread when it fits; else the kept summary and what came after
    it, when that fits; else the latest turns that fill half the room, and
    the rest folded into a new summary."""
    if size(turns) <= room:
        return False, [], turns
    upto = int(summary["upto"]) if summary else 0
    after = [t for t in turns if t["id"] > upto]
    if summary and len(summary["text"]) + size(after) <= room:
        return True, [], after
    recent: List[Dict[str, Any]] = []
    for t in reversed(after):
        if recent and size(recent + [t]) > room // 2:
            break
        recent.insert(0, t)
    fold = after[:len(after) - len(recent)]
    if not fold and len(recent) > 1:
        fold, recent = recent[:-1], recent[-1:]
    return bool(summary), fold, recent


def chunks(fold: List[Dict[str, Any]], names: Dict[str, str], limit: int) -> Tuple[List[str], bool]:
    """The turns to fold, as transcripts one pass can read — the newest
    MAX_PASSES of them, and whether older ones were left out."""
    out, cur = [], []
    for t in fold:
        if cur and size(cur + [t]) > limit:
            out.append(render(cur, names))
            cur = []
        cur.append(t)
    if cur:
        out.append(render(cur, names))
    return out[-MAX_PASSES:], len(out) > MAX_PASSES


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return text.strip()


async def write(backend: Backend, previous: str, fold: List[Dict[str, Any]],
                names: Dict[str, str]) -> Optional[str]:
    """A summary of `previous` and `fold`, by `backend`. None if it gave
    nothing usable — the caller falls back to an excerpt."""
    room = min(PASS_CHARS, budget(backend.info.capabilities.context_tokens))
    parts, dropped = chunks(fold, names, room)
    notes = previous
    if dropped and not notes:
        notes = "(The start of the conversation is left out.)"
    try:
        for part in parts:
            ask = (f"Notes so far:\n{notes}\n\n" if notes else "") + \
                  f"Conversation to add to them:\n\n{part}\n\nNotes:"
            got: List[str] = []

            async def collect() -> None:
                async for chunk in backend.stream([Message("system", PROMPT), Message("user", ask)],
                                                  max_tokens=700, temperature=0.2):
                    if isinstance(chunk, str):
                        got.append(chunk)
            await asyncio.wait_for(collect(), timeout=TIMEOUT)
            text = _clean("".join(got))
            if len(text) < 20:
                return None
            notes = text
    except (asyncio.TimeoutError, Exception):       # noqa: BLE001
        return None
    finally:
        try:
            await backend.close()
        except Exception:                           # noqa: BLE001
            pass
    return notes or None


def excerpt(previous: str, fold: List[Dict[str, Any]], names: Dict[str, str],
            limit: int = 6000) -> str:
    """What stands in for a summary when no model can write one: the one
    before, then the start and end of each turn, the newest kept."""
    body = render(fold, names, limit=400)
    if len(body) > limit:
        body = "[…]\n" + body[-limit:]
    head = "(An excerpt, not a summary: no local model was up to write one.)"
    return "\n\n".join(p for p in (head, previous, body) if p)


def joining(summary: str, author: str, recent: List[Dict[str, Any]], names: Dict[str, str]) -> str:
    """The thread, for a program joining it: the summary, then the latest turns."""
    text = render(recent, names)
    if summary:
        text = (f"[eki: a summary of the earlier part, by {author}:]\n\n{summary}\n\n"
                f"[eki: then, word for word:]\n\n{text}") if text else summary
    return ("[eki: you're joining a conversation that other models have been part of. "
            f"What was said so far:]\n\n{text}\n\n[eki: the new message follows.]\n\n")


def meanwhile(since: List[Dict[str, Any]], names: Dict[str, str], limit: int) -> str:
    """For a program resuming its own session: what others said since."""
    text = render(since, names)
    if len(text) > limit:
        text = "[…]\n" + text[-limit:]
    return ("[eki: since you last answered in this conversation, others have. What was said:]"
            f"\n\n{text}\n\n[eki: the new message follows.]\n\n")


def as_messages(summary: str, author: str, recent: List[Dict[str, Any]]) -> List[Message]:
    """The thread for a model that reads it every turn: the summary opens the
    first message, so the roles still take turns."""
    out = [Message(t["role"], t["content"]) for t in recent]
    if not summary:
        return out
    lead = f"[eki: a summary of the earlier conversation, by {author}:]\n\n{summary}"
    if out and out[0].role == "user":
        out[0] = Message("user", lead + "\n\n[eki: then:]\n\n" + out[0].content)
    else:
        out.insert(0, Message("user", lead))
    return out
