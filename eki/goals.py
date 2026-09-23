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
    bible = raw.get("bible") or []
    if isinstance(bible, str):
        bible = [bible]
    goals = []
    for g in raw.get("goals") or []:
        name = str(g.get("name") or "").strip()
        if not name or not re.fullmatch(r"[\w.-]+", name):
            raise GoalError(f"a goal needs a plain name, not {name!r}")
        if g.get("items"):
            items = [str(i) for i in g["items"]]
        elif g.get("count"):
            fmt = str(g.get("id") or name + "-{n:02}")
            items = [fmt.format(n=i + 1) for i in range(int(g["count"]))]
        else:
            raise GoalError(f"{name}: say how many (count) or which (items)")
        parts: Dict[str, Part] = {}
        for pname, p in (g.get("parts") or {}).items():
            kind = str(p.get("kind") or "text")
            if kind not in KINDS:
                raise GoalError(f"{name}.{pname}: kind is one of {', '.join(KINDS)}")
            if not p.get("file") or not p.get("prompt"):
                raise GoalError(f"{name}.{pname}: needs a file and a prompt")
            after = p.get("from") or []
            parts[str(pname)] = Part(str(pname), kind, str(p["file"]), str(p["prompt"]),
                                     [str(a) for a in ([after] if isinstance(after, str) else after)],
                                     str(p.get("line") or "local"), str(p.get("backend") or ""))
        if not parts:
            raise GoalError(f"{name}: no parts")
        goals.append(Goal(name, items, "{item}", str(g.get("dir") or name + "/{id}"),
                          _order(parts, name)))
    return Spec(str(root), [str(b) for b in bible], goals)


def _item_dir(spec: Spec, goal: Goal, item: str, n: int) -> Path:
    return Path(spec.folder) / goal.dir.format(id=item, item=item, n=n)


def pieces(spec: Spec) -> List[Piece]:
    """Every piece that's still missing, item by item, in dependency order."""
    out = []
    for goal in spec.goals:
        for n, item in enumerate(goal.items, 1):
            where = _item_dir(spec, goal, item, n)
            for part in goal.parts.values():
                path = where / part.file
                if path.exists():
                    continue
                ready = all((where / goal.parts[d].file).exists() for d in part.after)
                out.append(Piece(spec.folder, goal.name, item, n, part.name, part.kind, str(path),
                                 part.after, part.line, part.backend, ready))
    return out


def status(spec: Spec) -> List[Dict[str, Any]]:
    rows = []
    for goal in spec.goals:
        total = len(goal.items) * len(goal.parts)
        missing = [p for p in pieces(Spec(spec.folder, spec.bible, [goal]))]
        rows.append({"goal": goal.name, "items": len(goal.items), "parts": list(goal.parts),
                     "done": total - len(missing), "total": total,
                     "ready": sum(p.ready for p in missing)})
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

    return _FIELD.sub(fill, part.prompt).strip()


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
            out += pieces(load(folder))
        except GoalError:
            continue
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
