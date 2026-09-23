# SPDX-License-Identifier: Apache-2.0
"""One skill store, seen by every backend (ROADMAP, Stage 1).

A skill is a folder with a `SKILL.md` — name and description in the
frontmatter, instructions below, optional scripts and resources beside it
(the Agent Skills format Claude Code and Codex both read). eki keeps one
copy of each in ~/.eki/skills/, under git, and gives each backend a view:

- Claude Code reads ~/.claude/skills/<name>/ — an enabled skill is a
  symlink there into the store (the program follows it).
- Codex reads ~/.agents/skills/<name>/ — the same.
- Gemini CLI reads ~/.gemini/skills/<name>/ — the same.
- Local and API models have no loader, so the engine is theirs: the names
  and descriptions go in the system prompt, and the body is handed over
  only when the skill is invoked (`/name` or `$name`) or the model asks
  for it by answering `[[skill:name]]` (see `Engine._skilled`).

Turning a skill off for a backend removes the link, never the skill. What
eki knows about a skill that isn't the skill — which backends it is on
for, where it came from — lives in a sidecar, ~/.eki/skills.json, so the
`SKILL.md` stays portable. Every change is a commit in the store, which
makes a skill the safest thing eki can change about itself: readable,
revertible, no engine swap.

Nothing already in the CLIs' own folders is touched unless
you import it: a real folder there is moved into the store once and linked
back. A link eki didn't make, or a folder of the same name, is left alone
and reported as a conflict.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOME = Path("~/.eki").expanduser()
STORE = HOME / "skills"
SIDECAR = HOME / "skills.json"
VIEWS: Dict[str, Path] = {
    "claude": Path("~/.claude/skills").expanduser(),
    "codex": Path("~/.agents/skills").expanduser(),
    "gemini": Path("~/.gemini/skills").expanduser(),
}
#: each program's own home and binary: a view is kept only for a program
#: that is here — installed, or with a home of its own already — so eki
#: never makes ~/.gemini on a Mac that has never had Gemini CLI
HOMES: Dict[str, Path] = {
    "claude": Path("~/.claude").expanduser(),
    "codex": Path("~/.codex").expanduser(),
    "gemini": Path("~/.gemini").expanduser(),
}
BINARIES = {"claude": "claude", "codex": "codex", "gemini": "gemini"}
#: where Codex looked before ~/.agents/skills; only read, for import
LEGACY = [Path("~/.codex/skills").expanduser()]
BACKENDS = ("claude", "codex", "gemini", "local")
#: the backends there were before a sidecar entry said which ones it is on
#: for; one added since is on for every skill until you turn it off
FIRST_BACKENDS = ("claude", "codex", "local")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
#: what a model without a loader answers to ask for a skill's body
PICK_RE = re.compile(r"^\s*\[\[skill:\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\s*\]\]")
PICK_PREFIX = "[[skill:"
INVOKE_RE = re.compile(r"^\s*[/$]([A-Za-z0-9][A-Za-z0-9_.-]{0,63})(?:\s+|$)")
#: the body a local model is handed is capped; a skill is instructions, not a corpus
BODY_LIMIT = 24_000


# ---- reading a skill ---------------------------------------------------------

def split(text: str) -> Tuple[Dict[str, Any], str]:
    """Frontmatter and body of a SKILL.md."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            head = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            try:
                import yaml
                meta = yaml.safe_load(head) or {}
            except Exception:
                meta = _loose(head)
            return (meta if isinstance(meta, dict) else {}), body
    return {}, text


def _loose(head: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for line in head.splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip("\"'")
    return out


def read(path: Path) -> Optional[Dict[str, Any]]:
    """A skill folder as eki shows it, or None when it isn't one."""
    md = path / "SKILL.md"
    try:
        text = md.read_text(errors="replace")
    except OSError:
        return None
    meta, body = split(text)
    name = str(meta.get("name") or path.name).strip()
    return {"name": name, "folder": path.name,
            "description": " ".join(str(meta.get("description") or "").split()),
            "path": str(path), "size": len(text), "body_lines": body.count("\n")}


def render(name: str, description: str, body: str) -> str:
    import yaml
    head = yaml.safe_dump({"name": name, "description": description},
                          sort_keys=False, allow_unicode=True, width=1000).strip()
    return f"---\n{head}\n---\n\n{body.strip()}\n"


# ---- the sidecar -------------------------------------------------------------

def _meta() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(SIDECAR.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in (data.get("skills") or {}).items() if isinstance(v, dict)}


def _save_meta(meta: Dict[str, Dict[str, Any]]) -> None:
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIDECAR.with_suffix(".tmp")
    tmp.write_text(json.dumps({"skills": meta}, indent=2, sort_keys=True))
    tmp.replace(SIDECAR)


def _entry(meta: Dict[str, Dict[str, Any]], folder: str) -> Dict[str, Any]:
    e = meta.get(folder)
    if e is None:
        e = meta[folder] = {"backends": list(BACKENDS), "enabled": True,
                            "origin": "store", "added": int(time.time())}
    else:
        e["backends"] = _backends(e)
    e["offered"] = list(BACKENDS)
    return e


def _backends(e: Dict[str, Any]) -> List[str]:
    """The backends a skill is on for: what its entry says, and any backend
    eki learned to reach after the entry was written — a new program sees
    the skills you already have, not an empty folder."""
    offered = e.get("offered") or FIRST_BACKENDS
    on = set(e.get("backends") or []) | {b for b in BACKENDS if b not in offered}
    return [b for b in BACKENDS if b in on]


# ---- git ------------------------------------------------------------------------

def _git(*args: str) -> str:
    if not shutil.which("git"):
        return ""
    env = {**os.environ, "GIT_AUTHOR_NAME": "eki", "GIT_AUTHOR_EMAIL": "eki@localhost",
           "GIT_COMMITTER_NAME": "eki", "GIT_COMMITTER_EMAIL": "eki@localhost"}
    try:
        r = subprocess.run(["git", "-C", str(STORE), *args], capture_output=True,
                           text=True, env=env, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _ensure_store() -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    if not (STORE / ".git").exists() and shutil.which("git"):
        _git("init", "-q", "-b", "main")
        (STORE / "README.md").write_text(
            "# eki skills\n\nOne folder per skill, each with a SKILL.md. eki links the enabled\n"
            "ones into ~/.claude/skills, ~/.agents/skills and ~/.gemini/skills and hands\n"
            "them to local models itself. Every change is a commit here: `eki skills log`, and\n"
            "`git revert` undoes one.\n")
        _commit("start the skill store")


def _commit(message: str) -> None:
    _git("add", "-A")
    if _git("status", "--porcelain").strip():
        _git("commit", "-q", "-m", message)


def settle_stray(by: str = "", run: str = "") -> List[str]:
    """Commit what someone changed in the store without eki — an agent
    that edited a skill through its link, you in an editor — so every
    change stays a commit of its own, not swept into eki's next one.
    Returns the skills that changed."""
    if not (STORE / ".git").exists():
        return []
    status = _git("status", "--porcelain")
    changed = sorted({line[3:].strip().strip('"').split("/", 1)[0]
                      for line in status.splitlines() if len(line) > 3})
    if not changed:
        return []
    who = f" by {by}" if by else ""
    subject = f"edit {', '.join(changed)}{who}, outside eki"
    if len(subject) > 72:
        subject = f"edit {len(changed)} skills{who}, outside eki"
    _commit(subject + (f"\n\nRun: {run}" if run else ""))
    return changed


def history(limit: int = 30, name: str = "") -> List[Dict[str, str]]:
    args = ["log", f"-n{limit}", "--format=%h%x09%ad%x09%s", "--date=short"]
    if name:
        args += ["--", name]
    out = []
    for line in _git(*args).splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append({"commit": parts[0], "date": parts[1], "message": parts[2]})
    return out


# ---- the store ------------------------------------------------------------------

def _folders() -> List[Path]:
    if not STORE.is_dir():
        return []
    return sorted(p for p in STORE.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / "SKILL.md").is_file())


def _view_state(folder: str) -> Dict[str, str]:
    """For each CLI: linked | missing | conflict (something else holds the name)."""
    out = {}
    for backend, root in VIEWS.items():
        p = root / folder
        if p.is_symlink():
            out[backend] = "linked" if _points_into_store(p) else "conflict"
        elif p.exists():
            out[backend] = "conflict"
        else:
            out[backend] = "missing"
    return out


def _points_into_store(link: Path) -> bool:
    try:
        target = Path(os.readlink(link))
    except OSError:
        return False
    if not target.is_absolute():
        target = (link.parent / target)
    try:
        return target.resolve().parent == STORE.resolve()
    except OSError:
        return False


def list_skills() -> List[Dict[str, Any]]:
    meta = _meta()
    out = []
    for folder in _folders():
        s = read(folder)
        if not s:
            continue
        e = meta.get(folder.name) or {"backends": list(BACKENDS), "enabled": True, "origin": "store"}
        s.update({"backends": _backends(e),
                  "enabled": bool(e.get("enabled", True)), "origin": e.get("origin", "store"),
                  "generated": bool(e.get("generated")), "uses": int(e.get("uses") or 0),
                  "learned": e.get("learned") if isinstance(e.get("learned"), dict) else None,
                  "views": _view_state(folder.name)})
        out.append(s)
    return out


def get(name: str) -> Optional[Dict[str, Any]]:
    for s in list_skills():
        if s["folder"] == name or s["name"] == name:
            return s
    return None


def source(name: str) -> str:
    s = get(name)
    if not s:
        raise KeyError(name)
    return (Path(s["path"]) / "SKILL.md").read_text(errors="replace")


def _check_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise ValueError("a skill name is letters, digits, dots, dashes or underscores (max 64)")


def put(name: str, text: str = "", description: str = "", body: str = "",
        backends: Optional[List[str]] = None, message: str = "") -> Dict[str, Any]:
    """Create or replace a skill. Either the whole SKILL.md (`text`) or a
    description and a body. The frontmatter's name follows the folder."""
    _check_name(name)
    _ensure_store()
    if text:
        meta_fm, b = split(text)
        if not meta_fm.get("description") and not description:
            raise ValueError("SKILL.md needs a description in its frontmatter")
        desc = description or str(meta_fm.get("description") or "")
        extra = {k: v for k, v in meta_fm.items() if k not in ("name", "description")}
        text = render(name, desc, b)
        if extra:
            import yaml
            fm, b2 = split(text)
            fm.update(extra)
            text = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True,
                                            width=1000).strip() + "\n---\n\n" + b2.lstrip()
    else:
        if not description.strip():
            raise ValueError("a skill needs a description — it is how a model knows when to use it")
        text = render(name, description, body)
    folder = STORE / name
    existed = folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(text)
    meta = _meta()
    e = _entry(meta, name)
    e["generated"] = False
    if isinstance(e.get("learned"), dict):
        e["learned"]["edited_by_you"] = True    # yours now: eki won't rewrite it
    if backends is not None:
        e["backends"] = [b for b in BACKENDS if b in backends]
    _save_meta(meta)
    _commit(message or (f"edit {name}" if existed else f"add {name}"))
    sync()
    return get(name) or {}


def learnable(name: str) -> bool:
    """Whether eki may rewrite this skill: it learned it, and nobody has
    edited it since (eki/learn.py)."""
    e = _meta().get(name) or {}
    learned = e.get("learned")
    return (name != EKI_SKILL and e.get("origin") == "learned" and isinstance(learned, dict)
            and not learned.get("edited_by_you"))


def learn(name: str, description: str, body: str, *, why: str, run: str = "",
          conversation: str = "", backends: Optional[List[str]] = None,
          enabled: bool = True) -> Dict[str, Any]:
    """Write a skill eki learned from a run, or improve one it learned before.
    The commit says why and which run taught it. A skill someone else wrote
    or edited is refused."""
    _check_name(name)
    _ensure_store()
    folder = STORE / name
    existed = (folder / "SKILL.md").is_file()
    if existed and not learnable(name):
        raise ValueError(f"{name} isn't eki's to rewrite")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(render(name, description, body))
    meta = _meta()
    e = _entry(meta, name)
    e["origin"] = "learned"
    e["generated"] = False
    if not existed:
        e["enabled"] = bool(enabled)
    if backends:
        e["backends"] = [b for b in BACKENDS if b in backends]
    before = e.get("learned") if isinstance(e.get("learned"), dict) else {}
    e["learned"] = {"why": why, "run": run, "conversation": conversation,
                    "at": int(time.time()), "times": int(before.get("times") or 0) + 1,
                    "first": before.get("first") or int(time.time())}
    _save_meta(meta)
    verb = "improve" if existed else "learn"
    subject = f"{verb} {name}"
    head = re.split(r"(?<=[.;!?])\s", why.strip(), maxsplit=1)[0].rstrip(".")
    if head and len(subject) + 2 + len(head) <= 72:
        subject += f": {head}"
    body = "\n".join(x for x in (why.strip(), "",
                                  f"Run: {run}" if run else "",
                                  f"Conversation: {conversation}" if conversation else "") if x is not None)
    _commit(subject + "\n\n" + body.strip())
    sync()
    return get(name) or {}


def adopt(names: List[str], by: str = "", run: str = "") -> List[str]:
    """Take skill folders an agent wrote straight into a CLI's folder into
    the store, linked back. Written at your request, so they are yours:
    eki reads them and never rewrites them."""
    report = import_existing(names, message=f"take in {', '.join(names)}"
                             + (f", written by {by}" if by else "")
                             + (f"\n\nRun: {run}" if run else ""))
    done = report.get("imported") or []
    if done:
        meta = _meta()
        for name in done:
            e = _entry(meta, name)
            e["origin"] = f"agent:{by or 'unknown'}"
            e["adopted"] = {"by": by, "run": run, "at": int(time.time())}
        _save_meta(meta)
    return done


def remove(name: str) -> None:
    """Out of the store (the commit keeps it: `git revert` brings it back)."""
    _check_name(name)
    folder = STORE / name
    for root in VIEWS.values():
        link = root / name
        if link.is_symlink() and _points_into_store(link):
            link.unlink()
    if folder.exists():
        shutil.rmtree(folder)
    meta = _meta()
    gone = meta.pop(name, None) or {}
    if isinstance(gone.get("learned"), dict):
        from . import observe
        observe.note("history", what="a learned skill was removed", skill=name,
                     why=gone["learned"].get("why"))
    _save_meta(meta)
    _commit(f"remove {name}")


def set_enabled(name: str, enabled: bool, backend: str = "") -> Dict[str, Any]:
    if not (STORE / name / "SKILL.md").is_file():
        raise KeyError(name)
    meta = _meta()
    e = _entry(meta, name)
    if backend:
        if backend not in BACKENDS:
            raise ValueError(f"backend is one of {', '.join(BACKENDS)}")
        on = set(e.get("backends") or [])
        (on.add if enabled else on.discard)(backend)
        e["backends"] = [b for b in BACKENDS if b in on]
    else:
        e["enabled"] = bool(enabled)
    _save_meta(meta)
    sync()
    return get(name) or {}


def used(name: str) -> None:
    meta = _meta()
    if name in meta:
        meta[name]["uses"] = int(meta[name].get("uses") or 0) + 1
        meta[name]["last_used"] = int(time.time())
        _save_meta(meta)


def enabled_for(backend: str) -> List[Dict[str, Any]]:
    return [s for s in list_skills() if s["enabled"] and backend in s["backends"]]


# ---- the views -------------------------------------------------------------------

def present(backend: str) -> bool:
    """Whether this Mac has the program: its home folder exists (it has run
    here), or its binary is where CLIs live. Both, not just the binary: an
    engine started from the GUI may not find a binary a shell would, and
    that mustn't take a working view away."""
    home = HOMES.get(backend)
    if home is not None and home.is_dir():
        return True
    from .adapters.claude_code import _find_binary
    return bool(_find_binary(BINARIES.get(backend, backend)))


def sync() -> Dict[str, Any]:
    """Make each CLI's folder show exactly the enabled skills: a link per
    skill that is on for it, none for one that isn't, and none at all for
    a program that isn't here. Only links into the store are ever made or
    removed."""
    report: Dict[str, Any] = {"linked": [], "unlinked": [], "conflicts": []}
    if not STORE.is_dir():
        return report
    wanted = {b: {s["folder"] for s in enabled_for(b)} if present(b) else set() for b in VIEWS}
    for backend, root in VIEWS.items():
        # links into the store that should no longer be there, or point at nothing
        if root.is_dir():
            for p in root.iterdir():
                if p.is_symlink() and _points_into_store(p) and (
                        p.name not in wanted[backend] or not (STORE / p.name).is_dir()):
                    p.unlink()
                    report["unlinked"].append(f"{backend}:{p.name}")
        for folder in sorted(wanted[backend]):
            link = root / folder
            if link.is_symlink() and _points_into_store(link):
                continue
            if link.exists() or link.is_symlink():
                report["conflicts"].append(f"{backend}:{folder}")
                continue
            root.mkdir(parents=True, exist_ok=True)
            link.symlink_to(STORE / folder, target_is_directory=True)
            report["linked"].append(f"{backend}:{folder}")
    return report


def unmanaged() -> List[Dict[str, Any]]:
    """Skills in the CLIs' own folders that eki doesn't hold yet."""
    out = []
    held = {p.name for p in _folders()}
    for backend, root in [*VIEWS.items(), *(("codex", r) for r in LEGACY)]:
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if p.is_symlink() and _points_into_store(p):
                continue
            if p.name.startswith(".") or not p.is_dir() or not (p / "SKILL.md").is_file():
                continue
            s = read(p)
            if s:
                s["backend"] = backend
                s["held"] = p.name in held
                s["link"] = p.is_symlink()
                out.append(s)
    return out


def import_existing(names: Optional[List[str]] = None, message: str = "") -> Dict[str, Any]:
    """Take skills found in the CLIs' folders into the store, then link them
    back. A real folder is moved; a link someone else made is copied
    through and left where it is. A name the store already has is skipped."""
    _ensure_store()
    meta = _meta()
    done, skipped = [], []
    for s in unmanaged():
        folder = Path(s["path"])
        if names and folder.name not in names and s["name"] not in names:
            continue
        if s["held"] or not NAME_RE.match(folder.name):
            skipped.append(folder.name)
            continue
        dest = STORE / folder.name
        if s["link"]:
            shutil.copytree(folder.resolve(), dest, symlinks=True)
        else:
            shutil.move(str(folder), str(dest))
        e = _entry(meta, folder.name)
        e["origin"] = f"{s['backend']}:{folder.parent}"
        done.append(folder.name)
    _save_meta(meta)
    if done:
        _commit(message or ("import " + ", ".join(done)))
    report = sync()
    return {"imported": done, "skipped": skipped, **report}


# ---- the skill that says how to call eki ------------------------------------

EKI_SKILL = "eki"


def eki_skill_text() -> str:
    body = """\
eki is the model hub on this Mac. It routes a request to the backend that
fits — Claude Code, Codex, Gemini CLI, a local model (MLX / GGUF), a paid
API, or the local image model — and keeps every run in its own history, so
work you hand to it survives this session.

Use it when the task wants a different model than you: a picture, an
uncensored or long creative draft for a local model, a second agent on a
separate job, or a question you want answered without spending your own
context.

## From a shell

```
eki ask "…"                       # routed; prints the answer as it streams
eki ask "…" --backend <key>       # a backend by key (see `eki backends`)
eki ask "a watercolor fox" --image  # a picture; the path is in the answer
eki ask "…" -r <folder>           # a program that may edit that folder
eki ask "…" -d                    # detached: prints a run id
eki runs | eki watch <id> | eki cancel <id> | eki diff <id>
eki backends                      # what is available and healthy
eki models [list|start|stop] <key>
eki skills                        # the skills every backend shares
```

## As tools

The `eki` MCP server gives the same through `eki_capabilities` (read it
first), `eki_ask` and `eki_image`.
"""
    return render(EKI_SKILL, "Hand work to eki, the model hub on this Mac: route a prompt to "
                  "another model (local LLM, Claude Code, Codex, Gemini CLI, API), generate an image, or "
                  "start a background run. Use when a task needs a different model, a picture, "
                  "or work that should keep running on its own.", body)


def ensure_builtin() -> None:
    """Write (or refresh) eki's own skill, unless you have edited it."""
    _ensure_store()
    meta = _meta()
    e = meta.get(EKI_SKILL)
    if e is not None and not e.get("generated"):
        return                                  # yours now
    folder = STORE / EKI_SKILL
    text = eki_skill_text()
    try:
        current = (folder / "SKILL.md").read_text()
    except OSError:
        current = ""
    if e is None:
        e = meta[EKI_SKILL] = {"backends": ["claude", "codex", "gemini"], "enabled": True,
                               "offered": list(BACKENDS),
                               "origin": "eki", "generated": True, "added": int(time.time())}
        _save_meta(meta)
    if current != text:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(text)
        _commit("refresh the eki skill")


def boot() -> Dict[str, Any]:
    """At engine start: the store exists, eki's skill is current, the views match."""
    try:
        settle_stray()
        ensure_builtin()
        return sync()
    except OSError as e:
        return {"error": str(e)}


# ---- the loader for backends that have none ------------------------------------

def catalog_prompt(backend: str = "local") -> str:
    """The system-prompt section a model without a skill loader gets."""
    skills = enabled_for(backend)
    if not skills:
        return ""
    lines = [f"- {s['name']}: {s['description']}" for s in skills]
    return ("You have skills: instructions written for particular kinds of task.\n"
            + "\n".join(lines) + "\n\n"
            "If one of them clearly fits the request, reply with exactly "
            "[[skill:NAME]] and nothing else; its instructions will follow and you "
            "then answer. If none fits, answer normally and do not mention skills.")


def invoked(prompt: str, backend: str = "local") -> Tuple[Optional[Dict[str, Any]], str]:
    """`/name rest` or `$name rest` naming an enabled skill: the skill, and the rest."""
    m = INVOKE_RE.match(prompt or "")
    if not m:
        return None, prompt
    name = m.group(1)
    for s in enabled_for(backend):
        if name in (s["folder"], s["name"]):
            return s, prompt[m.end():].strip()
    return None, prompt


def picked(text: str, backend: str = "local") -> Optional[Dict[str, Any]]:
    m = PICK_RE.match(text or "")
    if not m:
        return None
    for s in enabled_for(backend):
        if m.group(1) in (s["folder"], s["name"]):
            return s
    return None


def could_be_pick(text: str) -> bool:
    """Whether the start of an answer may still turn into `[[skill:…]]`."""
    t = (text or "").lstrip()
    if not t:
        return True
    if len(t) <= len(PICK_PREFIX):
        return PICK_PREFIX.startswith(t)
    return t.startswith(PICK_PREFIX) and "]]" not in t and len(t) < 80


def loaded_prompt(skill: Dict[str, Any]) -> str:
    """A skill's instructions, as handed to a model with no loader."""
    folder = Path(skill["path"])
    _, body = split((folder / "SKILL.md").read_text(errors="replace"))
    body = body.strip()
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT] + "\n\n[…the rest of the skill is cut here]"
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*")
                   if p.is_file() and p.name != "SKILL.md" and ".git" not in p.parts)[:40]
    extra = ("\n\nFiles that come with it (in " + str(folder) + "): " + ", ".join(files)) if files else ""
    used(skill["folder"])
    return f"Use the skill \"{skill['name']}\" for this request. Its instructions:\n\n{body}{extra}"
