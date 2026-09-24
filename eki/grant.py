# SPDX-License-Identifier: Apache-2.0
"""What a run may do, decided in eki and rendered for each harness.

A run the person started is the top of the tree and may do anything. A run
an agent starts through eki (`eki ask` from its shell, `eki_ask` and
`eki_image` from its tools) gets what its parent hands it, never more
(ROADMAP, Stage 2). The grant is written into the run before it starts,
then rendered into the program's own words: Claude Code's permission mode
and tool lists, Codex's sandbox — which is also the harness the local
models borrow through the gateway — and Gemini CLI's approval mode.

A narrowed run runs headless (`claude -p`, `codex exec`): nobody is
watching it, so what it isn't allowed is denied, never asked. What was
denied is read back (`refusal`), said in the thread, and kept as the grant
that would have allowed it (`widened`) — *allow and rerun*.

Three levels, each inside the one before:

- ``full``  — anything: any file, the network, any command.
- ``write`` — edit its working copy (a thread's worktree) and the paths
  named, and run only the commands listed.
- ``read``  — read and answer; edit nothing; run only the commands listed.

The grant crosses into a child's shell as ``EKI_GRANT``, so an `eki ask`
from inside a narrowed run is narrowed by it again.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

LEVELS = ("read", "write", "full")
ENV = "EKI_GRANT"
#: where pictures land (eki/adapters/comfyui.py) — all `eki image` may write
IMAGES = os.path.expanduser("~/.eki/images")
#: Claude Code's tools that only look
READ_TOOLS = ("Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "TodoWrite")
#: and the ones that change files
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


@dataclass(frozen=True)
class Grant:
    level: str = "full"
    #: where it may write besides its working copy (absolute paths)
    paths: Tuple[str, ...] = ()
    #: command prefixes it may run; None = any (only at ``full``)
    commands: Optional[Tuple[str, ...]] = None

    @property
    def narrowed(self) -> bool:
        return self.level != "full"

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"level": self.level}
        if self.paths:
            out["paths"] = list(self.paths)
        if self.commands is not None:
            out["commands"] = list(self.commands)
        return out

    def describe(self) -> str:
        """One line for the thread: what the run was handed."""
        if not self.narrowed:
            return "full access"
        said = "read-only" if self.level == "read" else "may edit its copy"
        if self.paths:
            said += " and " + ", ".join(_home(p) for p in self.paths)
        said += "; commands: " + (", ".join(self.commands) if self.commands else "none")
        return said


FULL = Grant()


def load(data: Any) -> Grant:
    """A grant from its JSON; anything unreadable is read-only, not full —
    a child that can't say what it was given gets the least."""
    if data is None or data == {}:
        return FULL
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return Grant("read", (), ())
    if not isinstance(data, dict):
        return Grant("read", (), ())
    level = str(data.get("level") or "full")
    if level not in LEVELS:
        return Grant("read", (), ())
    paths = tuple(_abs(p) for p in data.get("paths") or () if str(p).strip())
    cmds = data.get("commands")
    commands = tuple(str(c).strip() for c in cmds if str(c).strip()) if cmds is not None else None
    if level != "full" and commands is None:
        commands = ()
    return Grant(level, paths, commands)


def from_env() -> Grant:
    """The grant of the run this process is inside of; the top of the tree
    when there is none."""
    return load(os.environ.get(ENV) or None)


def env(grant: Grant) -> Dict[str, str]:
    """The environment a program under this grant is started with."""
    return {**os.environ, ENV: json.dumps(grant.to_json())} if grant.narrowed else dict(os.environ)


def asked(*, repo: bool = False, images: bool = False, read_only: bool = False,
          commands: Optional[List[str]] = None, paths: Optional[List[str]] = None) -> Grant:
    """What an agent's request asks for, before its parent's grant bounds
    it: a picture writes only where pictures go; a folder is a delegated
    coding task in that folder's copy, with the commands named; anything
    else — a question, a review — reads."""
    cmds = tuple(c.strip() for c in commands or () if c.strip())
    if images:
        return Grant("write", (_abs(IMAGES),), ())
    if repo and not read_only:
        return Grant("write", tuple(_abs(p) for p in paths or ()), cmds)
    return Grant("read", (), cmds)


def narrow(parent: Grant, wanted: Grant) -> Grant:
    """What the child gets: what it asked for, cut to what its parent has.
    Never wider than either."""
    level = LEVELS[min(LEVELS.index(parent.level), LEVELS.index(wanted.level))]
    if not parent.narrowed:
        paths = wanted.paths
    else:
        paths = tuple(p for p in wanted.paths if any(_inside(p, q) for q in parent.paths))
    if parent.commands is None:
        commands = wanted.commands
    elif wanted.commands is None:
        commands = parent.commands
    else:
        commands = tuple(c for c in wanted.commands if any(_covers(q, c) for q in parent.commands))
    if level != "full" and commands is None:
        commands = ()
    return Grant(level, paths, commands)


def for_agent(parent: Grant, **request: Any) -> Grant:
    """The grant of a run an agent asked for (`asked`, bounded by `parent`)."""
    return narrow(parent, asked(**request))


# ---- rendered for each harness -------------------------------------------

def claude_argv(grant: Grant) -> List[str]:
    """Claude Code (`claude -p`): the permission mode and the tool lists.
    Headless, a tool outside them is refused — nobody is asked."""
    if not grant.narrowed:
        return []
    bash = [f"Bash({c}:*)" for c in grant.commands or ()]
    allowed = list(READ_TOOLS) + bash
    denied: List[str] = []
    if grant.level == "write":
        allowed += EDIT_TOOLS
        mode = "acceptEdits"                    # edits inside its folder and the added dirs
    else:
        denied += EDIT_TOOLS
        mode = "default"
    if not bash:
        denied.append("Bash")
    argv = ["--permission-mode", mode, "--allowedTools", *allowed]
    if denied:
        argv += ["--disallowedTools", *denied]
    for p in grant.paths:
        argv += ["--add-dir", p]
    return argv


def codex_argv(grant: Grant) -> List[str]:
    """Codex (`codex exec`), and the local models that borrow its harness:
    the sandbox. Codex has no per-command list to hand; its sandbox keeps
    whatever runs to the folder and off the network, which is the bound."""
    if not grant.narrowed:
        return []
    if grant.level == "read":
        return ["-s", "read-only"]
    argv = ["-s", "workspace-write"]
    if grant.paths:
        argv += ["-c", "sandbox_workspace_write.writable_roots=" + json.dumps(list(grant.paths))]
    return argv


def gemini_mode(grant: Grant) -> str:
    """Gemini CLI's approval mode: edits for ``write``, the default
    (nothing that changes anything, headless) for ``read``."""
    if not grant.narrowed:
        return ""
    return "auto_edit" if grant.level == "write" else "default"


# ---- what a narrowed run was refused --------------------------------------

def refusal(tool: str, given: Any) -> Dict[str, str]:
    """One thing a narrowed run was denied, from the program's own report
    (Claude Code's ``permission_denials``): the tool, what it was after in a
    few words, and what would allow it — a ``command`` prefix, an ``edit``,
    a ``path`` to write — when a grant can say it at all. An MCP tool or
    anything else is only described: no grant covers it."""
    given = given if isinstance(given, dict) else {}
    out: Dict[str, str] = {"tool": str(tool or "?")}
    if tool == "Bash":
        cmd = str(given.get("command") or "").strip()
        out["what"] = "run `" + cmd[:160] + "`"
        prefix = _prefix(cmd)
        if prefix:
            out["command"] = prefix
    elif tool in EDIT_TOOLS:
        path = str(given.get("file_path") or given.get("notebook_path") or "").strip()
        out["what"] = "edit `" + (_home(_abs(path)) if path else "a file") + "`"
        out["edit"] = "1"
        if path:
            out["path"] = os.path.dirname(_abs(path))
    else:
        out["what"] = "use " + out["tool"]
    return out


def widened(grant: Grant, refused: List[Dict[str, str]]) -> Grant:
    """The grant that would have allowed what was refused: the commands
    added, edits if it wanted to edit, the folders it wanted to write in.
    Never ``full`` — allowing more is still a narrowed run."""
    if not grant.narrowed:
        return grant
    level = grant.level
    paths = list(grant.paths)
    commands = list(grant.commands or ())
    for r in refused:
        if r.get("command") and not any(_covers(c, r["command"]) for c in commands):
            commands.append(r["command"])
        if r.get("edit"):
            level = "write"
        p = r.get("path")
        if p and not any(_inside(p, q) for q in paths):
            paths.append(p)
    if level == "read":
        paths = []                      # nothing to write in when it may not write
    return Grant(level, tuple(paths), tuple(commands))


def refused_line(refused: List[Dict[str, str]], run: str) -> str:
    """What the thread says under an answer whose run was refused things."""
    wanted = "; ".join(r.get("what") or r.get("tool", "?") for r in refused[:5])
    if len(refused) > 5:
        wanted += f"; and {len(refused) - 5} more"
    return (f"eki: this run wasn't allowed to {wanted} — "
            f"`eki allow {run}` allows it and runs the request again")


def _prefix(cmd: str) -> str:
    """The command prefix to allow for a refused command line: its program,
    and the verb after it when there is one (`git push`, `npm install`)."""
    words = cmd.split()
    while words and "=" in words[0] and not words[0].startswith("="):
        words = words[1:]                   # FOO=1 pytest → pytest
    if not words:
        return ""
    if len(words) > 1 and words[1].replace("-", "").isalpha() and not words[1].startswith("-"):
        return " ".join(words[:2])
    return words[0]


# ---- paths -----------------------------------------------------------------

def _abs(p: Any) -> str:
    return os.path.realpath(os.path.expanduser(str(p).strip()))


def _inside(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def _covers(allowed: str, cmd: str) -> bool:
    """A parent allowed to run `git` may hand on `git log`; not the reverse."""
    return cmd == allowed or cmd.startswith(allowed + " ")


def _home(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if _inside(p, home) else p
