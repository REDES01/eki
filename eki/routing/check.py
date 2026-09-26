"""The prompt check: which row of the table a request is.

One short call to the local model with the rows and their examples. When
the local model isn't running, the request goes to the `general` row and
the reason says so — it never silently guesses.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from .. import providers
from .table import rows

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
        lines.append(f"- {r['key']}: {r['title']}" + (f" (e.g. {ex})" if ex else ""))
    return "\n".join(lines)


def check(prompt: str, *, previous: Optional[str] = None, cwd: Optional[str] = None,
          checker: str = "local") -> Tuple[str, str]:
    """(row key, why)."""
    table = rows()
    keys = {r["key"] for r in table}
    try:
        judge = providers.get(checker)
    except KeyError:
        return "general", "no prompt checker configured"
    ok, why_not = judge.available()
    if not ok and getattr(judge, "cfg", {}).get("serve"):
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
