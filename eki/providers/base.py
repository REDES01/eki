"""What every provider is: a way to take one turn of a thread.

A provider gets a `Turn` (what to send, which session to resume, the
folder) and reports what happens through `emit(kind, data)`:

    session  {"id"}                    the program's session, so a resume is possible
    text     {"text"}                  what it said
    tool     {"name", "detail"}        a tool it used
    thinking {"text"}
    usage    {...}
    note     {"text"}                  something eki wants you to know

and returns an `Outcome`. It never decides where work goes; that is
routing's job.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

Emit = Callable[[str, Dict[str, Any]], None]


@dataclass
class Turn:
    prompt: str                           # what to send now
    history: List[Dict[str, str]]         # the thread so far (for providers without a session)
    resume: Optional[str] = None          # this provider's session for the thread
    cwd: Optional[str] = None
    run_id: str = ""
    thread_id: str = ""
    stop: threading.Event = field(default_factory=threading.Event)
    extra: Dict[str, Any] = field(default_factory=dict)   # skills dir, mcp config …
    images: List[str] = field(default_factory=list)       # this run's pictures: absolute paths


@dataclass
class Outcome:
    state: str = "done"                   # done | failed | handed_off | limited
    error: str = ""
    reset_at: Optional[float] = None      # when a limit lifts
    reason: str = ""                      # why it handed off
    finished: bool = False                # the program said its turn is over


class Provider:
    kind = "base"

    def __init__(self, name: str, cfg: Dict[str, Any]):
        from . import capabilities   # the package imports this module
        self.name = name
        self.cfg = cfg
        self.label = cfg.get("label") or name
        #: what it can do: text, tools, web, vision, image, image-edit
        self.can: List[str] = capabilities(name, cfg)
        #: a sentence or two on it, for the person and the prompt check alike
        self.about: str = cfg.get("about", "")

    @property
    def harness(self) -> bool:
        """It can use tools (files, commands, web)."""
        return "tools" in self.can

    def available(self) -> tuple:
        """(can it take work at all, why not)."""
        return True, ""

    def take(self, turn: Turn, emit: Emit) -> Outcome:
        raise NotImplementedError


def find_binary(name: str) -> Optional[str]:
    """A launchd-started process has a bare PATH, so look where CLIs live."""
    if os.path.isabs(name):
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for d in ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.codex/bin"):
        cand = os.path.join(os.path.expanduser(d), name)
        if os.access(cand, os.X_OK):
            return cand
    return None


def looks_like_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ("usage limit", "rate limit", "rate_limit", "limit reached",
                                "hit your limit", "quota", "too many requests", "429"))


#: how much of a thread a program joining it is given
HISTORY_CHARS = 12000


def with_history(turn: Turn) -> str:
    """The prompt for a program that has no session in this thread yet:
    what was said before (by whoever said it), then the request."""
    if not turn.history:
        return turn.prompt
    lines: List[str] = []
    for m in turn.history:
        who = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"{who}: {m['content']}")
    past = "\n\n".join(lines)
    if len(past) > HISTORY_CHARS:
        past = "…" + past[-HISTORY_CHARS:]
    return ("Part of this conversation happened with another assistant. What was said "
            "that you haven't seen:\n\n"
            f"<earlier>\n{past}\n</earlier>\n\nThe request now:\n\n{turn.prompt}")


MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}


def image_block(path: str) -> Tuple[str, str]:
    """A picture as the programs take it inline: (media type, base64 of the file)."""
    media = MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "image/png")
    with open(path, "rb") as f:
        return media, base64.b64encode(f.read()).decode("ascii")


def short(value: Any, n: int = 120) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
