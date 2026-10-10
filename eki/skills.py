"""One skill store for every program.

Skills are Agent Skills folders (`<name>/SKILL.md`) in `EKI_HOME/skills`.
Each program is handed the same store its own way:

- Claude Code: per run, as a plugin (`--plugin-dir`) whose skills folder
  is the store — nothing is written into `~/.claude`.
- Codex: it reads user skills from `~/.agents/skills`, so each skill is
  linked there. Only links eki made are ever changed.

A skill comes from a folder or a git URL (`https://…`, `git@…`, `OWNER/REPO`,
optionally `#subdir`). A URL is cloned `--depth 1` under `EKI_HOME/tmp`, its
SKILL.md folder copied into the store, and the clone removed. Where each skill
came from is kept in `skill-sources.json` (`{name: source}`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from . import paths, workspace

CLONE_TIMEOUT = 120
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def agents_dir() -> Path:
    return Path(os.environ.get("EKI_AGENTS_SKILLS") or "~/.agents/skills").expanduser()


def _front(text: str) -> Dict[str, str]:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out: Dict[str, str] = {}
    for line in (m.group(1).splitlines() if m else []):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def sources() -> Dict[str, str]:
    try:
        got = json.loads(paths.config("skill-sources").read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _set_source(name: str, source: Optional[str]) -> None:
    got = sources()
    if source is None:
        if name not in got:
            return
        got.pop(name)
    else:
        got[name] = source
    paths.config("skill-sources").write_text(json.dumps(got, indent=2) + "\n")


def all_skills() -> List[Dict]:
    found, where = [], sources()
    for d in sorted(paths.skills().iterdir()):
        md = d / "SKILL.md"
        if md.is_file():
            meta = _front(md.read_text(errors="replace"))
            found.append({"name": d.name, "description": meta.get("description", ""), "path": str(d),
                          "source": where.get(d.name), "added_at": d.stat().st_mtime})
    return found


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")


def is_git(source: str) -> bool:
    """A git URL or OWNER/REPO (with an optional #subdir) — not a folder on this Mac."""
    url = source.strip().split("#", 1)[0]
    if re.match(r"^(https?|ssh|git|file)://", url) or re.match(r"^[^@/\s]+@[^:/\s]+:", url):
        return True
    return bool(_REPO.match(url)) and not Path(url).expanduser().exists()


def _clone_url(url: str) -> str:
    return f"https://github.com/{url}.git" if _REPO.match(url) else url


def _copy(src: Path, fallback: str, into: Optional[Path] = None, replace: bool = True) -> str:
    """Copy the skill folder `src` into `into` (the store by default) as `<name>/`; its name.
    An existing one is overwritten only with `replace`, else ValueError."""
    if src.is_file() and src.name == "SKILL.md":
        src = src.parent
    md = src / "SKILL.md"
    if not md.is_file():
        raise ValueError(f"{src} has no SKILL.md")
    name = _front(md.read_text(errors="replace")).get("name") or fallback or src.name
    name = slug(name)
    if not name:
        raise ValueError(f"{src}: can't make a skill name")
    dest = (into if into is not None else paths.skills()) / name
    if dest.exists() or dest.is_symlink():
        if not replace:
            raise ValueError(f"{name} is already there; use --replace to overwrite it")
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
    os.utime(dest)                                    # added_at: when it came in, not the source's time
    return name


def _from_git(source: str, into: Optional[Path] = None, replace: bool = True) -> str:
    url, _, sub = source.strip().partition("#")
    sub = sub.strip("/")
    tmp = paths.home() / "tmp"
    tmp.mkdir(exist_ok=True)
    clone = Path(tempfile.mkdtemp(prefix="skill-", dir=tmp))
    try:
        try:
            out = subprocess.run(["git", *workspace.QUIET, "clone", "-q", "--depth", "1", _clone_url(url),
                                  str(clone / "repo")], capture_output=True, text=True,
                                 timeout=CLONE_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ValueError(f"git clone {url} timed out after {CLONE_TIMEOUT}s") from None
        if out.returncode != 0:
            why = next((ln for ln in out.stderr.splitlines() if ln.strip()), "git clone failed")
            raise ValueError(f"can't clone {url}: {why.strip()}")
        root = (clone / "repo").resolve()
        src = (root / sub).resolve() if sub else root
        if src != root and root not in src.parents:
            raise ValueError(f"{sub} is outside the repository")
        repo_name = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
        return _copy(src, Path(sub).name if sub else repo_name, into, replace)
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def add(source: str) -> str:
    """Copy a skill into the store from a folder or a git URL; returns its name."""
    source = (source or "").strip()
    if not source:
        raise ValueError("a skill folder or git URL is needed")
    if is_git(source):
        name = _from_git(source)
    else:
        src = Path(source).expanduser().resolve()
        name = _copy(src, "")
        source = str(src)
    _set_source(name, source)
    sync_codex()
    return name


def remove(name: str) -> bool:
    dest = paths.skills() / name
    if not name or "/" in name or not dest.exists():
        return False
    shutil.rmtree(dest)
    _set_source(name, None)
    sync_codex()
    return True


def claude_plugin() -> Optional[str]:
    """A plugin folder that is the store, for `claude --plugin-dir`."""
    if not all_skills():
        return None
    root = paths.home() / "claude-plugin"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.write_text(json.dumps({"name": "eki", "version": "1.0.0",
                                    "description": "Skills from eki's store"}, indent=2))
    link = root / "skills"
    if link.is_symlink() and Path(os.readlink(link)) != paths.skills():
        link.unlink()                         # the store moved (a new EKI_HOME)
    if not link.is_symlink():
        if link.exists():
            shutil.rmtree(link)
        link.symlink_to(paths.skills(), target_is_directory=True)
    return str(root)


def sync_codex() -> List[str]:
    """Link every skill into ~/.agents/skills; drop links to skills that are gone.
    A name that's already taken by something eki didn't make is left alone."""
    target = agents_dir()
    target.mkdir(parents=True, exist_ok=True)
    store = paths.skills().resolve()
    linked = []
    for s in all_skills():
        link = target / s["name"]
        if link.is_symlink() and Path(os.readlink(link)).resolve().parent == store:
            linked.append(s["name"])
            continue
        if not link.exists() and not link.is_symlink():
            link.symlink_to(Path(s["path"]), target_is_directory=True)
            linked.append(s["name"])
    for link in target.iterdir():
        if link.is_symlink():
            dest = Path(os.readlink(link))
            if dest.parent.resolve() == store and not dest.exists():
                link.unlink()
    return linked
