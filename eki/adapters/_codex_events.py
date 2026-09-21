"""Reading Codex's JSONL event stream.

Shapes observed on codex-cli 0.154.0:

    {"type":"thread.started","thread_id":"…"}
    {"type":"item.completed","item":{"type":"agent_message","text":"CODEX OK"}}
    {"type":"item.completed","item":{"type":"error","message":"…"}}
    {"type":"turn.completed","usage":{…}}

Older and newer releases have used `agent_message` at the top level and delta
events; both are accepted. Anything unrecognised is ignored rather than
guessed at — this vocabulary has already changed under us once.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def session_id(event: Dict[str, Any]) -> Optional[str]:
    for key in ("thread_id", "session_id", "conversation_id"):
        value = event.get(key)
        if value:
            return str(value)
    return None


def usage(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Token counts, which arrive once at the end of a turn."""
    if str(event.get("type") or "") in ("turn.completed", "turn.failed"):
        counts = event.get("usage")
        if isinstance(counts, dict):
            return counts
    return None


def read(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(assistant text, error message) — at most one of them."""
    kind = str(event.get("type") or "")

    if kind == "item.completed":
        item = event.get("item") or {}
        itype = str(item.get("type") or "")
        if itype == "agent_message":
            text = item.get("text") or item.get("message")
            return (text if isinstance(text, str) and text else None), None
        if itype == "error":
            return None, str(item.get("message") or "")[:300]
        return None, None

    if kind in ("agent_message", "assistant_message"):
        for key in ("text", "message", "content"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value, None
        return None, None

    if "delta" in kind:
        value = event.get("delta") or event.get("text")
        if isinstance(value, str) and value:
            return value, None
        if isinstance(value, dict):
            inner = value.get("text") or value.get("content")
            if isinstance(inner, str) and inner:
                return inner, None
        return None, None

    if kind == "error":
        return None, str(event.get("message") or event.get("error") or "")[:300]

    return None, None
