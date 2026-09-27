"""Transient errors: a run that fails this way is tried again, not taken
as the last word.

A dropped connection, a timeout or an overloaded server says nothing about
the work; the worker puts such a run back in the queue after a short wait
(BACKOFF) and resumes the same program session. After len(BACKOFF) tries
it fails as any other run.
"""
import re
from typing import Optional

#: seconds before the 1st, 2nd and 3rd retry
BACKOFF: tuple = (30, 120, 300)

_PASSING = ("connection dropped", "connection reset", "connection closed",
            "econnreset", "etimedout", "econnrefused", "epipe",
            "socket hang up", "network", "fetch failed", "timed out")

_SERVER = re.compile(r"\b5\d\d\b|overloaded|\brate\b")


def looks_transient(text: Optional[str]) -> bool:
    """True when the error reads like a passing network or server hiccup."""
    t = (text or "").lower()
    if not t:
        return False
    if any(k in t for k in _PASSING):
        return True
    return "api error" in t and bool(_SERVER.search(t))


def delay(retries: int) -> Optional[int]:
    """Seconds to wait before the next retry, given how many transient
    retries the run has had; None once they are used up."""
    if 0 <= retries < len(BACKOFF):
        return BACKOFF[retries]
    return None
