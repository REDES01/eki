# SPDX-License-Identifier: Apache-2.0
"""When a subscription runs out in the middle of a run, the run carries on
elsewhere (docs/routing.md, ROADMAP Stage 2: failover with handoff).

A limit hit before a run starts already moves it: the router skips a
provider whose window is spent. This is the other case — Claude Code is
halfway through and its week runs out. Without this the run fails, and you
ask again yourself. With it, eki:

- recognises the stop as a usage limit (Claude Code's own "rejected" rate
  limit event, or the words every provider uses for it);
- keeps what the run had done — its answer so far, and the files it changed
  in the thread's copy of the folder, which stays as it is;
- hands the same request to the next choice in the row that isn't on the
  same subscription, told what was done and to carry on, with the
  conversation so far;
- says so in the thread.

Once per run it can hop twice at most, and never for a model you picked by
name — that one reports the limit.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict

LIMIT = re.compile(
    r"usage limit|rate[ _-]?limit(ed)?|limit (reached|exceeded)|hit your (usage |weekly |daily )?limit"
    r"|reached your (weekly |daily |monthly )?(usage )?limit|out of (extra )?usage|usage credit limit"
    r"|spend limit|quota|insufficient_quota|credit balance (is )?too low|too many requests|\b429\b"
    r"|billing_error", re.I)
#: hops a run may make before it reports the limit instead
MAX_HOPS = 2


def is_limit(message: str) -> bool:
    return bool(LIMIT.search(message or ""))


def rejected(info: Dict[str, Any]) -> str:
    """Claude Code's rate limit event saying the request was refused: why, or ""."""
    if not isinstance(info, dict) or str(info.get("status") or "").lower() != "rejected":
        return ""
    if info.get("isUsingOverage") or str(info.get("overageStatus") or "").lower() in ("allowed", "allowed_warning"):
        return ""                                   # paying past the window: it carries on
    kind = str(info.get("rateLimitType") or info.get("type") or "usage").replace("_", " ")
    reset = info.get("resetsAt") or info.get("resets_at")
    when = ""
    if isinstance(reset, (int, float)) and reset > 0:
        when = time.strftime(", resets %a %H:%M", time.localtime(reset if reset < 1e12 else reset / 1000))
    return f"usage limit reached ({kind}{when})"


def brief(label: str, why: str, partial: str) -> str:
    """What the model taking over is told, before the request itself."""
    done = partial.strip()
    if len(done) > 3000:
        done = "[…]\n" + done[-3000:]
    return (f"[eki: {label} was working on this and stopped at its usage limit ({why}). "
            + (f"What it had written so far:]\n\n{done}\n\n[eki: " if done else "")
            + "Carry on from where it stopped — anything it changed in the folder is already there. "
              "The request was:]\n\n")
