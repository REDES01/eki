"""When a usage limit resets, read from what the program said.

Pure: no I/O. Claude Code says "You've hit your session limit · resets
4:30am (Asia/Tokyo)" (weekly: "resets Oct 16, 9am (…)" or with a weekday);
Codex says "… try again at 5:00 PM", "try again at Oct 15th, 2026 9:43 AM"
or "try again in 2 days 3 hours". eki/stopkind.py uses it.
"""
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

_RESETS = re.compile(r"resets\s+(?:at\s+|on\s+)?(?P<when>[^()·\n]*?)\s*(?:\((?P<tz>[\w/+\-]+)\))?\s*(?:[·\n]|$)", re.I)
_AGAIN = re.compile(r"try again (?:at|on)\s+(?P<when>.*?)\s*(?:\.\s|\.?$|\n)", re.I)
_IN = re.compile(r"try again in (.+)", re.I)
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b\.?|\b(\d{1,2}):(\d{2})\b", re.I)
_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                   r"(?:,?\s+(\d{4}))?", re.I)
_WDAY = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?\b", re.I)
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DAYS = "mon tue wed thu fri sat sun".split()
_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def label(text: str) -> str:
    """Which limit: "session limit", "weekly limit", else "usage limit"."""
    m = re.search(r"hit your (\w+ )?limit", text or "", re.I)
    return ((m.group(1) if m and m.group(1) else "usage ") + "limit").lower()


def parse(text: str, tz: Optional[str], now: float) -> Tuple[Optional[float], str]:
    """(when it resets, the words that said so); (None, "") when nothing
    names a time. A time with no zone is in `tz` (local when None)."""
    w = _RESETS.search(text or "") or _AGAIN.search(text or "")
    if w:
        zone = w.groupdict().get("tz")
        said = w.group("when") + (f" {zone}" if zone else "")
        at = _at(w.group("when"), zone or tz, now)
        if at is not None:
            return at, said
    m = _IN.search(text or "")
    if m:
        secs = sum(int(v) * _UNITS[u.lower()] for v, u in re.findall(r"(\d+)\s*([dhms])", m.group(1), re.I))
        if secs:
            return now + secs, "in " + m.group(1).rstrip(". ")
    return None, ""


def fmt(at: float, tz: Optional[str]) -> str:
    return datetime.fromtimestamp(at, zone(tz)).strftime("%a %H:%M")


def zone(name: Optional[str]):
    try:
        return ZoneInfo(name) if name else None
    except Exception:
        return None


def _at(when: str, tz: Optional[str], now: float) -> Optional[float]:
    """The next moment the words name: a time, maybe with a weekday or date.
    A time already past rolls to the next day; a weekday to its next one."""
    t = _TIME.search(when)
    if not t:
        return None
    if t.group(1):
        h, mi = int(t.group(1)) % 12 + (12 if t.group(3).lower() == "p" else 0), int(t.group(2) or 0)
    else:
        h, mi = int(t.group(4)), int(t.group(5))
    if h > 23 or mi > 59:
        return None
    base = datetime.fromtimestamp(now, zone(tz))
    at = base.replace(hour=h, minute=mi, second=0, microsecond=0)
    rest = when[:t.start()] + " " + when[t.end():]
    d, wd = _DATE.search(rest), _WDAY.search(rest)
    if d:
        try:
            at = at.replace(year=int(d.group(3) or base.year), month=_MONTHS.index(d.group(1).lower()) + 1,
                            day=int(d.group(2)))
        except ValueError:
            return None
        if at <= base and not d.group(3):
            at = at.replace(year=at.year + 1)
    elif wd:
        at += timedelta(days=(_DAYS.index(wd.group(1).lower()) - at.weekday()) % 7)
        if at <= base:
            at += timedelta(days=7)
    elif at <= base:
        at += timedelta(days=1)
    return at.timestamp()
