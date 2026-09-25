# SPDX-License-Identifier: Apache-2.0
"""ROADMAP.md, read and kept by eki (the self-build track).

The roadmap is the one plan, written for people: sections (`## Stage 3 —
Work while you're away`) and, under them, items —

    - [ ] **Goals are sentences.** What you'd type in a chat, left running…
          indented lines carry the item on

eki reads it to find the next thing to work on in itself, by what an item
is worth for the next release rather than where it sits (`ranked`), and ticks an item once the change that finished it has landed,
naming that change — one small commit of its own, written by the merge queue
alone (eki/selfengine.py, `_self_ticks`), and taken back if the change is
undone. A change never carries a tick in its own commit (a tick an agent
made anyway is dropped: `without_new_ticks`), so a tick is never what makes
two changes conflict — and never takes one back either: a tick it would
lose is put back before it lands (`keep_ticks`), and the merge queue ticks
a landed item again whenever the file shows it open.

Never taken: an item that says `(for a person)` anywhere in it, and
anything under *Not planned* or *Keeping this file*. Not taken yet: an item
that says `(waiting on …)` — it needs something else to land first.

Inside a stage (a `## Stage …` section) the items are in order: an open item
waits until every open item above it has landed, because the one below
usually builds on it — two agents each inventing the memory store the first
item was to make clash when they're applied. An item marked `(independent)`
neither waits nor holds anything up. Later stages don't wait on earlier ones
(`ranked` puts the release's stages first), and items in any other
section — *Alongside every stage*, *What eki keeps current* — are
independent anyway.

What an item is worth (`worth`), most first: named in the file's list of
what's *Left for the first release*, in that list's order; then what moves
the score — the local models' share of the work, redos, overrides; then the
rest of the release — its stages, as the vision names them ("the first
public release is Stages 1–3"), and *Where it stands*; then the other
sections; then the self-build machinery (*Alongside every stage — eki
builds eki*), which is only worth it if the rest moves; then the stages
after the release. Inside each, the file's order.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

NAME = "ROADMAP.md"
BOX = re.compile(r"^- \[( |x|X)\] (.*)$")
SECTION = re.compile(r"^##\s+(.*?)\s*$")
PERSON = re.compile(r"\(for a person\b[^)]*\)", re.I)
WAITING = re.compile(r"\(waiting on\b[^)]*\)", re.I)
INDEPENDENT = re.compile(r"\(independent\)", re.I)
#: sections whose bullets are never work
SKIP = ("not planned", "keeping this file")
#: "The first public release** is Stages 1–3" — which stages the release is
RELEASE = re.compile(r"release\**\s+is\s+stages?\s+(\d+)(?:\s*[–—-]\s*(\d+))?", re.I)
#: the list of what's left for the release, and an entry of it: "2. *Handoff…* (Stage 2)"
LEFT = re.compile(r"^\**left for the (?:first |next )?(?:public )?release", re.I)
NAMED = re.compile(r"^\s*\d+\.\s+\*([^*]+)\*")
#: what moves the score (docs/observe.md, the weekly note): the local share, redos, overrides
SCORE = re.compile(r"\b(local share|share of (?:the )?work|useful local work|redos?|overrides?)\b", re.I)
#: the self-build machinery's own section
BUILDER = "alongside every stage"


@dataclass
class Item:
    key: str            # from the title, so reordering the file keeps it
    section: str
    title: str
    text: str           # the whole item, on one line
    done: bool
    person: bool        # marked for a person: never taken
    start: int          # line index of "- [ ]"
    end: int            # its last line, inclusive
    order: int

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def title_of(first: str) -> str:
    """The bold lead-in if there is one, else the first sentence."""
    m = re.match(r"\s*\*\*(.+?)\*\*", first)
    if m:
        return m.group(1).strip().rstrip(".:").strip()
    first = re.sub(r"\s*\*?\(for a person[^)]*\)\*?", "", first)
    sentence = re.split(r"(?<=[.;:])\s", first.strip(), maxsplit=1)[0]
    return sentence[:100].strip().rstrip(".:;").strip()


def key_of(title: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:10]


def parse(text: str) -> List[Item]:
    """Every checkbox item, in file order."""
    lines = text.splitlines()
    items: List[Item] = []
    section = ""
    cur: Optional[Dict[str, Any]] = None

    def finish() -> None:
        nonlocal cur
        if cur is None:
            return
        joined = " ".join(p for p in cur["parts"] if p)
        title = title_of(cur["parts"][0])
        items.append(Item(key=key_of(title), section=cur["section"], title=title, text=joined,
                          done=cur["done"], person=bool(PERSON.search(joined)),
                          start=cur["start"], end=cur["end"], order=len(items)))
        cur = None

    for i, line in enumerate(lines):
        head = SECTION.match(line)
        if head:
            finish()
            section = head.group(1)
            continue
        box = BOX.match(line)
        if box:
            finish()
            cur = {"section": section, "done": box.group(1) != " ", "parts": [box.group(2).strip()],
                   "start": i, "end": i}
            continue
        if cur is not None and line.startswith("  ") and line.strip():
            cur["parts"].append(line.strip())
            cur["end"] = i
            continue
        finish()
    finish()
    return items


def workable(items: Iterable[Item]) -> List[Item]:
    """What eki may take: open, not a person's, not waiting, not under Not planned."""
    return [i for i in items if not i.done and not i.person and not WAITING.search(i.text)
            and not any(i.section.lower().startswith(s) for s in SKIP)]


def ordered(section: str) -> bool:
    """A stage: its items are taken in the order the file gives them."""
    return section.lower().startswith("stage")


def after(items: Iterable[Item], item: Item, landed: Iterable[str] = ()) -> Optional[Item]:
    """The open item above `item` in its stage it waits for — the nearest —
    or None if it may start. An item waits on every open one above it that
    hasn't landed (`landed`: keys a change was applied for, ticked or not),
    unless one of the two is marked `(independent)` or the section isn't a
    stage."""
    if not ordered(item.section) or INDEPENDENT.search(item.text):
        return None
    landed = set(landed)
    above = [i for i in items if i.section == item.section and i.order < item.order
             and not i.done and i.key not in landed and not INDEPENDENT.search(i.text)]
    return above[-1] if above else None


def stage_of(section: str) -> Optional[int]:
    m = re.match(r"stage\s+(\d+)", section.strip(), re.I)
    return int(m.group(1)) if m else None


def release_stages(text: str) -> Optional[range]:
    """The stages the next release is, as the file says — or None if it doesn't."""
    m = RELEASE.search(text)
    if not m:
        return None
    lo = int(m.group(1))
    return range(lo, int(m.group(2) or lo) + 1)


def left_for_release(text: str) -> List[str]:
    """The titles in the file's "Left for the first release" list, in its order."""
    out: List[str] = []
    inside = False
    for line in text.splitlines():
        if LEFT.match(line.strip()):
            inside = True
            continue
        if not inside:
            continue
        m = NAMED.match(line)
        if m:
            out.append(m.group(1).strip())
        elif out and line.strip() and not line.startswith(" "):
            break                               # the list is over; before it, the lead-in carries on
        elif SECTION.match(line):
            break
    return out


def _same(a: str, b: str) -> bool:
    """Two titles naming the same item: one starts the other, give or take case and punctuation."""
    a, b = (re.sub(r"[^a-z0-9]+", " ", x.lower()).strip() for x in (a, b))
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def worth(item: Item, text: str, named: Optional[List[str]] = None,
          release: Optional[range] = None) -> Tuple[Tuple[int, int, int], str]:
    """How much `item` is worth for the next release — a sort key, smallest
    first — and why, in a few words (see the module's docstring)."""
    named = left_for_release(text) if named is None else named
    section = item.section.split(" — ")[0]
    stage = stage_of(item.section)
    at = next((n for n, t in enumerate(named) if _same(t, item.title)), None)
    if at is not None:
        return (0, at, item.order), f"named #{at + 1} in what's left for the release"
    lower = item.section.lower()
    builder = lower.startswith(BUILDER)
    score = SCORE.search(item.text)
    if score and not builder:
        return (1, 0, item.order), f"it moves the score ({score.group(1).lower()}) — {section}"
    if stage is not None:
        if release is None or stage in release:
            span = f"Stages {release[0]}–{release[-1]}" if release is not None and len(release) > 1 else section
            return (2, 0, item.order), (f"{section} is part of the next release ({span})"
                                        if release is not None else f"{section}, in the file's order")
        return (5, 0, item.order), f"{section}, after the release — nothing worth more is left"
    if lower.startswith("where it stands"):
        return (2, 0, item.order), "open now, for the release (Where it stands)"
    if builder:
        return (4, 0, item.order), "the self-build machinery — worth it only once the rest moves"
    return (3, 0, item.order), f"not tied to a stage ({section}) — after the release's own items"


def ranked(text: str, items: Optional[List[Item]] = None) -> List[Tuple[Item, str]]:
    """The items eki may take (`workable`), most worth first, each with why."""
    items = parse(text) if items is None else items
    named, release = left_for_release(text), release_stages(text)
    got = [(i, *worth(i, text, named, release)) for i in workable(items)]
    return [(i, why) for i, _, why in sorted(got, key=lambda g: g[1])]


def find(text: str, key: str) -> Optional[Item]:
    return next((i for i in parse(text) if i.key == key), None)


def intro(text: str, section: str) -> str:
    """What a section says before its first item — the stage's why."""
    out: List[str] = []
    inside = False
    for line in text.splitlines():
        head = SECTION.match(line)
        if head:
            if inside:
                break
            inside = head.group(1) == section
            continue
        if not inside:
            continue
        if BOX.match(line) or line.startswith("- "):
            break
        out.append(line.strip())
    return " ".join(p for p in out if p).strip()


def mark(cid: str) -> str:
    """What a tick says after the item: the change that finished it."""
    return f"*(eki: self/{cid})*"


def tick(text: str, key: str, note: str) -> str:
    """The item ticked, `note` at its end — or the text as it was if the item
    is gone or already ticked."""
    item = find(text, key)
    if item is None or item.done:
        return text
    lines = text.splitlines(keepends=True)
    lines[item.start] = lines[item.start].replace("- [ ]", "- [x]", 1)
    last = lines[item.end]
    body = last.rstrip("\r\n")
    lines[item.end] = f"{body} {note}{last[len(body):]}" if note else last
    return "".join(lines)


def untick(text: str, key: str, note: str = "") -> str:
    """The item open again, `note` (or any eki mark) gone from its end — or
    the text as it was if the item is gone or already open."""
    item = find(text, key)
    if item is None or not item.done:
        return text
    lines = text.splitlines(keepends=True)
    lines[item.start] = re.sub(r"^- \[[xX]\]", "- [ ]", lines[item.start], count=1)
    last = lines[item.end]
    body = last.rstrip("\r\n")
    trimmed = body.replace(f" {note}", "") if note else MARK.sub("", body)
    lines[item.end] = trimmed + last[len(body):]
    return "".join(lines)


#: what a tick says after the item, whatever change it names
MARK = re.compile(r"\s*\*\(eki: self/[0-9a-f]+\)\*")


def without_new_ticks(before: str, after: str) -> str:
    """`after`, with every item that was open in `before` and is ticked in
    `after` as it was in `before` — the ticks a change made (with whatever
    note it put after them) taken out, the rest of its edits to the file
    kept."""
    was = {i.key: i for i in parse(before) if not i.done}
    old = before.splitlines(keepends=True)
    lines = after.splitlines(keepends=True)
    for item in sorted(parse(after), key=lambda i: -i.start):
        if item.done and item.key in was:
            open_ = was[item.key]
            lines[item.start:item.end + 1] = old[open_.start:open_.end + 1]
    return "".join(lines)


def keep_ticks(before: str, after: str) -> str:
    """`after`, with every item ticked in `before` and open in `after` ticked
    again, with the eki mark it had in `before` — what a change carrying an
    old copy of a ticked line (a docs edit made from an older checkout, a
    conflict resolved to the older side) would otherwise take back. The
    rest of the change's edits to those lines are kept."""
    for item in parse(before):
        if not item.done:
            continue
        now = find(after, item.key)
        if now is not None and not now.done:
            found = MARK.search(item.text)
            after = tick(after, item.key, found.group(0).strip() if found else "")
    return after


def tick_file(root: Path, key: str, note: str) -> bool:
    """Tick the item in `root`/ROADMAP.md. True if the file changed."""
    path = Path(root) / NAME
    try:
        text = path.read_text()
    except OSError:
        return False
    after = tick(text, key, note)
    if after == text:
        return False
    path.write_text(after)
    return True


def read(root: Path) -> str:
    try:
        return (Path(root) / NAME).read_text()
    except OSError:
        return ""


def add(text: str, section: str, line: str) -> str:
    """A new open item at the end of `section`'s items (or of the section), or
    before *Not planned* in an *Inbox* section if there's no such section."""
    lines = text.splitlines(keepends=True)
    entry = f"- [ ] {line.strip()}\n"
    start = next((i for i, l in enumerate(lines)
                  if (m := SECTION.match(l)) and m.group(1) == section), None)
    if start is None:
        inbox = next((i for i, l in enumerate(lines)
                      if (m := SECTION.match(l)) and m.group(1).lower() == "inbox"), None)
        if inbox is None:
            before = next((i for i, l in enumerate(lines)
                           if (m := SECTION.match(l)) and m.group(1).lower().startswith("not planned")),
                          len(lines))
            block = ["## Inbox\n", "\n",
                     "Picked from eki's weekly notes, not placed in a stage yet.\n", "\n", entry, "\n"]
            if before and lines[before - 1].strip():
                block.insert(0, "\n")
            return "".join(lines[:before] + block + lines[before:])
        start = inbox
    end = next((i for i in range(start + 1, len(lines)) if SECTION.match(lines[i])), len(lines))
    last_item = max((it.end for it in parse(text) if start < it.start < end), default=None)
    at = (last_item + 1) if last_item is not None else end
    if last_item is None:
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1
        return "".join(lines[:at] + ["\n", entry] + lines[at:])
    return "".join(lines[:at] + [entry] + lines[at:])


def counts(items: Iterable[Item]) -> Dict[str, int]:
    items = list(items)
    return {"done": sum(i.done for i in items), "open": sum(not i.done for i in items),
            "person": sum(i.person and not i.done for i in items)}
