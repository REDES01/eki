"""The prompt check: which row of the table a request is.

Rules first (rules.py): the obvious requests are sorted without a model.
Only when no rule fires is there one short call to the local model with the
rows, their needs, and each target's tags. When the checker can't run, the
request goes to the `general` row and the reason says so — it never
silently guesses. A checker that is off on purpose (an on-demand model) is
started for the check only if routing.json's `checker_wakes` is true.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from .. import providers
from . import rules
from .needs import describe, row_needs
from .table import checker_wakes, rows

ASK = (
    "Sort a request into one row of a routing table. Rows:\n{rows}\n\n"
    "A request in a project folder or about files, code, commands or this computer is `code`. "
    "A follow-up that only rewrites the previous answer is `answer`.\n"
    'Reply with only JSON: {{"row": "<key>", "why": "<a few words>"}}')


def _menu(table: List[Dict]) -> str:
    lines = []
    for r in table:
        if r["key"] == "general":
            continue
        ex = "; ".join(r.get("examples") or [])
        lines.append(f"- {r['key']}: {r['title']}" + (f" (e.g. {ex})" if ex else "")
                     + f"; needs {', '.join(row_needs(r)) or 'nothing'}")
        lines += [f"    · {describe(t)}" for t in r.get("targets") or []]
    return "\n".join(lines)


def check(prompt: str, *, previous: Optional[str] = None, cwd: Optional[str] = None,
          checker: str = "local", attachments: Optional[List[str]] = None,
          last_picture: Optional[str] = None) -> Tuple[str, str]:
    """(row key, why): a rule's if one fires, else the model's."""
    table = rows()
    keys = {r["key"] for r in table}
    ruled = rules.match(prompt, previous=previous, cwd=cwd, attachments=attachments, keys=keys,
                        last_picture=last_picture)
    if ruled:
        return ruled
    try:
        judge = providers.get(checker)
    except KeyError:
        return "general", "no prompt checker configured"
    ok, why_not = judge.available()
    if not ok and getattr(judge, "cfg", {}).get("serve"):
        if not checker_wakes():
            return "general", f"prompt check skipped: {checker} is off (checker_wakes is false)"
        from .. import models                         # off on purpose: start it for the check
        ok = models.ensure(checker)
        why_not = "didn't come up" if not ok else ""
    if not ok:
        return "general", f"prompt check skipped: {checker} {why_not}"
    request = prompt if not previous else f"(previous answer: {previous[:300]})\n{prompt}"
    if cwd:
        request = f"(working in folder {cwd})\n{request}"
    messages = [{"role": "system", "content": ASK.format(rows=_menu(table))},
                {"role": "user", "content": request[:4000]}]
    try:
        got = judge.complete(messages, max_tokens=80, timeout=30)   # type: ignore[attr-defined]
    except Exception as e:                    # noqa: BLE001 — any failure means "couldn't check"
        return "general", f"prompt check failed: {e}"
    m = re.search(r"\{.*\}", got.get("text", ""), re.S)
    try:
        answer = json.loads(m.group(0)) if m else {}
    except ValueError:
        answer = {}
    key = str(answer.get("row", ""))
    if key not in keys:
        return "general", f"prompt check gave no row ({got.get('text', '')[:60]!r})"
    return key, f"prompt check: {answer.get('why') or key}"
