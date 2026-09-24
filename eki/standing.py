# SPDX-License-Identifier: Apache-2.0
"""One standing context, seen by both CLIs (ROADMAP, Stage 1).

Claude Code reads ~/.claude/CLAUDE.md and Codex reads ~/.codex/AGENTS.md:
two files saying the same thing drift the day one is edited. `AGENTS.md` is
the standard, so it is the one eki keeps:

- ~/.eki/context/AGENTS.md — canonical: yours, plus a section eki keeps
  current between markers ("how to use eki here").
- ~/.eki/context/CLAUDE.md — `@AGENTS.md`, then only what is truly
  Claude's.

Each CLI's file is a link into that folder: ~/.codex/AGENTS.md, and
~/.claude/CLAUDE.md beside a ~/.claude/AGENTS.md link, so Claude's
`@AGENTS.md` import resolves whether it reads through the link or not.
Nothing is authored inside ~/.claude or ~/.codex.

A file there eki didn't make is left alone and reported until you import
it (`eki context import`): its text is taken into the source — Codex's into
AGENTS.md, Claude's below the import in CLAUDE.md, since only you can say
which of it is Claude-only — the original is kept under
~/.eki/context/imported/, and the link takes its place.

A project follows the same rule (`eki context project DIR`): its AGENTS.md
is canonical and its CLAUDE.md starts with `@AGENTS.md`. `eki context use
DIR` goes one step further (ROADMAP, Stage 4): the project's AGENTS.md gets
eki's section too, and Claude Code there may run `eki` without asking.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

HOME = Path("~/.eki/context").expanduser()
IMPORTED = HOME / "imported"
CLAUDE_HOME = Path("~/.claude").expanduser()
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()

IMPORT = "@AGENTS.md"
#: the rule that lets Claude Code run `eki …` in a project without a prompt
ALLOW = "Bash(eki:*)"
BEGIN = "<!-- eki: how to use eki here (kept current by eki; edit above or below) -->"
END = "<!-- eki: end -->"


def views() -> List[Tuple[str, Path, str]]:
    """(backend, the file that CLI reads, the source file it links to)."""
    return [("claude", CLAUDE_HOME / "CLAUDE.md", "CLAUDE.md"),
            ("claude", CLAUDE_HOME / "AGENTS.md", "AGENTS.md"),
            ("codex", CODEX_HOME / "AGENTS.md", "AGENTS.md")]


# ---- the source ------------------------------------------------------------------

def eki_section() -> str:
    """What every agent on this Mac should know about eki. Short on purpose:
    it is in every session's context, and the `eki` skill has the rest."""
    return (f"{BEGIN}\n"
            "## eki\n\n"
            "eki is the model hub on this Mac. When a task wants another model — a local\n"
            "LLM, Claude Code, Codex, an API, or a picture — or work that should keep\n"
            "running on its own, hand it to eki: `eki ask \"…\"` from a shell, or the\n"
            "`eki_ask` / `eki_image` tools. The `eki` skill says how.\n"
            f"{END}\n")


def _with_section(text: str) -> str:
    """`text` with eki's section current: replaced where the markers are,
    appended when they aren't. Everything outside them is kept as is."""
    section = eki_section()
    if BEGIN in text and END in text:
        head, _, rest = text.partition(BEGIN)
        _, _, tail = rest.partition(END)
        return head + section + tail.lstrip("\n")
    return text + ("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "") + section


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _write(path: Path, text: str) -> bool:
    if _read(path) == text and path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return True


def ensure_source() -> List[str]:
    """The source exists, AGENTS.md carries eki's current section and
    CLAUDE.md starts with the import. Returns the files it changed."""
    changed = []
    agents = HOME / "AGENTS.md"
    if _write(agents, _with_section(_read(agents))):
        changed.append(str(agents))
    claude = HOME / "CLAUDE.md"
    text = _read(claude)
    if not imports_agents(text):
        text = IMPORT + "\n" + (("\n" + text.lstrip("\n")) if text.strip() else "")
    if _write(claude, text):
        changed.append(str(claude))
    return changed


def imports_agents(text: str) -> bool:
    return any(line.strip() == IMPORT for line in text.splitlines())


# ---- the views -------------------------------------------------------------------

def _points_into_source(link: Path) -> bool:
    try:
        target = Path(os.readlink(link))
    except OSError:
        return False
    if not target.is_absolute():
        target = link.parent / target
    try:
        return target.resolve().parent == HOME.resolve()
    except OSError:
        return False


def state() -> List[Dict[str, Any]]:
    """For each file a CLI reads: linked | missing | conflict (a file eki
    didn't make holds the name)."""
    out = []
    for backend, path, name in views():
        if path.is_symlink():
            s = "linked" if _points_into_source(path) else "conflict"
        elif path.exists():
            s = "conflict"
        else:
            s = "missing"
        out.append({"backend": backend, "path": str(path), "source": str(HOME / name), "state": s})
    return out


def sync() -> Dict[str, Any]:
    """Link each CLI's file into the source. Only links are made; a file
    eki didn't make is reported, never replaced."""
    report: Dict[str, Any] = {"changed": ensure_source(), "linked": [], "conflicts": []}
    for v in state():
        path = Path(v["path"])
        if v["state"] == "conflict":
            report["conflicts"].append(v["path"])
        elif v["state"] == "missing":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(v["source"])
            report["linked"].append(v["path"])
    return report


def import_existing() -> Dict[str, Any]:
    """Take the CLIs' own files into the source once, then link them back.
    Codex's AGENTS.md becomes (or is added to) the canonical one; Claude's
    CLAUDE.md goes below the import in the Claude-only file. A link someone
    else made is theirs: left where it is, and still reported. Text the
    source already has (the same file copied to both CLIs) isn't added twice."""
    ensure_source()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    taken = []
    # the shared text first, so what Claude's file repeats of it is seen as such
    for v in sorted(state(), key=lambda v: not v["source"].endswith("AGENTS.md")):
        path = Path(v["path"])
        if v["state"] != "conflict":
            continue
        if path.is_symlink():
            continue                                # not ours to replace
        text = _read(path).strip()
        source = Path(v["source"])
        if text and text not in _read(HOME / "AGENTS.md") and text not in _read(source):
            current = _read(source)
            if source.name == "AGENTS.md":
                head, sep, rest = current.partition(BEGIN)
                current = (head.rstrip("\n") + "\n\n" + text + "\n\n" + sep + rest
                           if head.strip() else text + "\n\n" + sep + rest)
            else:
                current = current.rstrip("\n") + "\n\n" + text + "\n"
            _write(source, current)
        IMPORTED.mkdir(parents=True, exist_ok=True)
        kept = IMPORTED / f"{v['backend']}-{path.name}-{stamp}"
        path.replace(kept)
        taken.append({"from": str(path), "kept": str(kept)})
    report = sync()
    return {"imported": taken, **report}


def boot() -> Dict[str, Any]:
    """At engine start: the source is current and the views that are free are linked."""
    try:
        return sync()
    except OSError as e:
        return {"error": str(e)}


# ---- a project ---------------------------------------------------------------------

def project(folder: str) -> Dict[str, Any]:
    """Make a project's AGENTS.md canonical and its CLAUDE.md an import of
    it. A CLAUDE.md with no AGENTS.md beside it becomes the AGENTS.md; one
    that has both keeps its text below the import, as Claude-only."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValueError(f"no such folder: {root}")
    agents, claude = root / "AGENTS.md", root / "CLAUDE.md"
    if claude.is_symlink() or agents.is_symlink():
        return {"folder": str(root), "done": [], "note": "a link is there; left alone"}
    a, c = _read(agents), _read(claude)
    done = []
    if not agents.exists() and c.strip() and not imports_agents(c):
        _write(agents, c)
        done.append("moved CLAUDE.md to AGENTS.md")
        c = ""
    if not agents.exists() and not a:
        return {"folder": str(root), "done": done, "note": "no AGENTS.md or CLAUDE.md here"}
    if not imports_agents(c):
        _write(claude, IMPORT + "\n" + (("\n" + c.lstrip("\n")) if c.strip() else ""))
        done.append("CLAUDE.md imports AGENTS.md")
    return {"folder": str(root), "done": done}



def _allow_eki(root: Path) -> bool:
    """`eki` on the allow list of the project's .claude/settings.local.json —
    the personal file, not the shared one: eki is on this Mac, not on every
    teammate's. Other settings and rules there are kept as they are."""
    path = root / ".claude" / "settings.local.json"
    text = _read(path)
    try:
        settings = json.loads(text) if text.strip() else {}
    except ValueError:
        raise ValueError(f"{path} isn't JSON; left alone")
    if not isinstance(settings, dict):
        raise ValueError(f"{path} isn't a settings object; left alone")
    perms = settings.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if ALLOW in allow:
        return False
    allow.append(ALLOW)
    return _write(path, json.dumps(settings, indent=2) + "\n")


def use_here(folder: str) -> Dict[str, Any]:
    """Tell the agents working in a project how to reach eki: the project
    gets the AGENTS.md / CLAUDE.md shape, eki's section in its AGENTS.md
    (between the markers, so a second run only refreshes it), and `eki` on
    Claude Code's allow list there. Only when asked, like `project`."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValueError(f"no such folder: {root}")
    agents = root / "AGENTS.md"
    if agents.is_symlink() or (root / "CLAUDE.md").is_symlink():
        return {"folder": str(root), "done": [], "note": "a link is there; left alone"}
    done = list(project(str(root))["done"])
    if _write(agents, _with_section(_read(agents))):
        done.append("eki's section in AGENTS.md")
    if not imports_agents(_read(root / "CLAUDE.md")):
        done += project(str(root))["done"]
    if _allow_eki(root):
        done.append(f"{ALLOW} allowed in .claude/settings.local.json")
    return {"folder": str(root), "done": done}
