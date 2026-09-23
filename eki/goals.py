# SPDX-License-Identifier: Apache-2.0
"""What a project wants to exist, and what's still missing (ROADMAP, Stage 3).

eki doesn't guess what work is worth doing on an idle machine. A project says
what should exist — `goals.yaml` in its folder — and the backlog is the
difference between that and the files that are there, like `make`:

    bible: [world.md]                  # read by every piece
    goals:
      - name: npcs
        count: 20                      # or items: [innkeeper, smith, …]
        id: "npc-{n:02}"
        dir: "npcs/{id}"
        parts:
          bio:
            kind: text
            file: bio.md
            prompt: |
              Invent a new character for this world. Already made: {others}
          portrait:
            kind: image
            file: portrait.png
            from: [bio]
            prompt: "Painterly fantasy portrait. {bio:600}"
          lines:
            kind: text
            file: lines.md
            from: [bio]
            prompt: "Three lines this character says. {bio}"

A piece is one part of one item. It's missing while its file isn't there, and
ready once the parts it's made `from` exist. In a prompt, `{bio}` is that
item's bio (`{bio:600}` its first 600 characters), `{bible}` the bible,
`{others}` the first line of this part in every other item — so the twentieth
character isn't the first one again — and `{id}`, `{n}`, `{item}` name it.
`line: frontier` marks a part worth a subscription; the rest stays local.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

FILE = "goals.yaml"
PROJECTS = Path("~/.eki/goals/projects.json").expanduser()
LOG = Path("~/.eki/goals/log.jsonl").expanduser()
KINDS = ("text", "image")
#: how much of the bible and of {others} a prompt carries
BIBLE_CHARS = 6000
OTHERS_ITEMS = 60


class GoalError(ValueError):
    pass


@dataclass
class Part:
    name: str
    kind: str
    file: str
    prompt: str
    after: List[str] = field(default_factory=list)
    line: str = "local"                     # "local" | "frontier"
    backend: str = ""                       # a provider key, to override the choice


@dataclass
class Goal:
    name: str
    items: List[str]
    id: str
    dir: str
    parts: Dict[str, Part]


@dataclass
class Piece:
    folder: str
    goal: str
    item: str
    n: int
    part: str
    kind: str
    path: str                               # absolute
    after: List[str]
    line: str
    backend: str
    ready: bool

    @property
    def key(self) -> str:
        return f"{self.goal}/{self.item}/{self.part}"

    def to_json(self) -> Dict[str, Any]:
        return {"folder": self.folder, "goal": self.goal, "item": self.item, "n": self.n,
                "part": self.part, "kind": self.kind, "path": self.path, "line": self.line,
                "backend": self.backend, "ready": self.ready}


@dataclass
class Spec:
    folder: str
    bible: List[str]
    goals: List[Goal]


def _order(parts: Dict[str, Part], goal: str) -> Dict[str, Part]:
    """Parts in an order where each comes after what it's made from."""
    done: List[str] = []
    seen: set = set()

    def visit(name: str, trail: List[str]) -> None:
        if name in done:
            return
        if name in trail:
            raise GoalError(f"{goal}: parts go round in a circle: {' → '.join(trail + [name])}")
        if name not in parts:
            raise GoalError(f"{goal}: {trail[-1]} is made from {name!r}, which isn't a part")
        for dep in parts[name].after:
            visit(dep, trail + [name])
        if name not in seen:
            seen.add(name)
            done.append(name)

    for name in parts:
        visit(name, [])
    return {n: parts[n] for n in done}


def load(folder: str) -> Spec:
    root = Path(folder).expanduser()
    path = root / FILE
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        raise GoalError(f"no {FILE} in {root}")
    except yaml.YAMLError as e:
        raise GoalError(f"{path}: {e}")
    return from_raw(raw, root)


def goal_from_raw(g: Any) -> Goal:
    """One goal entry, checked — what goals.yaml says, or what the board sends."""
    if not isinstance(g, dict):
        raise GoalError("a goal is a set of fields (name, count, parts…)")
    name = str(g.get("name") or "").strip()
    if not name or not re.fullmatch(r"[\w.-]+", name):
        raise GoalError(f"a goal needs a plain name (letters, digits, - _ .), not {name!r}")
    if g.get("items"):
        items = [str(i).strip() for i in g["items"] if str(i).strip()]
        if len(set(items)) != len(items):
            raise GoalError(f"{name}: the same item is listed twice")
    elif g.get("count"):
        fmt = str(g.get("id") or name + "-{n:02}")
        try:
            count = int(g["count"])
            items = [fmt.format(n=i + 1) for i in range(count)]
        except (KeyError, ValueError, IndexError) as e:
            raise GoalError(f"{name}: can't number the items with {fmt!r} ({e})")
        if count < 1 or count > 10000:
            raise GoalError(f"{name}: count is between 1 and 10000")
        if len(set(items)) != len(items):
            raise GoalError(f"{name}: the id {fmt!r} gives every item the same name — use {{n}} in it")
    else:
        raise GoalError(f"{name}: say how many (count) or which (items)")
    parts: Dict[str, Part] = {}
    for pname, p in (g.get("parts") or {}).items():
        p = p or {}
        if not re.fullmatch(r"[\w.-]+", str(pname)):
            raise GoalError(f"{name}: a part needs a plain name, not {pname!r}")
        kind = str(p.get("kind") or "text")
        if kind not in KINDS:
            raise GoalError(f"{name}.{pname}: kind is one of {', '.join(KINDS)}")
        if not p.get("file") or not str(p.get("prompt") or "").strip():
            raise GoalError(f"{name}.{pname}: needs a file and a prompt")
        after = p.get("from") or []
        parts[str(pname)] = Part(str(pname), kind, str(p["file"]), str(p["prompt"]),
                                 [str(a) for a in ([after] if isinstance(after, str) else after)],
                                 str(p.get("line") or "local"), str(p.get("backend") or ""))
    if not parts:
        raise GoalError(f"{name}: no parts")
    # a part a prompt reads is made before it, whether or not `from` says so —
    # otherwise {bio} could be filled in before there is a bio
    for part in parts.values():
        for ref, _ in _FIELD.findall(part.prompt):
            if ref in parts and ref != part.name and ref not in part.after:
                part.after.append(ref)
    files = [p.file for p in parts.values()]
    if len(set(files)) != len(files):
        raise GoalError(f"{name}: two parts write the same file")
    return Goal(name, items, "{item}", str(g.get("dir") or name + "/{id}"), _order(parts, name))


def from_raw(raw: Dict[str, Any], root: Path) -> Spec:
    bible = raw.get("bible") or []
    if isinstance(bible, str):
        bible = [bible]
    goals = []
    for g in raw.get("goals") or []:
        goal = goal_from_raw(g)
        if any(x.name == goal.name for x in goals):
            raise GoalError(f"two goals are called {goal.name!r}")
        goals.append(goal)
    return Spec(str(root), [str(b) for b in bible], goals)


# ---- what you've paused or deleted (the project's .eki/state.json) ----------------------

def _state_path(folder: str) -> Path:
    return Path(folder) / ".eki" / "state.json"


def state(folder: str) -> Dict[str, List[str]]:
    """{"paused": [goal], "deleted": ["goal/item" or "goal/item/part"]}"""
    try:
        raw = json.loads(_state_path(folder).read_text())
    except (OSError, ValueError):
        raw = {}
    return {"paused": list(raw.get("paused") or []), "deleted": list(raw.get("deleted") or [])}


def _save_state(folder: str, data: Dict[str, List[str]]) -> None:
    path = _state_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _deleted(st: Dict[str, List[str]], goal: str, item: str, part: str = "") -> bool:
    gone = st["deleted"]
    return f"{goal}/{item}" in gone or bool(part and f"{goal}/{item}/{part}" in gone)


def _item_dir(spec: Spec, goal: Goal, item: str, n: int) -> Path:
    return Path(spec.folder) / goal.dir.format(id=item, item=item, n=n)


def pieces(spec: Spec) -> List[Piece]:
    """Every piece that's still missing, item by item, in dependency order —
    not ones you deleted (they stay deleted until you ask for them again)."""
    out = []
    st = state(spec.folder)
    for goal in spec.goals:
        for n, item in enumerate(goal.items, 1):
            where = _item_dir(spec, goal, item, n)
            for part in goal.parts.values():
                path = where / part.file
                if path.exists() or _deleted(st, goal.name, item, part.name):
                    continue
                ready = all((where / goal.parts[d].file).exists() for d in part.after)
                out.append(Piece(spec.folder, goal.name, item, n, part.name, part.kind, str(path),
                                 part.after, part.line, part.backend, ready))
    return out


def status(spec: Spec) -> List[Dict[str, Any]]:
    rows = []
    st = state(spec.folder)
    for goal in spec.goals:
        deleted = sum(_deleted(st, goal.name, i, p) for i in goal.items for p in goal.parts)
        total = len(goal.items) * len(goal.parts) - deleted
        missing = [p for p in pieces(Spec(spec.folder, spec.bible, [goal]))]
        kinds = sorted({p.kind for p in goal.parts.values()})
        rows.append({"goal": goal.name, "items": len(goal.items), "parts": list(goal.parts),
                     "kinds": kinds, "done": total - len(missing), "total": total,
                     "ready": sum(p.ready for p in missing), "deleted": deleted,
                     "paused": goal.name in st["paused"]})
    return rows


def _read(path: Path, limit: int = 0) -> str:
    try:
        text = path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return text[:limit] if limit else text


def bible(spec: Spec) -> str:
    parts = [_read(Path(spec.folder) / b) for b in spec.bible]
    text = "\n\n".join(p for p in parts if p)
    return text[:BIBLE_CHARS]


_FIELD = re.compile(r"\{(\w+)(?::(\d+))?\}")


def render(spec: Spec, piece: Piece) -> str:
    """The prompt for a piece, with what it's made from filled in."""
    goal = next(g for g in spec.goals if g.name == piece.goal)
    part = goal.parts[piece.part]
    where = _item_dir(spec, goal, piece.item, piece.n)

    def others() -> str:
        names = []
        for n, item in enumerate(goal.items, 1):
            if item == piece.item:
                continue
            first = _read(_item_dir(spec, goal, item, n) / part.file, 400).splitlines()
            line = next((ln.strip("# ").strip() for ln in first if ln.strip()), "")
            if line:
                names.append(line[:100])
        return "; ".join(names[:OTHERS_ITEMS]) or "none yet"

    def value(name: str) -> str:
        if name in goal.parts:
            return _read(where / goal.parts[name].file)
        if name == "bible":
            return bible(spec)
        if name == "others":
            return others()
        if name in ("id", "item"):
            return piece.item
        if name == "n":
            return str(piece.n)
        return "{" + name + "}"

    def fill(m: "re.Match[str]") -> str:
        text = value(m.group(1))
        return text[:int(m.group(2))] if m.group(2) else text

    text = _FIELD.sub(fill, part.prompt).strip()
    asked = notes(spec.folder).get(piece.key)
    if asked:
        text += f"\n\nThis time: {asked}"
    return text


# ---- reviewing (the board: eki/web/goals.html) -----------------------------------------

def _notes_path(folder: str) -> Path:
    return Path(folder) / ".eki" / "notes.json"


def notes(folder: str) -> Dict[str, str]:
    try:
        return dict(json.loads(_notes_path(folder).read_text()))
    except (OSError, ValueError):
        return {}


def _save_notes(folder: str, data: Dict[str, str]) -> None:
    path = _notes_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def forget_note(folder: str, key: str) -> None:
    have = notes(folder)
    if have.pop(key, None) is not None:
        _save_notes(folder, have)


def _title(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return ""


def items(spec: Spec, only: str = "") -> List[Dict[str, Any]]:
    """Every item with each part as it stands, for the board."""
    out = []
    pending = notes(spec.folder)
    st = state(spec.folder)
    for goal in spec.goals:
        if only and goal.name != only:
            continue
        rows = []
        for n, item in enumerate(goal.items, 1):
            where = _item_dir(spec, goal, item, n)
            parts = []
            title = ""
            for part in goal.parts.values():
                path = where / part.file
                entry: Dict[str, Any] = {"name": part.name, "kind": part.kind,
                                         "exists": path.exists(), "line": part.line,
                                         "after": part.after, "file": part.file,
                                         "deleted": _deleted(st, goal.name, item, part.name),
                                         "note": pending.get(f"{goal.name}/{item}/{part.name}", "")}
                if entry["exists"]:
                    entry["path"] = str(path.relative_to(spec.folder))
                    entry["mtime"] = int(path.stat().st_mtime)
                    if part.kind == "text":
                        entry["text"] = _read(path, 4000)
                        title = title or _title(entry["text"])
                else:
                    entry["ready"] = all((where / goal.parts[d].file).exists() for d in part.after)
                parts.append(entry)
            rows.append({"item": item, "n": n, "title": title or item, "parts": parts,
                         "deleted": _deleted(st, goal.name, item),
                         "dir": str(where.relative_to(spec.folder))})
        out.append({"goal": goal.name, "parts": list(goal.parts), "items": rows,
                    "paused": goal.name in st["paused"]})
    return out


def dependents(goal: Goal, part: str) -> List[str]:
    """The parts made from `part`, directly or through another."""
    out: List[str] = []
    changed = True
    while changed:
        changed = False
        for p in goal.parts.values():
            if p.name not in out and (part in p.after or any(a in out for a in p.after)):
                out.append(p.name)
                changed = True
    return out


def _goal(spec: Spec, name: str) -> Goal:
    goal = next((g for g in spec.goals if g.name == name), None)
    if goal is None:
        raise GoalError(f"no goal {name!r}")
    return goal


def _trash(spec: Spec, paths: List[Path]) -> int:
    """Into the project's .eki/trash/<when>/, keeping their places — never deleted."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved = 0
    for path in paths:
        if not path.exists():
            continue
        keep = Path(spec.folder) / ".eki" / "trash" / stamp / path.relative_to(spec.folder)
        keep.parent.mkdir(parents=True, exist_ok=True)
        path.replace(keep)
        moved += 1
    return moved


def _scope(goal: Goal, item: str, part: str) -> List[str]:
    if part and part not in goal.parts:
        raise GoalError(f"{goal.name} has no part {part!r}")
    if item and item not in goal.items:
        raise GoalError(f"{goal.name} has no item {item!r}")
    return [part] + dependents(goal, part) if part else list(goal.parts)


def redo(spec: Spec, goal_name: str, item: str, part: str, note: str = "") -> List[str]:
    """Make a piece again — and what was made from it, which would no longer
    match. The old files move to the project's .eki/trash/. `note` is added
    to the piece's prompt the next time it's made."""
    goal = _goal(spec, goal_name)
    if not item or not part:
        raise GoalError("say which item and part to redo")
    names = _scope(goal, item, part)
    where = _item_dir(spec, goal, item, goal.items.index(item) + 1)
    had = [n for n in names if (where / goal.parts[n].file).exists()]
    _trash(spec, [where / goal.parts[n].file for n in names])
    restore(spec, goal_name, item, part)               # wanted again, if it had been deleted
    if note.strip():
        have = notes(spec.folder)
        have[f"{goal_name}/{item}/{part}"] = note.strip()[:500]
        _save_notes(spec.folder, have)
    return [f"{goal_name}/{item}/{n}" for n in had]


def delete(spec: Spec, goal_name: str, item: str = "", part: str = "") -> Dict[str, Any]:
    """Remove what was made, and keep it removed.

    A part (and what was made from it) or a whole item: its files go to the
    trash and it isn't made again until you restore it. The whole goal: every
    file it made goes to the trash and the goal is paused, so nothing is
    remade until you resume it."""
    goal = _goal(spec, goal_name)
    st = state(spec.folder)
    if not item:
        paths = [_item_dir(spec, goal, i, n) / p.file
                 for n, i in enumerate(goal.items, 1) for p in goal.parts.values()]
        moved = _trash(spec, paths)
        if goal.name not in st["paused"]:
            st["paused"].append(goal.name)
        _save_state(spec.folder, st)
        return {"moved": moved, "paused": True}
    names = _scope(goal, item, part)
    where = _item_dir(spec, goal, item, goal.items.index(item) + 1)
    moved = _trash(spec, [where / goal.parts[n].file for n in names])
    keys = [f"{goal.name}/{item}"] if not part else [f"{goal.name}/{item}/{n}" for n in names]
    st["deleted"] += [k for k in keys if k not in st["deleted"]]
    _save_state(spec.folder, st)
    return {"moved": moved, "deleted": keys}


def restore(spec: Spec, goal_name: str, item: str, part: str = "") -> List[str]:
    """Want a deleted item or part again: it's made on the next idle turn."""
    goal = _goal(spec, goal_name)
    names = _scope(goal, item, part)
    st = state(spec.folder)
    drop = {f"{goal.name}/{item}/{n}" for n in names}
    if not part:
        drop.add(f"{goal.name}/{item}")
    back = [k for k in st["deleted"] if k in drop]
    if back:
        st["deleted"] = [k for k in st["deleted"] if k not in drop]
        _save_state(spec.folder, st)
    return back


def pause(folder: str, goal_name: str, paused: bool) -> bool:
    spec = load(folder)
    _goal(spec, goal_name)
    st = state(spec.folder)
    if paused and goal_name not in st["paused"]:
        st["paused"].append(goal_name)
    if not paused:
        st["paused"] = [g for g in st["paused"] if g != goal_name]
    _save_state(spec.folder, st)
    return paused


def inside(folder: str, relative: str) -> Optional[Path]:
    """A file in a registered project, or None — nothing outside it is served."""
    root = Path(folder).expanduser().resolve()
    if str(root) not in [str(Path(f).resolve()) for f in projects()]:
        return None
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def instructions(spec: Spec, piece: Piece) -> str:
    """The system prompt a text piece is written under."""
    world = bible(spec)
    head = ("You are writing one piece of a larger project. Write only the piece itself — "
            "no preamble, no notes about what you did, no questions. Stay consistent with "
            "the project's world.")
    return head + (f"\n\nThe world:\n\n{world}" if world else "")


# ---- which projects -------------------------------------------------------------------

def projects() -> List[str]:
    try:
        return list(json.loads(PROJECTS.read_text()).get("projects") or [])
    except (OSError, ValueError):
        return []


def _save(folders: List[str]) -> None:
    PROJECTS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROJECTS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"projects": folders}, indent=2))
    tmp.replace(PROJECTS)


def add(folder: str) -> Spec:
    spec = load(folder)                     # refuses a folder without a readable goals.yaml
    have = projects()
    if spec.folder not in have:
        _save(have + [spec.folder])
    return spec


def remove(folder: str) -> bool:
    root = str(Path(folder).expanduser())
    have = projects()
    if root not in have:
        return False
    _save([f for f in have if f != root])
    return True


def backlog() -> List[Piece]:
    """What's missing across every project, ready ones first, in order."""
    out: List[Piece] = []
    for folder in projects():
        try:
            spec = load(folder)
        except GoalError:
            continue
        paused = set(state(spec.folder)["paused"])
        out += [p for p in pieces(spec) if p.goal not in paused]
    return out


def note(entry: Dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def history(since: float) -> List[Dict[str, Any]]:
    try:
        lines = LOG.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if float(e.get("at") or 0) >= since:
            out.append(e)
    return out


def clean(text: str) -> str:
    """A text piece as it goes to disk: a model's thinking left out."""
    if "</think>" in text:
        text = text.rpartition("</think>")[2]
    return text.strip() + "\n"
