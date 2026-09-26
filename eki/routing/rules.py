"""Rules first: the obvious requests are sorted without a model.

Each rule is one line: a test on the request, the row it sends to, and the
reason that goes in the why as "rule: <reason>". The first rule that fires
wins; a rule whose row isn't in the table doesn't fire (a picture falls back
to `answer` when there is no `vision` row). Only when no rule fires does the
prompt check ask a model.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from .. import paths
from .needs import is_picture

DOING = ("fix", "add a", "run", "build", "refactor", "install", "delete", "rename", "commit", "deploy")
DRAW = ("draw", "generate an image", "make a picture", "paint", "picture of", "image of", "sketch")
EDIT = ("edit", "make it", "change the", "remove the", "add a")
REWRITE = ("make it", "shorter", "longer", "simpler", "again but", "rewrite", "in")
UPSCALE = ("upscale", "enlarge", "higher resolution", "bigger version")
ANIME = ("anime", "manga", "waifu", "chibi")
QUALITY = ("high quality", "detailed", "hq", "photorealistic", "best quality", "4k")
QUESTION = ("what", "why", "how", "who", "where", "when", "which", "is", "are", "does", "do",
            "can", "could", "should", "explain", "tell me")


@dataclass
class Ask:
    text: str                       # as written
    words: str                      # lower case, leading space and punctuation gone
    previous: Optional[str]
    cwd: Optional[str]
    attachments: List[str]
    last_picture: Optional[str] = None    # the picture the last turn drew or was given

    @property
    def folder(self) -> bool:
        return bool(self.cwd) and os.path.realpath(str(self.cwd)) != os.path.realpath(str(paths.scratch()))

    @property
    def picture(self) -> bool:
        return any(is_picture(a) for a in self.attachments)


def _starts(words: str, heads: Iterable[str]) -> Optional[str]:
    return next((h for h in heads if re.match(re.escape(h) + r"\b", words)), None)


def _has(words: str, bits: Iterable[str]) -> bool:
    return any(re.search(r"\b" + re.escape(b) + r"\b", words) for b in bits)


def _names_path(a: Ask) -> bool:
    for tok in a.text.split():
        tok = tok.strip("'\"`,;:()[]<>").rstrip("?!.")
        if not tok or ("/" not in tok and not tok.startswith("~")) or "://" in tok:
            continue
        p = os.path.expanduser(tok)
        if not os.path.isabs(p):
            p = os.path.join(a.cwd or str(paths.scratch()), p)
        try:
            if os.path.exists(os.path.realpath(p)):
                return True
        except (OSError, ValueError):
            continue
    return False


def _question(a: Ask) -> bool:
    return a.text.strip().endswith("?") or _starts(a.words, QUESTION) is not None


def _draws(a: Ask, bits: Iterable[str] = ()) -> bool:
    return _starts(a.words, DRAW) is not None and (not bits or _has(a.words, bits))


def _rewrite(a: Ask) -> bool:
    return bool(a.previous) and not a.folder and len(a.words.split()) <= 8 \
        and _starts(a.words, REWRITE) is not None


Rule = Tuple[Callable[[Ask], object], Callable[[Ask], str], Callable[[Ask], str]]

#: (fires?, row, reason) — tried in order
RULES: List[Rule] = [
    (lambda a: a.folder,                         lambda a: "code",       lambda a: "has a folder"),
    (_names_path,                                lambda a: "code",       lambda a: "names a path"),
    (lambda a: _has(a.words, UPSCALE) and (a.picture or a.last_picture),
     lambda a: "image-upscale", lambda a: "upscale the picture"),
    (lambda a: a.picture and _has(a.words, EDIT), lambda a: "image-edit", lambda a: "edit the picture"),
    (lambda a: a.picture,                        lambda a: "vision",     lambda a: "a picture attached"),
    (lambda a: _draws(a, ANIME),                 lambda a: "image-anime", lambda a: "asks for an anime picture"),
    (lambda a: _draws(a, QUALITY),               lambda a: "image-hq",   lambda a: "asks for a detailed picture"),
    (_draws,                                     lambda a: "image",      lambda a: "asks for a picture"),
    (lambda a: a.last_picture and not _question(a),
     lambda a: "image-edit", lambda a: "follow-up to a picture: edit it"),
    (lambda a: _starts(a.words, DOING),          lambda a: "code",
     lambda a: f"starts with {_starts(a.words, DOING)}"),
    (_rewrite,                                   lambda a: "answer",     lambda a: "rewrites the last answer"),
]


def _clean(prompt: str) -> str:
    return re.sub(r"^[\W_]+", "", prompt.lower()).strip()


def match(prompt: str, *, previous: Optional[str] = None, cwd: Optional[str] = None,
          attachments: Optional[List[str]] = None, keys: Iterable[str] = (),
          last_picture: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """(row key, "rule: <reason>") for the first rule that fires, or None."""
    keys = set(keys)
    a = Ask(prompt, _clean(prompt), previous, cwd, list(attachments or []), last_picture)
    for fires, row, reason in RULES:
        if not fires(a):
            continue
        key = row(a)
        if key == "vision" and key not in keys:
            key = "answer"                    # no vision row: a model that reads pictures answers
        if key in keys:
            return key, f"rule: {reason(a)}"
    return None
