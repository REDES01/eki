"""A kind's name and explanation for people: each kind module's TITLE ("Preview links") and EXPLAIN (a short
plain-words paragraph: what it is, why the project has it, who uses it and how). The grid and the card panel
show these; the internal kind name ("serve") stays in the files. Neither function ever raises."""
from __future__ import annotations

from typing import Any


def _attr(kind: Any, name: str) -> str:
    from . import KINDS, kind_module
    if not isinstance(kind, str) or kind not in KINDS:
        return ""
    try:
        got = getattr(kind_module(kind), name, "")
    except Exception:  # a kind that won't import has no words of its own
        return ""
    return got if isinstance(got, str) else ""


def title(kind: str) -> str:
    """The kind's TITLE; the kind string itself for an unknown kind, "Card" when there is none."""
    return _attr(kind, "TITLE") or (kind if isinstance(kind, str) and kind else "Card")


def explain(kind: str) -> str:
    """The kind's EXPLAIN; '' for an unknown kind."""
    return _attr(kind, "EXPLAIN")
