"""The local classifier's look at each new picture: marks it sensitive or not.

Each picture eki drew (gallery.pictures) is looked at once by NudeNet — an
open ONNX nudity detector that runs on this Mac's CPU, never a cloud — when
it can be imported (`pip install nudenet`, the `pictures` extra). A picture
is 'on' when an exposed-nudity class scores THRESHOLD or more, 'off'
otherwise; either way it is marked by="auto" and never looked at again. A
person's mark always wins (sensitive.mark keeps it). A picture that can't be
read is journaled and marked 'off' with no score, so the queue never sticks.

Nothing here is state that matters: the detector, the note that NudeNet is
missing and how far back every picture was seen live in memory; a restart
only means one more walk of the gallery and one more note.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, List, Optional

from . import gallery, observe, sensitive

log = logging.getLogger("eki.picturecheck")

#: the most pictures looked at in one pass
BATCH = 20
#: a picture is sensitive when an exposed class scores this or more
THRESHOLD = 0.6
#: NudeNet's classes that make a picture sensitive
EXPOSED = ("FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "FEMALE_BREAST_EXPOSED",
           "ANUS_EXPOSED", "BUTTOCKS_EXPOSED")
#: how many gallery pictures are read at a time
_PAGE = 60

_detector: List[Any] = []
_said_missing = [False]
#: every picture drawn at or before this time has a mark (in memory only)
_seen_upto: List[Optional[float]] = [None]


def available() -> bool:
    """Whether NudeNet can be imported here."""
    try:
        import nudenet  # noqa: F401
    except Exception:                                    # noqa: BLE001 — missing or broken: off either way
        return False
    return True


def _classify(path: str) -> float:
    """The highest exposed-nudity score NudeNet gives the picture; 0.0 for none."""
    if not _detector:
        from nudenet import NudeDetector
        _detector.append(NudeDetector())
    found = _detector[0].detect(path) or []
    return max((float(d.get("score") or 0.0) for d in found if d.get("class") in EXPOSED), default=0.0)


def _unseen(conn: sqlite3.Connection) -> List[str]:
    """Up to BATCH pictures with no mark, newest first."""
    out: List[str] = []
    before: Optional[float] = None
    newest: Optional[float] = None
    upto = _seen_upto[0]
    while len(out) < BATCH:
        page = gallery.pictures(conn, limit=_PAGE, before=before)
        for p in page:
            newest = p["created_at"] if newest is None else newest
            if upto is not None and p["created_at"] <= upto:
                break
            if not sensitive.seen(conn, p["path"]):
                out.append(p["path"])
                if len(out) >= BATCH:
                    return out
        else:
            if len(page) == _PAGE:
                before = page[-1]["created_at"]
                continue
        break
    if newest is not None:                               # every picture down to the last one read has a mark,
        _seen_upto[0] = newest if not out else None      # or will once this pass marks `out`
    return out


def tick(conn: sqlite3.Connection) -> List[str]:
    """One pass: classify up to BATCH unmarked pictures; what it did, one line each."""
    if not available():                                  # said once, and only when there is a picture to look at
        if not _said_missing[0] and _unseen(conn):
            _said_missing[0] = True
            observe.record(conn, "picturecheck", data={"note": "automatic sensitive marking off: pip install nudenet"})
        return []
    said: List[str] = []
    todo = _unseen(conn)
    for path in todo:
        try:
            score: Optional[float] = _classify(path)
        except Exception as e:                           # noqa: BLE001 — one picture never sticks the queue
            log.info("picture %s not classified: %s", path, e)
            observe.record(conn, "picturecheck", data={"note": f"could not classify {path}: {e}", "path": path})
            score = None
        on = score is not None and score >= THRESHOLD
        if sensitive.mark(conn, path, on, by="auto", score=score) and on:
            said.append(f"pictures: marked sensitive {path} ({score:.2f})")
    return said
