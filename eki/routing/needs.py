"""What a request needs from a provider, and what a provider lacks.

A routing row declares `needs` (from providers.ABILITIES); a request adds
what its shape asks for (a picture attached → vision). A provider whose
`can` doesn't cover the needs is skipped, and the why says so in words.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import providers

#: what a row needs when it doesn't say
ROW_NEEDS: Dict[str, List[str]] = {"code": ["tools"], "web": ["web"], "general": []}

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic")

#: how each ability reads in "can't do …"
WORDS: Dict[str, str] = {"text": "text", "tools": "tools", "web": "the web", "vision": "pictures",
                         "image": "images", "image-edit": "image edits"}


def row_needs(row: Dict[str, Any]) -> List[str]:
    if isinstance(row.get("needs"), list):
        return list(row["needs"])
    return list(ROW_NEEDS.get(row.get("key", ""), ["text"]))


def is_picture(path: str) -> bool:
    return str(path).lower().endswith(IMAGE_EXT)


def request_needs(row: Dict[str, Any], attachments: Optional[List[str]] = None) -> List[str]:
    """The row's needs plus what the request's shape adds; no duplicates, order kept."""
    out = row_needs(row)
    if any(is_picture(a) for a in attachments or []):
        out.append("vision")
    return list(dict.fromkeys(out))


def lacking(name: str, needs: List[str]) -> List[str]:
    """Which of `needs` the provider `name` can't do (all of them if there's no such provider)."""
    cfg = providers.config().get(name)
    if cfg is None:
        return list(needs)
    can = set(providers.capabilities(name, cfg))
    return [n for n in needs if n not in can]


def cant(missing: List[str]) -> str:
    return "can't do " + " or ".join(WORDS.get(m, m) for m in missing)


def describe(name: str) -> str:
    """One line for the prompt-check menu and `eki route`: name [tags] — about."""
    cfg = providers.config().get(name, {})
    line = f"{name} [{', '.join(providers.capabilities(name, cfg))}]"
    about = str(cfg.get("about") or "").strip()
    return f"{line} — {about}" if about else line
