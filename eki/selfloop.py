# SPDX-License-Identifier: Apache-2.0
"""eki building eki, whenever the machine has room (docs/self-build.md, "The loop").

Self-work used to start only when someone asked (`eki self "…"`) or when a
fault had happened twice. This is the loop around it:

  what   an item: something you asked for (in a chat, on the board, with
         `eki self`), a fault eki noticed in its own code, the weekly note,
         or the next unchecked item in ROADMAP.md — in that order
  when   as a goal's turns: the goal "eki works on itself" gets turns like
         any goal, whenever the machine has room, inside its budget (by
         default only your subscriptions' spare room)
  how    eki/selfwork.py: its own worktree, an agent, the candidate check —
         a branch, a diff and a verdict
  then   the autonomy setting, per area: propose (you apply it, on the board
         or with `eki self apply`) or apply (swapped in, watched, rolled back
         if unhealthy)
  plan   a roadmap item is ticked once its change has landed, as a commit
         of its own (selfwork.write_ticks) — never in the change itself

Several at once (`self_parallel`, default 2), when there's room: each
subscription carries only what its spare room allows, the models on this
machine one between them; an item doesn't start beside another that works
in the same area (area_of, which looks its words up in the repo when it
names no file), and the Mac app is changed by one at a time. An item whose
area can't be told still goes beside the others — the merge queue sorts out
what collides — and only waits for another like it.
What they make is applied one after another, in the order it was finished
(the merge queue) — each put on top of what landed before it.

It never takes more of your attention than you give it: while
`self_review_max` fit changes wait for you, it starts nothing new except what
you asked for. And it doesn't try forever: an item whose change fails its
checks twice is left for you, and a roadmap item that ended with no change
(or the agent said it's a person's) isn't taken again unless its entry in
ROADMAP.md changes.

This module holds the items and the choices — no model, no engine. The
engine drives them (eki/selfengine.py).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import roadmap
from .selfwork import SUMMARY_LINE

HOME = Path("~/.eki/self").expanduser()
#: an item whose change failed its checks this many times is left for a person
MAX_ATTEMPTS = 2
#: fit changes waiting for you before the loop stops starting new ones
REVIEW_MAX = 3
#: the most self-work going at once (setting `self_parallel`)
PARALLEL = 2
#: the weekly note
NOTE_EVERY = 7 * 86400
SOURCES = ("asked", "fault", "note", "roadmap", "undo")
STATES = ("queued", "working", "review", "done", "person", "gave up", "dropped")
ITEM_LINE = re.compile(r"^\s*\**\s*ITEM\s*:\s*(done|partial|already|person)\b", re.I | re.M)
GOAL_TEXT = "Work on eki itself: fix what breaks, then the next item in ROADMAP.md"

_lock = threading.Lock()


@dataclass
class Item:
    id: str
    source: str                     # asked | fault | note | roadmap | undo
    title: str
    request: str = ""               # what the agent is asked to do (asked, fault)
    key: str = ""                   # a roadmap item's key, a fault's signature
    state: str = "queued"
    when: str = "later"             # asked: "now" (started at once) or "later" (the loop takes it)
    conversation: str = ""          # the thread it's worked in
    run: str = ""                   # the run working on it
    change: str = ""                # the change being made for it (selfwork id)
    changes: List[str] = field(default_factory=list)
    phase: str = ""                 # while working: "" | "agent" | "checking" | "merging"
    area: List[str] = field(default_factory=list)   # the lanes it's likely to touch (area_of)
    #: while working: the change in progress (a selfwork.Proposal), so a
    #: turn cut off — you came back, the engine restarted — carries on
    open: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    note: str = ""                  # why it stopped; what an earlier attempt got wrong; what's left
    #: a roadmap item: its entry as it read when last taken (entry_print) —
    #: one left for you after changing nothing is taken again only once that changes
    seen: str = ""
    base: str = ""                  # a fault fix starts from the code that's running
    check_base: bool = True
    apply: bool = False             # applied when fit, whatever the autonomy (asked with --apply)
    #: "eki": asked for from inside work eki started on its own (see `owner`)
    by: str = ""
    backend: str = ""               # asked for a backend by name
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


#: self-work eki opens on its own, with nobody asking: a fault in its code
EKI_ROOTS = ("fault",)


def owner(it: Item) -> str:
    """"eki" | "person" — whose authority a piece of self-work runs under.

    What eki starts on its own isn't the person's: it goes only as far as the
    autonomy setting lets it (never `apply` for itself), and nothing below it
    — the runs its agent starts through eki — may flip a switch only the
    person may. A roadmap item or the weekly note is a turn of the goal the
    person set up, so it is theirs."""
    return "eki" if it.source in EKI_ROOTS or it.by == "eki" else "person"


# ---- the store ---------------------------------------------------------------------

def _path(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "work.json"


def _load(home: Optional[Path] = None) -> List[Item]:
    try:
        raw = json.loads(_path(home).read_text()).get("items") or []
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for r in raw:
        try:
            out.append(Item(**{k: v for k, v in r.items() if k in Item.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def _save(items: List[Item], home: Optional[Path] = None) -> None:
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": [asdict(i) for i in items]}, indent=2, ensure_ascii=False))
    tmp.replace(path)


def items(home: Optional[Path] = None) -> List[Item]:
    return _load(home)


def get(iid: str, home: Optional[Path] = None) -> Item:
    found = [i for i in _load(home) if i.id == iid] or \
        [i for i in _load(home) if iid and i.id.startswith(iid)]
    if len(found) != 1:
        raise KeyError(iid)
    return found[0]


def by_key(key: str, source: str, home: Optional[Path] = None) -> Optional[Item]:
    return next((i for i in _load(home) if i.key == key and i.source == source), None)


def add(source: str, title: str, request: str = "", home: Optional[Path] = None,
        **fields: Any) -> Item:
    if source not in SOURCES:
        raise ValueError(f"no source {source!r}")
    it = Item(id=uuid.uuid4().hex[:10], source=source, title=title.strip()[:120] or request[:120],
              request=request.strip(), **{k: v for k, v in fields.items()
                                          if k in Item.__dataclass_fields__ and k not in ("id",)})
    with _lock:
        all_ = _load(home)
        all_.append(it)
        _save(all_, home)
    return it


def update(iid: str, home: Optional[Path] = None, **fields: Any) -> Item:
    with _lock:
        all_ = _load(home)
        it = next((i for i in all_ if i.id == iid), None)
        if it is None:
            raise KeyError(iid)
        for k, v in fields.items():
            if k in Item.__dataclass_fields__ and k != "id":
                setattr(it, k, v)
        it.updated_at = int(time.time())
        _save(all_, home)
    return it


def remove(iid: str, home: Optional[Path] = None) -> None:
    with _lock:
        _save([i for i in _load(home) if i.id != iid], home)


# ---- what to take next ------------------------------------------------------------

#: a roadmap item in one of these states isn't taken again on its own
SETTLED = ("working", "review", "done", "person", "gave up", "dropped")


def pick(roadmap_text: str, *, waiting: int = 0, review_max: int = REVIEW_MAX,
         live: Iterable[str] = (), home: Optional[Path] = None, parallel: int = 1,
         landed: Iterable[str] = (),
         files: Optional[Dict[str, str]] = None, root: Optional[Path] = None) -> Tuple[Optional[Item], str]:
    """The next piece of self-work, and why — or None and why not.

    `waiting`: fit changes waiting for you; `live`: runs still going (an item
    whose run is one of them is being worked on already); `parallel`: how
    many may be worked on at once; `files`: the repo's files by name, and
    `root`: the repo, for guessing an item's area (repo_files, area_of). An
    item doesn't start beside one whose area it shares; one whose change is
    only waiting its turn to be applied ("merging") takes no room. A roadmap
    item left for you after changing nothing comes back only once its entry
    in ROADMAP.md reads differently. `landed`: keys of roadmap items a change
    was applied for — never taken again on their own, ticked or not, unless
    left for you and reworded since."""
    live, landed = set(live), set(landed)
    all_ = _load(home)
    files = files or {}

    def guess(text: str) -> List[str]:
        return area_of(text, files, root)

    # something already begun, cut off (you came back, the engine restarted): carry on
    for it in all_:
        if it.state == "working" and not (it.run and it.run in live):
            return _with_area(it, roadmap_text, guess, home), "carrying on where it stopped"
    busy = [i for i in all_ if i.state == "working" and i.phase != "merging"]
    if len(busy) >= max(1, parallel):
        if len(busy) == 1:
            return None, f"working on “{busy[0].title[:60]}”"
        return None, f"working on {len(busy)} things at once: " + ", ".join(f"“{i.title[:40]}”" for i in busy)
    areas = {i.id: i.area or guess(text_of(i, roadmap_text)) for i in busy}
    held: List[Tuple[str, List[str], Item]] = []           # what waits for an area, and for whom

    def free(title: str, area: List[str]) -> bool:
        other = next((i for i in busy if overlaps(area, areas[i.id])), None)
        if other is not None and not held:
            held.append((title, area, other))
        return other is None

    queued = [i for i in all_ if i.state == "queued"]
    for it in (i for i in queued if i.source in ("asked", "undo")):
        area = it.area or guess(text_of(it, roadmap_text))
        if free(it.title, area):
            return update(it.id, home, area=area), "you asked for it"
    if waiting >= review_max:
        return None, (f"{waiting} change{'s' if waiting != 1 else ''} waiting for you to look at "
                      "(Self, or `eki self`) — nothing new until then")
    for source, why in (("fault", "a fault in eki's own code"), ("note", "the weekly note is due")):
        for it in (i for i in queued if i.source == source):
            area = it.area or guess(text_of(it, roadmap_text))
            if free(it.title, area):
                return update(it.id, home, area=area), why
    known = {i.key: i for i in all_ if i.source == "roadmap"}
    for entry in roadmap.workable(roadmap.parse(roadmap_text)):
        it = known.get(entry.key)
        seen = entry_print(entry.text)
        again = it is not None and it.state == "person" and bool(it.seen) and it.seen != seen
        if it is not None and it.state in SETTLED and not again:
            continue
        if entry.key in landed and not again:
            continue
        area = guess(f"{entry.title}\n{entry.text}")
        if not free(entry.title, area):
            continue
        if it is None:
            it = add("roadmap", entry.title, key=entry.key, home=home)
        fields: Dict[str, Any] = {"area": area, "seen": seen}
        if again:
            fields.update(state="queued", attempts=0, note="", open={}, phase="", run="")
        return update(it.id, home, **fields), \
            (f"its entry in ROADMAP.md changed since it was left for you ({entry.section.split(' — ')[0]})"
             if again else f"the next open item in ROADMAP.md ({entry.section.split(' — ')[0]})")
    if held:
        title, area, other = held[0]
        return None, (f"“{title[:60]}” waits: it touches {', '.join(lane_name(a) for a in area)}, "
                      f"like “{other.title[:60]}”, being worked on now")
    return None, "nothing to do — ROADMAP.md has no open item eki may take, and nothing is queued"


def _with_area(it: Item, roadmap_text: str, guess: Any, home: Optional[Path]) -> Item:
    if it.area:
        return it
    return update(it.id, home, area=guess(text_of(it, roadmap_text)))


def entry_print(text: str) -> str:
    """A roadmap entry as it reads, give or take spacing: has it changed since?"""
    return hashlib.sha1(" ".join(text.split()).encode()).hexdigest()[:10]


# ---- side by side: areas, room, and the merge queue --------------------------------
#
# Two agents changing the same files make two changes that don't go on top
# of each other. So before an item starts beside others, eki guesses where
# it will work — from the files it names, its words, and failing those
# where its words turn up in the repo — and waits while that area is taken.
# The guess only has to be good enough: what does collide is put on top of
# what landed first when it's applied, conflicts resolved (the merge queue,
# eki/selfengine.py). So an item whose area can't be told doesn't hold up
# the rest: it only waits for another like it.

#: path prefix → lane; the longest match wins, the rest of eki/ is the engine
LANES = {
    "mac/": "mac", "eki/web/": "board", "eki/cli.py": "cli", "docs/": "docs", "tests/": "tests",
    "README.md": "docs", "ROADMAP.md": "docs", "AGENTS.md": "docs",
    "eki/router.py": "routing", "eki/table.py": "routing", "eki/capacity.py": "routing",
    "eki/priors.py": "routing", "eki/handoff.py": "routing", "eki/classify.py": "routing",
    "eki/failover.py": "routing", "eki/policy.py": "routing", "eki/quota/": "routing",
    "eki/self": "self", "eki/candidate.py": "self", "eki/builds.py": "self", "eki/roadmap.py": "self",
    "eki/appbuild.py": "self", "eki/supervisor.sh": "self", "eki/": "engine",
}
LANE_NAMES = {"mac": "the Mac app", "board": "the board", "cli": "the command line", "docs": "the docs",
              "tests": "the tests", "routing": "routing", "self": "self-work", "engine": "the engine",
              "note": "the weekly note", "*": "somewhere it couldn't tell"}
#: words that say where a request works, when it names no file
_WORDS = (
    ("mac", re.compile(r"\b(mac app|eki\.app|the app|swift(ui)?|menu ?bar|dock icon|onboarding"
                       r"|preferences window|settings window)\b", re.I)),
    ("board", re.compile(r"\b(the board|goals board|self board|web page|goals\.html)\b", re.I)),
    ("cli", re.compile(r"\b(cli|command line|--[a-z][\w-]+)\b|`eki [a-z]+", re.I)),
    ("routing", re.compile(r"\b(rout(e|es|ed|ing|er)|capacity|quota|handoff|failover|spare room)\b", re.I)),
    ("self", re.compile(r"\b(self[- ](work|build|loop)|eki self|works? on itself|merge queue"
                        r"|candidate check|swaps?|builds?)\b", re.I)),
    ("engine", re.compile(r"\b(engine|runner|shift|goals?|providers?|adapters?|skills?|mcp)\b", re.I)),
    ("docs", re.compile(r"\b(docs?|documentation|readme)\b", re.I)),
    ("tests", re.compile(r"\btests?\b", re.I)),
)
_PATH = re.compile(r"\b(?:eki|mac|docs|tests)/[\w./-]*[\w/]|\b(?:README|ROADMAP|AGENTS)\.md\b")
_FILE = re.compile(r"\b([A-Za-z_]\w+\.(?:py|swift|md|html|sh))\b")
#: the words of an item worth looking up in the repo — not these
_TOKEN = re.compile(r"[a-z][a-z0-9]{3,}")
_COMMON = frozenset("""
    about above after again against also always another anything around because been before being
    below between both cannot change changed changes check could does doing done each either else
    even every fine first from have here into item items itself just keep kept like make makes many
    more most much must need needs never next nothing once only other others over real really right
    same should show shown since some something still such sure than that their them then there these
    they thing things this those through under until upon very want wants what when where which while
    will with without work works would your confirm
""".split())
#: a word in more files than this says little about where the work is
DISTINCT = 12
#: the repo's answers, for a while — the loop asks every turn
LOOK_TTL = 600
_looked: Dict[Tuple[str, str], Tuple[float, List[str]]] = {}


def lane_of(path: str) -> str:
    best = max((p for p in LANES if path == p or path.startswith(p)), key=len, default="")
    return LANES[best] if best else "engine"


def lane_name(lane: str) -> str:
    return LANE_NAMES.get(lane, lane)


def repo_files(root: Path) -> Dict[str, str]:
    """A cheap look at the repo: its source files, by name (lower case)."""
    out: Dict[str, str] = {}
    for pattern in ("eki/*.py", "eki/*/*.py", "eki/web/*", "mac/*.swift", "docs/*.md"):
        for p in sorted(Path(root).glob(pattern)):
            out.setdefault(p.name.lower(), str(p.relative_to(root)))
    return out


def words_of(text: str, limit: int = 12) -> List[str]:
    """An item's distinctive words, in order: swipe, trackpad — not fix, real."""
    out: List[str] = []
    for w in _TOKEN.findall(text.lower()):
        if w not in _COMMON and w not in out:
            out.append(w)
    return out[:limit]


def look(root: Path, word: str) -> List[str]:
    """The repo's files that name `word`: in their path, or in their text
    (git grep, whole words). Nothing when git can't say."""
    key, now = (str(root), word), time.time()
    got = _looked.get(key)
    if got and now - got[0] < LOOK_TTL:
        return got[1]
    try:
        named = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                               text=True, timeout=10).stdout.split()
        said = subprocess.run(["git", "-C", str(root), "grep", "-I", "-i", "-w", "-l", "-e", word],
                              capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []
    hits = sorted({p for p in named if word in p.lower().rsplit("/", 1)[-1]} | set(said))
    _looked[key] = (now, hits)
    return hits


def repo_lanes(text: str, root: Path) -> Tuple[List[str], List[str]]:
    """Where an item's words turn up in the repo: (lanes, hint). A word found
    in a handful of files counts one hit per file, a file named after it two
    more; the lane with most hits wins, a tie gives several. Words found all
    over say nothing on their own — only a hint of where it might be. The
    docs and the tests mention everything (the roadmap holds the item
    itself), so they aren't counted."""
    hits: Dict[str, int] = {}
    spread: Dict[str, int] = {}
    for word in words_of(text):
        paths = look(root, word)
        for path in paths:
            lane = lane_of(path)
            if lane in ("docs", "tests"):
                continue
            spread[lane] = spread.get(lane, 0) + 1
            if len(paths) <= DISTINCT:
                named = word in path.lower().rsplit("/", 1)[-1]
                hits[lane] = hits.get(lane, 0) + (3 if named else 1)
    return _most(hits), _most(spread)


def _most(counts: Dict[str, int]) -> List[str]:
    """The lanes with most hits — several when they tie."""
    top = max(counts.values(), default=0)
    return sorted(k for k, n in counts.items() if n == top and n > 0)


def text_of(it: Item, roadmap_text: str = "") -> str:
    """What an item says about itself: its title, its request, its roadmap entry."""
    if it.source == "note":
        return ""
    entry = roadmap.find(roadmap_text, it.key) if it.source == "roadmap" and roadmap_text else None
    return "\n".join(x for x in (it.title, it.request, entry.text if entry else "") if x)


def area_of(text: str, files: Optional[Dict[str, str]] = None, root: Optional[Path] = None) -> List[str]:
    """The lanes a piece of work is likely to touch: the paths and files it
    names, then the words it uses, then — given the repo's `root` — where
    its distinctive words turn up in the repo (repo_lanes). Docs and tests
    go with the code they're about — they're a lane of their own only when
    nothing else is named. Nothing to go on: ["*"], which waits only for
    another ["*"] — and ["*", "mac"] when the repo hints at the app, since
    there's one app to change at a time. The weekly note writes no code:
    ["note"]."""
    if not text.strip():
        return ["note"]
    files = files or {}
    found = {lane_of(m.group(0)) for m in _PATH.finditer(text)}
    for m in _FILE.finditer(text):
        path = files.get(m.group(1).lower())
        if path:
            found.add(lane_of(path))
    words = _FILE.sub(" ", _PATH.sub(" ", text))           # a path's words aren't what it's about
    found |= {lane for lane, rx in _WORDS if rx.search(words)}
    code = found - {"docs", "tests"}
    if code or found or root is None:
        return sorted(code or found) or ["*"]
    lanes, hint = repo_lanes(text, root)
    if lanes:
        return lanes
    return ["*", "mac"] if "mac" in hint else ["*"]


def overlaps(a: Iterable[str], b: Iterable[str]) -> bool:
    """Do two areas share a lane? One that couldn't be told ("*") shares
    only with another like it: what collides is sorted out when applied."""
    return bool(set(a) & set(b))


def room(parallel: int, slots: Dict[str, int], busy: Dict[str, int],
         local: Iterable[str] = ()) -> Tuple[int, List[str]]:
    """How many more pieces of self-work may start now, and on which backends.

    `slots`: how many self runs each subscription's spare room can carry
    (capacity.spare) — never the part kept for you; `busy`: self runs on
    each now. The models on this machine are counted apart: one self run
    between them, whatever the subscriptions have."""
    local = set(local)
    on_machine = sum(n for k, n in busy.items() if k in local)
    free, left = [], 0
    for k, n in slots.items():
        if k in local:
            if on_machine < 1:
                free.append(k)
            continue
        if n - busy.get(k, 0) > 0:
            free.append(k)
            left += n - busy.get(k, 0)
    if on_machine < 1 and any(k in local for k in free):
        left += 1
    more = max(0, int(parallel) - sum(busy.values()))
    return min(more, left), free


def _merge_path(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "merge.json"


def merge_queue(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Fit changes waiting their turn to be applied, in the order they were finished."""
    try:
        rows = json.loads(_merge_path(home).read_text()).get("queue") or []
        return [r for r in rows if isinstance(r, dict) and r.get("change")]
    except (OSError, ValueError, AttributeError):
        return []


def _save_merge(rows: List[Dict[str, Any]], home: Optional[Path] = None) -> None:
    path = _merge_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"queue": rows}, indent=2, ensure_ascii=False))
    tmp.replace(path)


def merge_join(change: str, *, item: str = "", title: str = "", run: str = "", area: Iterable[str] = (),
               home: Optional[Path] = None) -> int:
    """Into the merge queue, at the back — or where it already stood (a run
    carried on after a restart keeps its place). Returns how many are ahead."""
    with _lock:
        rows = merge_queue(home)
        mine = next((r for r in rows if r["change"] == change), None)
        if mine is None:
            rows.append({"change": change, "item": item, "title": title[:120], "run": run,
                         "area": list(area), "at": int(time.time()), "applying": False})
        else:
            mine["run"] = run or mine.get("run", "")
        _save_merge(rows, home)
        return next(n for n, r in enumerate(rows) if r["change"] == change)


def merge_turn(change: str, live: Iterable[str], home: Optional[Path] = None) -> bool:
    """Is it this change's turn? The first in line whose run is still going
    goes; one whose run was cut off keeps its place, and doesn't hold up the
    rest until it's carried on."""
    live = set(live)
    first = next((r for r in merge_queue(home) if r.get("run") in live), None)
    return first is not None and first["change"] == change


def merge_mark(change: str, home: Optional[Path] = None, **fields: Any) -> None:
    with _lock:
        rows = merge_queue(home)
        for r in rows:
            if r["change"] == change:
                r.update(fields)
        _save_merge(rows, home)


def merge_leave(change: str, home: Optional[Path] = None) -> None:
    with _lock:
        _save_merge([r for r in merge_queue(home) if r["change"] != change], home)


# ---- what the agent is told -------------------------------------------------------

def request_for(it: Item, roadmap_text: str = "") -> str:
    """The change to make, for eki/selfwork.brief (which adds how to work here)."""
    retry = (f"\n\nAn earlier attempt at this didn't pass eki's checks: {it.note}"
             if it.attempts and it.note else "")
    if it.source != "roadmap":
        return it.request + retry
    entry = roadmap.find(roadmap_text, it.key)
    section = entry.section if entry else ""
    text = entry.text if entry else it.title
    why = roadmap.intro(roadmap_text, section) if entry else ""
    return (
        f"Work on this item from eki's ROADMAP.md, under “{section}”:\n\n    {text}\n\n"
        + (f"What that part of the roadmap is for: {why}\n\n" if why else "")
        + "Read ROADMAP.md first — the vision and “What doesn't change” are rules every change "
        "keeps — then the docs/ and the code the item touches.\n"
        "- The file can lag behind the code. If the item is already done, change nothing and "
        "say where it's done.\n"
        "- If it can't be done as a change to this repository by an agent working alone — it "
        "needs a person's hands or real hardware (trying it on a real device, a trackpad, a "
        "screen), an account, money, a release, or a decision only the person can make — change "
        "nothing, say why, and end with `ITEM: person`; it isn't tried again.\n"
        "- If it's too big for one change a person can review in a sitting, do the first slice "
        "that stands on its own, and say what's left.\n"
        "- Don't tick ROADMAP.md yourself; eki ticks it when your change lands.\n"
        "- End your final message with one line: `ITEM: done` (this change completes the item), "
        "`ITEM: partial` (a slice; more to do), `ITEM: already` (it was done before), or "
        "`ITEM: person` (not for an agent)."
        + retry)


def said(answer: str) -> str:
    """The agent's ITEM line: done | partial | already | person, or ""."""
    found = ITEM_LINE.findall(answer or "")
    return found[-1].lower() if found else ""


def reason(answer: str, limit: int = 600) -> str:
    """What the agent said above its ITEM line — why it's a person's, what's
    left — cut at the end of a sentence, not in the middle of a word."""
    # the plain-words summary for you is shown on its own (eki/selfwork.summary_of)
    found = list(SUMMARY_LINE.finditer(answer or ""))
    if found:
        answer = answer[:found[-1].start()]
    lines = [x.strip() for x in (answer or "").splitlines() if x.strip() and not ITEM_LINE.match(x)]
    text = " ".join(lines[-3:])
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 3 else cut.rstrip() + "…"


# ---- after the verdict: where the item stands --------------------------------------

def failed_check(change: Dict[str, Any]) -> str:
    """"tests: 3 failed, …" — what the next attempt should know."""
    for c in (change.get("report") or {}).get("checks") or []:
        if not c.get("ok") and not c.get("skipped"):
            return f"{c.get('name')}: {str(c.get('detail') or '')[:300]}"
    return ""


def after_change(it: Item, change: Dict[str, Any], home: Optional[Path] = None) -> Item:
    """An item follows its change: waiting for you, done, or tried again."""
    try:
        it = get(it.id, home)                   # as it is now, not as the caller last saw it
    except KeyError:
        return it
    state = change.get("state") or ""
    said_ = change.get("said") or ""
    fields: Dict[str, Any] = {"change": change.get("id", it.change), "phase": "", "run": ""}
    if change.get("id") and change["id"] not in it.changes:
        fields["changes"] = it.changes + [change["id"]]
    if state in ("proposed", "conflicts", "applying", "undoing"):
        fields["state"] = "review"
        if state == "conflicts":
            fields["note"] = change.get("why") or "it doesn't go on top of your checkout any more"
    elif state == "applied":
        if it.source == "roadmap" and said_ == "partial":   # not taken again until its entry changes
            fields.update(state="person", note="a first slice landed; what's left is yours — "
                                               "reword its entry in ROADMAP.md to have eki take it again")
        else:
            fields["state"] = "done"
    elif state in ("discarded", "undone", "gone"):
        fields.update(state="dropped", note={"undone": "you took it back", "gone": "its branch was deleted"}
                      .get(state, "you discarded its change"))
    elif state in ("unfit", "rolled back", "stopped"):
        attempts = it.attempts + 1
        why = change.get("why") or failed_check(change) or change.get("verdict") or state
        if it.source in ("asked", "undo"):
            fields.update(state="done", attempts=attempts, note=why[:300])
        elif attempts >= MAX_ATTEMPTS:
            fields.update(state="gave up", attempts=attempts,
                          note=f"{attempts} attempts didn't pass eki's checks — left for you: {why}"[:400])
        else:
            fields.update(state="queued", attempts=attempts, note=why[:400])
    elif state in ("no change", "not started"):
        if it.source == "roadmap" and said_ == "person":
            fields.update(state="person", note=change.get("why") or "not for an agent")
        elif it.source == "roadmap" and said_ in ("already", "done"):
            fields.update(state="done", note="already done")
        elif state == "not started":
            fields.update(state="queued" if it.source != "asked" else "done",
                          note=(change.get("verdict") or "")[:300])
        elif it.source == "roadmap":        # not taken again until its entry changes
            fields.update(state="person", note=change.get("why") or "the agent changed nothing")
        else:
            fields.update(state="done" if it.source in ("asked", "undo", "fault") else "gave up",
                          note="the agent changed nothing")
    return update(it.id, home, **fields)


# ---- a chat message that asks eki to change itself --------------------------------

_YOURSELF = re.compile(r"\b(change|improve|fix|update|modify|upgrade|rewrite|evolve|extend|teach|rebuild)"
                       r"\s+yourself\b", re.I)
_OWN = re.compile(r"\b(your|eki'?s)\s+own\s+(code|source|codebase|app|ui|interface)\b"
                  r"|\beki'?s\s+(code|source|codebase|repo)\b", re.I)
_AT_EKI = re.compile(r"^\W*(hey\s+|ok\s+)?eki\s*[,:]\s*(please\s+|can you\s+|could you\s+)?"
                     r"(change|make|fix|add|let|teach|give|improve|update|stop|remove|rename|show|have"
                     r"|allow|support|build|implement|redesign|move|put|drop|hide|sort)\b", re.I)
_PARTS = re.compile(r"\b(the|your)\s+(chat list|thread list|sidebar|rail|goals? board|board|self (view|pane)"
                    r"|menu ?bar|router|routing table|engine|composer|gallery|artifacts? (panel|pane)"
                    r"|settings( pane| window)?|model picker|picker|command line|`?eki`? command|cli"
                    r"|dock icon|notifications?|usage (pane|meter)s?|app|ui|interface)\b", re.I)
_NOT_EKI = re.compile(r"(~/|/Users/|\b(my|our|this) (app|project|game|repo|site|website|code)\b)", re.I)
_LATER = re.compile(r"\b(tonight|overnight|later|in the background|when (you have|there'?s|you've got) "
                    r"(time|room))\b", re.I)


def addressed(prompt: str) -> str:
    """"now" | "later" | "" — whether a chat message asks eki to change itself:
    "eki, make the chat list show the project name", "improve yourself so…"."""
    text = (prompt or "").strip()
    if not text or len(text) > 2000 or _NOT_EKI.search(text):
        return ""
    if not (_YOURSELF.search(text) or _OWN.search(text) or (_AT_EKI.search(text) and _PARTS.search(text))):
        return ""
    return "later" if _LATER.search(text) else "now"


# ---- how far it goes alone -------------------------------------------------------

def autonomy_for(files: List[str], settings: Dict[str, Any]) -> str:
    """"apply" | "propose" for a change touching `files`.

    `self_autonomy` is the default; `self_autonomy_areas` maps a path or
    folder ("docs/", "eki/web/", "ROADMAP.md") to its own setting, the
    longest match winning. A change is applied only if every file it
    touches may be."""
    base = str(settings.get("self_autonomy") or "propose")
    areas = settings.get("self_autonomy_areas") or {}
    if not files:
        return base if base in ("apply", "propose") else "propose"
    modes = []
    for f in files:
        best: Tuple[int, str] = (-1, base)
        for prefix, mode in areas.items():
            p = str(prefix).strip()
            if p and (f == p or f.startswith(p if p.endswith("/") else p + "/")) and len(p) > best[0]:
                best = (len(p), str(mode))
        modes.append(best[1])
    return "apply" if all(m == "apply" for m in modes) else "propose"


# ---- the weekly note -------------------------------------------------------------

def notes_dir(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "notes"


def notes(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(notes_dir(home).glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def latest_note(home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    got = notes(home)
    return got[-1] if got else None


def note_due(now: Optional[float] = None, home: Optional[Path] = None, since: float = 0) -> bool:
    """A week since the last note — or, before the first, since `since` (when
    eki started working on itself): a note needs a week to write about."""
    last = latest_note(home)
    queued = any(i.source == "note" and i.state in ("queued", "working") for i in _load(home))
    ref = float(last.get("at") or 0) if last else float(since or 0)
    return not queued and (now or time.time()) - ref >= NOTE_EVERY


def note_prompt(evidence: Dict[str, Any]) -> str:
    return (
        "You write eki's weekly note to the person whose Mac it runs on. eki is a light router "
        "between the person and every model and agent they use — local models, Claude Code, "
        "Codex — that keeps the machine working on their goals whenever it has room, and works "
        "on its own code too. It builds on what exists: models from providers, tools from MCP "
        "servers, instructions from skills.\n\n"
        "Here is what eki recorded this week, as JSON:\n\n"
        f"```json\n{json.dumps(evidence, indent=1, ensure_ascii=False)[:14000]}\n```\n\n"
        "Write, in plain English, short:\n"
        "1. What eki did this week and what it noticed — three to six sentences, with the numbers "
        "that matter (how much the local models worked; what failed; what the person had to "
        "correct).\n"
        "2. Two or three suggestions. Each: what to do, the evidence for it (with counts), and "
        "what it would take. Prefer adding a provider or an MCP server, or changing a setting, "
        "over building something into eki. Don't repeat the ROADMAP's next items unless the "
        "evidence says one matters more than its place says.\n\n"
        "Then, at the very end, the same suggestions as JSON in a ```json block — a list of "
        '{"title": "…", "why": "…", "kind": "provider" | "server" | "setting" | "roadmap" | '
        '"change", "do": "the request eki would be given, or the ROADMAP item\'s text", '
        '"stage": "for kind roadmap: the ROADMAP section it belongs under"}.\n'
        "Nothing is done without the person picking it; don't write as if it were."
    )


_JSON_BLOCK = re.compile(r"```json\s*(.*?)```", re.S | re.I)


def parse_note(answer: str) -> Tuple[str, List[Dict[str, Any]]]:
    """The note to show, and its suggestions — [] if the JSON can't be read."""
    blocks = list(_JSON_BLOCK.finditer(answer or ""))
    if not blocks:
        return (answer or "").strip(), []
    last = blocks[-1]
    text = ((answer or "")[:last.start()] + (answer or "")[last.end():]).strip()
    try:
        raw = json.loads(last.group(1))
    except ValueError:
        return text, []
    if isinstance(raw, dict):
        raw = raw.get("suggestions") or []
    out = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict) or not str(s.get("title") or "").strip():
            continue
        kind = str(s.get("kind") or "change").lower()
        out.append({"title": str(s["title"]).strip()[:160], "why": str(s.get("why") or "").strip()[:600],
                    "kind": kind if kind in ("provider", "server", "setting", "roadmap", "change") else "change",
                    "do": str(s.get("do") or s["title"]).strip()[:800],
                    "stage": str(s.get("stage") or "").strip()[:120], "picked": ""})
    return text, out[:5]


def save_note(text: str, suggestions: List[Dict[str, Any]], *, run: str = "", conversation: str = "",
              backend: str = "", home: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    nid = time.strftime("%Y-%m-%d", time.localtime(now))
    note = {"id": nid, "at": int(now), "text": text, "suggestions": suggestions, "run": run,
            "conversation": conversation, "backend": backend}
    d = notes_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{nid}.json").write_text(json.dumps(note, indent=2, ensure_ascii=False))
    return note


def pick_suggestion(nid: str, index: int, picked: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """Mark what the person did with a suggestion: "asked", "roadmap", "dismissed"."""
    path = notes_dir(home) / f"{nid}.json"
    note = json.loads(path.read_text())
    sugg = note.get("suggestions") or []
    if not 0 <= index < len(sugg):
        raise KeyError(index)
    sugg[index]["picked"] = picked
    path.write_text(json.dumps(note, indent=2, ensure_ascii=False))
    return sugg[index]
