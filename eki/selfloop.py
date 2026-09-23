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
  plan   a roadmap item's change ticks the item, in the same commit

It never takes more of your attention than you give it: while
`self_review_max` fit changes wait for you, it starts nothing new except what
you asked for. And it doesn't try forever: an item whose change fails its
checks twice is left for you.

This module holds the items and the choices — no model, no engine. The
engine drives them (eki/selfengine.py).
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import roadmap

HOME = Path("~/.eki/self").expanduser()
#: an item whose change failed its checks this many times is left for a person
MAX_ATTEMPTS = 2
#: fit changes waiting for you before the loop stops starting new ones
REVIEW_MAX = 3
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
    phase: str = ""                 # while working: "" | "agent" | "checking"
    #: while working: the change in progress (a selfwork.Proposal), so a
    #: turn cut off — you came back, the engine restarted — carries on
    open: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    note: str = ""                  # why it stopped; what an earlier attempt got wrong; what's left
    base: str = ""                  # a fault fix starts from the code that's running
    check_base: bool = True
    apply: bool = False             # applied when fit, whatever the autonomy (asked with --apply)
    backend: str = ""               # asked for a backend by name
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


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
         live: Iterable[str] = (), home: Optional[Path] = None) -> Tuple[Optional[Item], str]:
    """The next piece of self-work, and why — or None and why not.

    `waiting`: fit changes waiting for you; `live`: runs still going (an item
    whose run is one of them is being worked on already)."""
    live = set(live)
    all_ = _load(home)
    # something already begun, cut off (you came back, the engine restarted): carry on
    for it in all_:
        if it.state == "working":
            if it.run and it.run in live:
                return None, f"working on “{it.title[:60]}”"
            return it, "carrying on where it stopped"
    queued = [i for i in all_ if i.state == "queued"]
    asked = [i for i in queued if i.source in ("asked", "undo")]
    if asked:
        return asked[0], "you asked for it"
    if waiting >= review_max:
        return None, (f"{waiting} change{'s' if waiting != 1 else ''} waiting for you to look at "
                      "(Self, or `eki self`) — nothing new until then")
    for source, why in (("fault", "a fault in eki's own code"), ("note", "the weekly note is due")):
        found = [i for i in queued if i.source == source]
        if found:
            return found[0], why
    known = {i.key: i for i in all_ if i.source == "roadmap"}
    for entry in roadmap.workable(roadmap.parse(roadmap_text)):
        it = known.get(entry.key)
        if it is not None and it.state in SETTLED:
            continue
        if it is None:
            it = add("roadmap", entry.title, key=entry.key, home=home)
        return it, f"the next open item in ROADMAP.md ({entry.section.split(' — ')[0]})"
    return None, "nothing to do — ROADMAP.md has no open item eki may take, and nothing is queued"


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
        "needs a person, hardware, an account, money, a release, or a decision only the person "
        "can make — change nothing and say why.\n"
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


def reason(answer: str) -> str:
    """What the agent said above its ITEM line — why it's a person's, what's left."""
    lines = [x.strip() for x in (answer or "").splitlines() if x.strip() and not ITEM_LINE.match(x)]
    return " ".join(lines[-3:])[:400]


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
        if it.source == "roadmap" and said_ == "partial":
            fields.update(state="queued", attempts=0,
                          note="a first slice landed; the rest is still open")
        else:
            fields["state"] = "done"
    elif state in ("discarded", "undone"):
        fields.update(state="dropped", note=f"you {'took it back' if state == 'undone' else 'discarded its change'}")
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
