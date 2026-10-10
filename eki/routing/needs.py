"""What a request needs from a provider, and what a provider lacks.

A routing row declares `needs` (from providers.ABILITIES); a request adds
what its shape asks for (a picture attached → vision). A provider whose
`can` doesn't cover the needs is skipped, and the why says so in words.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import providers, targets

#: what a row needs when it doesn't say
ROW_NEEDS: Dict[str, List[str]] = {"code": ["tools"], "web": ["web"], "general": [],
                                   "image": ["image"], "image-hq": ["image"], "image-anime": ["image"],
                                   "image-edit": ["image-edit"], "image-upscale": ["image-edit"]}

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
    """The row's needs plus what the request's shape adds; no duplicates, order kept.
    A picture attached to an edit is the source to change, not one to read:
    it adds no vision."""
    out = row_needs(row)
    if "image-edit" not in out and any(is_picture(a) for a in attachments or []):
        out.append("vision")
    return list(dict.fromkeys(out))


def lacking(target: Any, needs: List[str]) -> List[str]:
    """Which of `needs` the target's provider can't do (all of them if there's no such provider)."""
    name = targets.provider_of(target)
    cfg = providers.config().get(name)
    if cfg is None:
        return list(needs)
    can = set(providers.capabilities(name, cfg))
    return [n for n in needs if n not in can]


def cant(missing: List[str]) -> str:
    return "can't do " + " or ".join(WORDS.get(m, m) for m in missing)


def describe(target: Any) -> str:
    """One line for the prompt-check menu and `eki route`: target [tags] — about.
    A target naming a model or effort reads as its label (`claude · opus · high`)."""
    name = targets.provider_of(target)
    cfg = providers.config().get(name, {})
    try:
        t = targets.parse(target)
        shown = targets.label(name, t.tier or t.model, t.effort, None) if (t.tier or t.model or t.effort) else name
    except ValueError:
        shown = str(target)
    line = f"{shown} [{', '.join(providers.capabilities(name, cfg))}]"
    about = str(cfg.get("about") or "").strip()
    return f"{line} — {about}" if about else line
