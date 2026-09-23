# SPDX-License-Identifier: Apache-2.0
"""Creating, changing and removing goals — without anyone hand-editing YAML.

goals.yaml stays the one place a goal lives (it's in the project, readable and
versioned with it). What changes is who writes it: the board's form, `eki
goals new|edit|rm`, or a model drafting an entry from a sentence.

An edit touches only the entry it's about. Everything else in the file — your
comments, the bible, the other goals, their formatting — stays exactly as it
was; the changed entry is written afresh. Every write is checked by loading
the file back, and undone if it doesn't load.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from . import goals as goals_mod
from .goals import FILE, GoalError

#: the fields of an entry, in the order they're written
ORDER = ("name", "count", "items", "id", "dir", "parts")
PART_ORDER = ("kind", "file", "from", "line", "backend", "prompt")


# ---- writing one entry ------------------------------------------------------------------

class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, text: str) -> Any:
    style = "|" if "\n" in text else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", text, style=style)


_Dumper.add_representer(str, _str)


def clean(entry: Dict[str, Any]) -> Dict[str, Any]:
    """An entry in its written shape: known fields in order, empty ones out."""
    out: Dict[str, Any] = {}
    for key in ORDER:
        value = entry.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "parts":
            parts = {}
            for pname, p in value.items():
                part = {}
                for k in PART_ORDER:
                    v = (p or {}).get(k)
                    if v in (None, "", []) or (k == "line" and v == "local"):
                        continue
                    if k == "prompt":
                        v = str(v).strip("\n") + ("\n" if "\n" in str(v).strip("\n") else "")
                    part[k] = v
                parts[str(pname)] = part
            value = parts
        if key == "count":
            value = int(value)
        out[key] = value
    if "items" in out:
        out.pop("count", None)
        out.pop("id", None)
    return out


def dump(entry: Dict[str, Any]) -> str:
    """One entry as YAML, as a list item (`- name: …`)."""
    return yaml.dump([clean(entry)], Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     width=100, default_flow_style=None)


def _read(folder: str) -> str:
    try:
        return (Path(folder) / FILE).read_text()
    except FileNotFoundError:
        return ""


def raw_entries(folder: str) -> List[Dict[str, Any]]:
    try:
        raw = yaml.safe_load(_read(folder)) or {}
    except yaml.YAMLError as e:
        raise GoalError(f"{FILE}: {e}")
    return list(raw.get("goals") or [])


def entry(folder: str, name: str) -> Dict[str, Any]:
    found = next((g for g in raw_entries(folder) if str(g.get("name")) == name), None)
    if found is None:
        raise GoalError(f"no goal {name!r} in {folder}")
    return found


def _blocks(text: str) -> Tuple[List[str], int, List[Tuple[int, int, str]], int]:
    """(lines, where `goals:` is, [(start, end, name)] of each entry, the
    entries' indent). end is exclusive and leaves trailing blank lines out."""
    lines = text.splitlines(keepends=True)
    top = next((i for i, ln in enumerate(lines) if re.match(r"goals\s*:", ln)), -1)
    if top < 0:
        return lines, -1, [], 2
    indent = None
    starts: List[int] = []
    end_of_list = len(lines)
    for i in range(top + 1, len(lines)):
        ln = lines[i]
        if ln.strip() == "" or ln.lstrip().startswith("#"):
            continue
        if not ln.startswith((" ", "-", "\t")):            # the next top-level key
            end_of_list = i
            break
        m = re.match(r"( *)- ", ln)
        if m and (indent is None or len(m.group(1)) == indent):
            indent = len(m.group(1)) if indent is None else indent
            starts.append(i)
    blocks = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else end_of_list
        while end > start + 1 and (lines[end - 1].strip() == "" or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        try:
            body = yaml.safe_load("".join(lines[start:end]))
            name = str((body or [{}])[0].get("name") or "")
        except (yaml.YAMLError, AttributeError, IndexError, TypeError):
            name = ""
        blocks.append((start, end, name))
    return lines, top, blocks, indent if indent is not None else 2


def _indented(text: str, indent: int) -> List[str]:
    pad = " " * indent
    return [(pad + ln if ln.strip() else ln) for ln in text.splitlines(keepends=True)]


def _write_checked(folder: str, text: str, before: str) -> goals_mod.Spec:
    path = Path(folder) / FILE
    tmp = path.with_suffix(".yaml.eki-tmp")
    tmp.write_text(text)
    tmp.replace(path)
    try:
        return goals_mod.load(folder)
    except GoalError:
        if before:
            path.write_text(before)
        else:
            path.unlink(missing_ok=True)
        raise


def put(folder: str, new: Dict[str, Any], old_name: str = "") -> goals_mod.Spec:
    """Add a goal, or replace `old_name` with it (a rename, if the names differ)."""
    folder = str(Path(folder).expanduser())
    goals_mod.goal_from_raw(clean(new))                      # checked before anything is written
    name = str(new.get("name"))
    before = _read(folder)
    lines, top, blocks, indent = _blocks(before)
    taken = {b[2] for b in blocks}
    if name != old_name and name in taken:
        raise GoalError(f"there's already a goal called {name!r}")
    fresh = _indented(dump(new), indent)
    target = next((b for b in blocks if b[2] == old_name), None) if old_name else None
    if old_name and target is None:
        raise GoalError(f"no goal {old_name!r} to change")
    if target is not None:
        start, end, _ = target
        lines[start:end] = fresh
    elif top < 0:
        if before and not before.endswith("\n"):
            lines.append("\n")
        lines += (["\n"] if before.strip() else []) + ["goals:\n"] + fresh
    else:
        where = blocks[-1][1] if blocks else top + 1
        if re.match(r"goals\s*:\s*\[\s*\]", lines[top]):
            lines[top] = "goals:\n"
        gap = ["\n"] if blocks else []
        lines[where:where] = gap + fresh
    Path(folder).mkdir(parents=True, exist_ok=True)
    return _write_checked(folder, "".join(lines), before)


def remove(folder: str, name: str, trash: bool = False) -> Dict[str, Any]:
    """Take a goal out of goals.yaml. Its files stay where they are, unless
    `trash` — then they go to the project's .eki/trash first."""
    folder = str(Path(folder).expanduser())
    before = _read(folder)
    lines, top, blocks, _ = _blocks(before)
    target = next((b for b in blocks if b[2] == name), None)
    if target is None:
        raise GoalError(f"no goal {name!r} in {folder}")
    moved = 0
    if trash:
        moved = goals_mod.delete(goals_mod.load(folder), name)["moved"]
    start, end, _ = target
    while end < len(lines) and lines[end].strip() == "":        # and the gap after it
        end += 1
    del lines[start:end]
    _write_checked(folder, "".join(lines), before)
    st = goals_mod.state(folder)                                # its pause and deletions go too
    st["paused"] = [g for g in st["paused"] if g != name]
    st["deleted"] = [k for k in st["deleted"] if not k.startswith(name + "/")]
    goals_mod._save_state(folder, st)
    return {"removed": name, "moved": moved}


# ---- what a change does to what's already made -----------------------------------------------

def impact(folder: str, old_name: str, new: Dict[str, Any]) -> Dict[str, Any]:
    """What saving `new` over `old_name` would mean for files already made."""
    goal = goals_mod.goal_from_raw(clean(new))
    out: Dict[str, Any] = {"changed": [], "orphaned": 0, "new_items": 0, "dropped_items": 0}
    if not old_name:
        return out
    spec = goals_mod.load(folder)
    old = next((g for g in spec.goals if g.name == old_name), None)
    if old is None:
        return out
    made = lambda g, part, item: (goals_mod._item_dir(spec, g, item, g.items.index(item) + 1)   # noqa: E731
                                  / g.parts[part].file).exists()
    out["new_items"] = len([i for i in goal.items if i not in old.items])
    out["dropped_items"] = len([i for i in old.items if i not in goal.items])
    for pname, part in goal.parts.items():
        was = old.parts.get(pname)
        if was is None:
            continue
        if (was.file != part.file or old.dir != goal.dir):
            out["orphaned"] += sum(made(old, pname, i) for i in old.items)
            continue
        if (was.prompt.strip(), was.kind, sorted(was.after)) != (part.prompt.strip(), part.kind, sorted(part.after)):
            n = sum(made(old, pname, i) for i in old.items if i in goal.items)
            if n:
                out["changed"].append({"part": pname, "made": n,
                                       "with": goals_mod.dependents(goal, pname)})
    return out


def redo_parts(folder: str, goal_name: str, parts: List[str]) -> int:
    """Every item's `parts` (and what was made from them) to the trash, to be made again."""
    spec = goals_mod.load(folder)
    goal = next(g for g in spec.goals if g.name == goal_name)
    names: List[str] = []
    for p in parts:
        for n in [p] + goals_mod.dependents(goal, p):
            if n not in names:
                names.append(n)
    paths = [goals_mod._item_dir(spec, goal, item, i) / goal.parts[n].file
             for i, item in enumerate(goal.items, 1) for n in names]
    return goals_mod._trash(spec, paths)


# ---- a goal from a sentence -------------------------------------------------------------------

DRAFT = """You write goal entries for eki, which makes a project's content on an idle machine.
A goal says what should exist: some items, each made of parts; each part is one file,
written by a text model or drawn by an image model from a prompt.

Reply with ONE goal entry as YAML, nothing else — no prose, no code fence. The shape:

name: npcs                     # plain name: letters, digits, - _ .
count: 20                      # how many items (or `items: [a, b, c]` for named ones)
id: "npc-{{n:02}}"             # how items are named; {{n}} is the number
dir: "npcs/{{id}}"               # where each item's files go, inside the project
parts:
  bio:
    kind: text                 # text or image
    file: bio.md
    prompt: |
      Invent one new character who lives in this world. First line: their name
      and role as a Markdown heading. Then 4-6 sentences about them.
      Make them clearly different from those already made: {{others}}
  portrait:
    kind: image
    file: portrait.png
    from: [bio]                # made after, and from, the bio
    prompt: "Painterly portrait, head and shoulders. {{bio:500}}"

In a prompt: {{other_part}} is that part of the same item ({{bio:500}} = its first 500
characters), {{others}} lists this part's first line from every other item (use it in
the first text part, so items don't repeat), {{n}} and {{id}} name the item. Every piece
also reads the project's world bible, so prompts needn't repeat it. Text parts: .md
files; image parts: .png. An image part needs `from:` a text part and its prompt should
carry a short style line plus that part. {kinds}

The project: {project}
{bible}
Write the goal for: {description}
"""


def draft_prompt(description: str, folder: str, kinds: List[str]) -> str:
    try:
        spec = goals_mod.load(folder)
        bible = goals_mod.bible(spec)[:2500]
        others = ", ".join(g.name for g in spec.goals)
    except GoalError:
        bible, others = "", ""
    can = ("This machine can make: " + " and ".join(kinds) + ".") if kinds else ""
    project = Path(folder).name + (f" (its goals already: {others})" if others else "")
    return DRAFT.format(kinds=can, project=project,
                        bible=f"Its world bible begins:\n{bible}\n" if bible else "",
                        description=description.strip())


def parse_draft(text: str) -> Dict[str, Any]:
    """The entry a model wrote, however it wrapped it."""
    if "</think>" in text:
        text = text.rpartition("</think>")[2]
    fence = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise GoalError(f"the draft isn't valid YAML: {e}")
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict) and "goals" in data and isinstance(data["goals"], list) and data["goals"]:
        data = data["goals"][0]
    if not isinstance(data, dict):
        raise GoalError("the draft isn't a goal entry")
    entry = clean(data)
    goals_mod.goal_from_raw(entry)
    return entry
