# SPDX-License-Identifier: Apache-2.0
"""Talking to eki about routing, in plain words (docs/routing.md).

Most messages are for a model. A few are for eki: "use Claude for code",
"only use Codex if Claude runs out", "never use API keys", "for this thread
use Codex", "things like this are writing", "why did that go to Codex?",
"/routing". eki answers those itself, in the thread, without a model run —
except that turning free words into a table edit is read by a model when it
isn't one of the plain forms.

Recognising them is deliberately narrow: short, not in a folder, starting
like an instruction or a question about routing, and naming something eki
routes to or a row of the table. "use Claude's API in this script" is a
coding request, not a preference; it doesn't start with a routing verb
aimed at a model name followed by a kind of work, and it goes to a model.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

WHY = re.compile(r"^\W*(eki[,:]?\s*)?why (did|was|does|is|'d) (that|this|it|the last one)\b.{0,60}"
                 r"\b(go|goes|gone|sent|routed|went|use|used|pick|picked|choose|chose|end up)\b", re.I)
SHOW = re.compile(r"^\s*/routing\b|^\W*(eki[,:]?\s*)?(show|what('s| is| are)|let me see|list)\s+"
                  r"(me\s+)?(the\s+|my\s+|your\s+)?(routing|routing table|routing rules|where things go)\b", re.I)
EDIT = re.compile(
    r"^\W*(eki[,:]?\s*)?(please\s+)?(always\s+|from now on,?\s*|for this (thread|chat|conversation),?\s*"
    r"|today,?\s*|for now,?\s*)?"
    r"(use|prefer|route|send|put|stick (to|with)|switch (to|over to)|go easy on|avoid|don'?t use|do not use"
    r"|never use|stop using|only use|keep|save|reserve|treat|things like (this|that)|that (was|is)|forget|undo"
    r"|reset)\b", re.I)
ROW_WORDS = re.compile(r"\b(code|coding|repo|writing|translation|translate|quick|questions?|explain"
                       r"|reasoning|research|search|web|pictures?|images?|retry|hard|big|everything"
                       r"|runs? out|backup|fallback|rule|routing|thread)\b", re.I)


def addressed(prompt: str, names: List[str], has_folder: bool = False) -> str:
    """"why" | "show" | "edit" | "" — whether this message is for eki."""
    text = (prompt or "").strip()
    if has_folder or not text or len(text) > 220 or "\n" in text.strip():
        return ""
    if WHY.search(text):
        return "why"
    if SHOW.search(text):
        return "show"
    if EDIT.search(text):
        low = text.lower()
        named = any(re.search(rf"\b{re.escape(n.lower())}\b", low) for n in names if n)
        if (named or re.search(r"\b(things like|that (was|is))\b", low)) and ROW_WORDS.search(text):
            return "edit"
        # "never use API keys", "stop using Codex" — a name, then the end of
        # the thought ("…Claude's API in this script" is a request, not this)
        for n in names:
            if n and re.search(rf"\b(never use|don'?t use|do not use|stop using|avoid)\s+(the\s+)?"
                              rf"{re.escape(n.lower())}\b\s*(for (anything|everything|now)|anymore|again"
                              rf"|at all|unless\b.*|except\b.*|[.!]?\s*$)", low):
                return "edit"
        if re.match(r"^\W*(eki[,:]?\s*)?(forget|undo|reset)\b", text, re.I) and \
                re.search(r"\b(rule|routing|that)\b", low):
            return "edit"
    return ""


def edit_prompt(message: str, rows: List[Dict[str, Any]], targets: List[Dict[str, str]],
                previous: str) -> str:
    rows_text = "\n".join(f"- {r['id']}: {r['title']} (e.g. {'; '.join(r['examples'][:2])}) → "
                          + " → ".join(r["labels"]) for r in rows)
    tg = "\n".join(f"- {t['target']}: {t['label']}" for t in targets)
    return (
        "You turn a person's instruction about which AI model handles what into one edit of a "
        "routing table. Rows are kinds of work; each row lists its choices in order, first one "
        "that can take the request gets it.\n\n"
        f"The table now:\n{rows_text}\n\nTargets that exist:\n{tg}\n\n"
        + (f"The request before this instruction (it may refer to it): {previous[:300]!r}\n"
           if previous and re.search(r"\b(things like|like (this|that)|that (was|is)|this (was|is))\b",
                                     message, re.I) else "")
        + f"The instruction — act on this and nothing else: {message!r}\n\n"
        "Reply with ONE JSON object and nothing else:\n"
        '{"action": "first" | "never" | "backup" | "thread" | "example" | "forget" | "none",\n'
        ' "row": "<row id, or \\"all\\">",           // for first / example\n'
        ' "target": "<a target from the list>",      // for first / never / backup / thread\n'
        ' "without": ["<targets to take out of that row>"], // for first: \"…, not the local model\"\n'
        ' "until": "today" | null,                   // for thread: only today\n'
        ' "forget": "<row id | provider key | all>", // for forget\n'
        ' "reply": "one short sentence confirming it, in the person\'s language"}\n'
        "first = put the target first in that row (\"use Claude for code\"); never = don't route "
        "there automatically; backup = only when nothing else can (\"only use Codex if Claude runs "
        "out\"); thread = first choice for this conversation only; example = the request before "
        "belongs to that row (\"things like this are writing\"); forget = take a rule back; "
        "none = it isn't about routing after all.")


def plain(message: str, providers: Dict[str, List[str]]) -> Optional[Dict[str, Any]]:
    """The plain forms, read without a model: "never use X", "only use X if …
    runs out", "for this thread use X". `providers` maps a provider key to the
    words that name it."""
    low = message.lower()
    if re.search(r"\b(forget|undo|remove|drop|cancel|reset)\b", low) and \
            re.search(r"\bthis (thread|chat|conversation)\b|\bthread\b", low):
        return {"action": "forget", "forget": "thread"}

    def who() -> Optional[str]:
        hits = [(low.find(w), k) for k, ws in providers.items() for w in ws
                if w and re.search(rf"\b{re.escape(w.lower())}\b", low)]
        return min(hits)[1] if hits else None
    m = re.search(r"\b(never use|don'?t use|do not use|stop using)\b", low)
    if m and not re.search(r"\b(for|on|in)\b.*\b(code|writing|research|questions?|pictures?)\b", low):
        k = who()
        return {"action": "never", "target": k} if k else None
    if re.search(r"\bonly (use|when|if)\b.*\b(runs? out|is out|can'?t|isn'?t available|hits? (its|the) limit)\b"
                 r"|\bas (a |the )?(backup|fallback)\b", low):
        k = who()
        return {"action": "backup", "target": k} if k else None
    if re.search(r"\bfor this (thread|chat|conversation)\b", low):
        k = who()
        return {"action": "thread", "target": k, "until": None} if k else None
    return None
