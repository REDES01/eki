"""What kind of stop a failed or lost run is — the one place that decides.

Pure. Callers pass n = runs.stop["n"] (or use `after`) and store `record`;
only PINGS kinds may ask a person. Reset times are read by eki/resettime.py.
"""
import re
import time
from dataclasses import dataclass, replace
from typing import Optional

from eki import resettime
from eki.providers.base import looks_like_limit

KINDS = ("lost", "quota", "transient", "environment", "judgment")
PINGS = ("environment", "judgment")
TRANSIENT_WAITS = (60, 600, 1800)                  # 3 tries over about 41 minutes
QUOTA_BACKOFF = (1800, 3600, 7200, 14400, 28800)   # when no reset time is given; capped at the last
LONG_WAIT = 12 * 3600
LOST_CAP = 10
ENV_RECHECK = 3600


@dataclass(frozen=True)
class Failure:
    state: str                 # "failed" | "limited" | "lost"
    error: str = ""
    reset_at: Optional[float] = None
    provider: str = ""
    n: int = 0                 # stops of this same kind this run already had in a row
    since: Optional[float] = None
    now: float = 0.0
    tz: Optional[str] = None


@dataclass(frozen=True)
class Stop:
    kind: str
    retry_at: Optional[float]
    fix: str
    long_wait: bool = False


_CHECK = re.compile(r"\b\d+ (?:failed|errors?)\b|^FAILED |AssertionError", re.M)
_LOGIN = re.compile(r"please run /login|invalid api key|\b401\b|authentication_error|not logged in|codex login", re.I)
_MISSING = re.compile(r"(?:FileNotFoundError|No such file or directory)[^'\"\n]*['\"]([^'\"\n]+)['\"]")
_NOCLI = re.compile(r"(?:command not found|is not installed|not found in PATH|executable not found)[: ]*['\"]?([\w.\-]*)", re.I)
_DENIED = re.compile(r"operation not permitted(?:[^'\"\n]*['\"](/[^'\"\n]+)['\"])?", re.I)
_MODEL = re.compile(r"unknown provider|unknown model|invalid model|model_not_found|model\b[^\n]{0,60}\b(?:does not exist|not found|not supported)", re.I)
_PASSING = ("connection dropped", "connection reset", "connection closed", "econnreset", "etimedout",
            "econnrefused", "epipe", "socket hang up", "network", "fetch failed", "timed out",
            "didn't come up", "lost stream", "stream disconnected", "stream ended unexpectedly")
_CRASH = re.compile(r"traceback \(most recent call last\)|segmentation fault|sigsegv|sigabrt|panicked at|"
                    r"abort trap|core dumped|killed by signal|\bexited (?:with (?:code|status) )?-?[1-9]\d*\b", re.I)
_LIMIT = re.compile(r"hit your \w+ limit", re.I)    # Claude's session and weekly limits
_SERVER = re.compile(r"\b5\d\d\b|overloaded", re.I)
_NAMES = {"claude": "Claude Code", "claude_code": "Claude Code", "codex": "Codex"}
_SIGNIN = {"Codex": "Codex isn't signed in — run `codex login`",
           "Claude Code": "Claude Code isn't signed in — run `claude` and /login"}


def classify(f: Failure) -> Stop:
    """The kind of this stop, when to try again and what to fix."""
    now, text = f.now, f.error or ""
    if f.state == "lost":
        return Stop("lost", now, "") if f.n < LOST_CAP else _transient(now, f.n - LOST_CAP)
    if _CHECK.search(text):
        return Stop("judgment", None, "")
    if fix := _environment(text, f.provider):
        return Stop("environment", None, fix)
    low = text.lower()
    if any(k in low for k in _PASSING) or _CRASH.search(text) or ("api error" in low and _SERVER.search(low)):
        return _transient(now, f.n)
    if f.state == "limited" or looks_like_limit(text) or _LIMIT.search(text):
        return _quota(f)
    return Stop("judgment", None, "")


def after(prev: Optional[dict], f: Failure) -> Stop:
    """classify with n and since taken from the run's last stop when it was
    of the same kind (a lost run past LOST_CAP keeps counting as transient)."""
    first = classify(replace(f, n=0, since=None))
    same = prev and (prev.get("kind") == first.kind or (
        f.state == "lost" and prev.get("kind") == "transient" and (prev.get("n") or 0) > LOST_CAP))
    return classify(replace(f, n=prev.get("n") or 0, since=prev.get("since"))) if same else first


def record(prev: Optional[dict], stop: Stop, now: float) -> dict:
    """The stored shape; n and since carry over while the kind holds."""
    same = prev and (prev.get("kind") == stop.kind or (
        prev.get("kind") == "lost" and stop.kind == "transient" and (prev.get("n") or 0) >= LOST_CAP))
    return {"kind": stop.kind, "retry_at": stop.retry_at, "fix": stop.fix,
            "since": prev.get("since", now) if same else now,
            "n": (prev.get("n") or 0) + 1 if same else 1}


def describe(rec: dict, now: float) -> str:
    """One line for the views (takes items.hold too)."""
    kind, n, at = rec.get("kind", "judgment"), rec.get("n") or 1, rec.get("retry_at")
    fix = (rec.get("fix") or "").replace("`", "")
    if kind == "lost":
        return f"lost — resumed ({n} in a row)"
    if kind == "transient":    # past LOST_CAP losses, n kept counting
        return f"transient — try {n % LOST_CAP if n > LOST_CAP else n} of {len(TRANSIENT_WAITS)} at {_clock(at, now)}"
    if kind == "quota":
        who = f"waits for {rec['provider']}, " if rec.get("provider") else ""
        return f"quota — {who}retries {_clock(at, now)}" + (f" ({fix})" if fix else "")
    if kind == "environment":
        return f"environment — fix: {fix}" + (f"; rechecks {_clock(at, now)}" if at else "")
    return "judgment — stopped; needs a decision"


def _clock(at: Optional[float], now: float) -> str:
    return "now" if at is None else time.strftime("%H:%M" if at - now < 20 * 3600 else "%a %H:%M", time.localtime(at))


def _transient(now: float, n: int) -> Stop:
    ok = 0 <= n < len(TRANSIENT_WAITS)
    return Stop("transient", now + TRANSIENT_WAITS[n], "") if ok else Stop("judgment", None, "")


def _environment(text: str, provider: str) -> str:
    """What is broken and the fix — only from stable parts of the message."""
    name = _NAMES.get(provider) or ("Codex" if "codex" in text.lower() else
                                    "Claude Code" if "/login" in text else provider or "the program")
    if _LOGIN.search(text):
        return _SIGNIN.get(name, f"{name} isn't signed in — sign in to it in Terminal")
    if m := _DENIED.search(text):
        what = f" to {m.group(1)}" if m.group(1) else ""
        return (f"macOS refused access{what} (Operation not permitted) — allow it in "
                "System Settings → Privacy & Security")
    m = _MISSING.search(text)
    if m and "/" in m.group(1):
        return f"folder {m.group(1)} is missing — restore it or `eki project …`"
    if m or (m := _NOCLI.search(text)):
        cli = m.group(1) or name
        return f"{cli} isn't installed or isn't on PATH — install it so `{cli}` runs in Terminal"
    if _MODEL.search(text):
        return f"{name}'s model or provider in config isn't available — pick another in Settings"
    return ""


def _quota(f: Failure) -> Stop:
    what = resettime.label(f.error)
    at, said = resettime.parse(f.error, f.tz, f.now)
    if f.reset_at is not None:
        at, said = f.reset_at, said or resettime.fmt(f.reset_at, f.tz)
    fix = f"{what} resets {said}" if at is not None else f"{what}, no reset time given"
    at = at if at is not None else f.now + QUOTA_BACKOFF[min(f.n, len(QUOTA_BACKOFF) - 1)]
    return Stop("quota", at, fix, at - (f.since if f.since is not None else f.now) > LONG_WAIT)
