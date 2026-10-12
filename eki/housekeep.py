"""Housekeeping: the engine's duty pass over the journal and what is built from it.

Each step runs on its own — one that raises is written down as a fault and
the others still run: forget journal rows past 90 days (at most once an
hour), settle the build scores, turn repeated faults into items, let the
loop pick eki's next self-work when it is on and eki is idle
(eki/selfpick.py), open the next round of each standing goal (eki/standing.py), finish a project
delete a restart interrupted (eki/projectdelete.py), settle eki's own
clones of GitHub repos — clone, guessed check, install once, retired folder
rows (eki/projectsetup.py, every pass), fill the branch of a project whose
folder got its first commit after it said where its work lands (eki/projectlands.py) — take issues in and hand PRs out on GitHub (eki/issues.py, prs.py, prfollow.py,
prmirror.py — at most every self.issues_minutes), write the daily digest when it is due and give the
local model its patch notes and triage (eki/digestprose.py), queue a notice for each goal
that ended (eki/notifygoals.py), fire the scheduled prompts that are due and
queue a notice when one's chat ends (eki/schedule.py), and push the
queued notices to the person's phone through ntfy (eki/notify.py), keep the
chat list (eki/threadkeep.py: mark eki's own threads, purge deleted ones,
title new chats), and read a
program's model list again when it is a day old (eki/modellist.py — on a thread
of its own with a 20 s limit, so the pass never waits on it), and stop each
component server eki started that has sat idle past its `idle_stop`
(eki/components/servers.py) — and each `serve` server whose folder is gone,
its port freed (eki/components/serve.py), and run each direction of the Claude-client
sync (eki/syncstate.py), and measure every assets pool's size on a thread of its
own at most every five minutes, so no request ever walks one
(eki/components/assets.py), and copy each pool's new and changed files to its
card's backup folder, if it has one, on a thread of its own as often
(eki/components/assetsbackup.py), and move what an older eki left in EKI_HOME/work
into work.noindex, out of Spotlight's way (eki/noindex.py), and remove the worktrees under EKI_HOME/work.noindex that
are done with, a few a pass, and measure the disk they take
(eki/worktreegc.py), and keep the browser shim's mirror of Playwright's browsers
folder current, so a newly installed browser version waits for a slot too
(eki/browsershim.py), and note browsers open in eki worktrees that hold no
browser slot (eki/browserstrays.py), and measure the peak memory of each
run's, browser's and test hold's process tree and learn each kind's estimate
from it (eki/memlearn.py), and let the local classifier look at each new
picture once and mark it sensitive or not (eki/picturecheck.py). Nothing here is
state that matters: the hour between prunes and the last list tries live in
memory, and losing them on a restart only means an early prune or an early try.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Callable, Dict, List, Tuple

from . import (browsershim, browserstrays, db, digest, digestprose, faults, issues, memlearn, modellist, noindex, notify, notifygoals, observe, picturecheck,
               prfollow, prmirror, projectdelete, projectlands, projectsetup, prs, schedule, score, selfpick, standing, syncstate,
               threadkeep, worktreegc)
from .components import assets, assetsbackup, serve, servers

log = logging.getLogger("eki.housekeep")

#: how long the journal keeps a row
KEEP_DAYS = 90
#: how often the journal is pruned
PRUNE_EVERY = 3600.0
_last_prune = [0.0]


def _prune(conn: sqlite3.Connection) -> List[str]:
    if time.time() - _last_prune[0] < PRUNE_EVERY:
        return []
    _last_prune[0] = time.time()
    gone = observe.prune(conn, KEEP_DAYS)
    return [f"journal: forgot {gone} rows older than {KEEP_DAYS} days"] if gone else []


def _digest(conn: sqlite3.Connection) -> List[str]:
    now = db.now()
    page = digest.tick(conn, now)
    if not page:
        return []
    since = digest.window_start(now)        # the window digest.write took (kept beside the page)
    rid = digestprose.start(conn, page, since, now)
    return [f"digest written: {page}"] + ([f"digest prose asked of the local model: run {rid}"] if rid else [])


#: how long after a try (good or not) a provider's list isn't tried again
LIST_RETRY = 3600.0
_list_tried: Dict[str, float] = {}


def _read_list(name: str) -> None:
    c = db.connect()                                     # a thread of its own needs its own connection
    try:
        modellist.refresh(c, name, timeout=modellist.TIMEOUT)
    except Exception as e:                               # noqa: BLE001 — a list is never worth a fault
        log.info("model list of %s not read: %s", name, e)
    finally:
        c.close()


def _models(conn: sqlite3.Connection) -> List[str]:
    """Start a thread for each list that is a day old; never wait for one."""
    said = []
    now = time.time()
    for name in modellist.stale(conn):
        if now - _list_tried.get(name, 0.0) < LIST_RETRY:
            continue
        _list_tried[name] = now
        threading.Thread(target=_read_list, args=(name,), name=f"eki-models-{name}", daemon=True).start()
        said.append(f"models: reading {name}'s list")
    return said


STEPS: List[Tuple[str, Callable[[sqlite3.Connection], List[str]]]] = [
    ("prune", _prune),
    ("score", lambda conn: score.settle(conn)),
    ("faults", lambda conn: faults.tick(conn)),
    ("pick", lambda conn: selfpick.tick(conn)),
    ("standing", lambda conn: standing.tick(conn)),
    ("projectdelete", projectdelete.tick),
    ("projects", projectsetup.tick),
    ("lands", projectlands.tick),
    ("issues", issues.tick),
    ("prs", prs.tick),
    ("prfollow", prfollow.tick),
    ("prmirror", prmirror.tick),
    ("digest", _digest),
    ("digestprose", digestprose.tick),
    ("models", _models),
    ("threads", threadkeep.tick),
    ("serveports", serve.tick),
    ("componentservers", servers.tick),
    ("sync", syncstate.tick),
    ("poolsizes", assets.tick),
    ("poolbackup", assetsbackup.tick),
    ("picturecheck", picturecheck.tick),
    ("noindex", noindex.migrate),
    ("worktrees", worktreegc.tick),
    ("browsershim", lambda conn: browsershim.ensure() and []),
    ("browserstrays", lambda conn: browserstrays.note()),
    ("memory", memlearn.tick),
    ("schedule", schedule.tick),
    ("schedulenotify", schedule.settle),
    ("notifygoals", notifygoals.tick),
    ("notify", notify.tick),
]


def tick(conn: sqlite3.Connection) -> List[str]:
    """One pass; returns what it did, one line each, for the engine's log."""
    said: List[str] = []
    for name, step in STEPS:
        try:
            said.extend(step(conn) or [])
        except Exception:                                # noqa: BLE001 — one failing never stops the others
            log.exception("housekeeping step %s failed", name)
            observe.fault(conn, f"housekeep.{name}")
    return said
