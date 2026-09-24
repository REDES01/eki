# SPDX-License-Identifier: Apache-2.0
"""ROADMAP.md, read and kept by eki (the self-build track).

The roadmap is the one plan, written for people: sections (`## Stage 3 —
Work while you're away`) and, under them, items —

    - [ ] **Goals are sentences.** What you'd type in a chat, left running…
          indented lines carry the item on

eki reads it to find the next thing to work on in itself, in the order the
file gives, and ticks an item once the change that finished it has landed,
naming that change — one small commit of its own, written by the merge queue
alone (eki/selfengine.py, `_self_ticks`), and taken back if the change is
undone. A change never carries a tick in its own commit (a tick an agent
made anyway is dropped: `without_new_ticks`), so a tick is never what makes
two changes conflict.

Never taken: an item that says `(for a person)` anywhere in it, and
anything under *Not planned* or *Keeping this file*. Not taken yet: an item
that says `(waiting on …)` — it needs something else to land first.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

NAME = "ROADMAP.md"
BOX = re.compile(r"^- \[( |x|X)\] (.*)$")
SECTION = re.compile(r"^##\s+(.*?)\s*$")
PERSON = re.compile(r"\(for a person\b[^)]*\)", re.I)
WAITING = re.compile(r"\(waiting on\b[^)]*\)", re.I)
#: sections whose bullets are never work
SKIP = ("not planned", "keeping this file")


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
