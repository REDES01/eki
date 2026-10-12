"""A folder gets a history: survey it, then git init, .gitignore and a first commit.

The one place that decides whether a folder can get a git history and gives it
one (docs/design.md, "Projects you work inside"). Git is eki's engine for
parallel work, never the person's chore: what it says is plain words. A folder
a sync service, a pile of media or another app's live edits own is refused,
with chat only as the choice. A folder that already has commits is used as it is.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths, workspace

MEDIA_BLOCK = "# eki: media and big files (the project's assets pool)"
OTHER_BLOCK = "# eki: build output, caches, system files"
FIRST_COMMIT = "the folder as it was"
OFFER = ("eki keeps a history of this folder so several agents can work on it at once "
         "and any change can be undone")
CHAT_ONLY = "Chat only: one agent works in the folder directly, with no parallel work and no queue."

MEDIA = frozenset((".wav .aif .aiff .flac .mp3 .ogg .m4a .mov .mp4 .mkv .avi .webm "
                   ".psd .tif .tiff .exr .raw .cr2 .nef .heic .blend .fbx .zip .dmg .iso").split())
BIG = 20 * 1024 * 1024
WALK = 20000
MEDIA_SHARE = 0.9
MEDIA_MIN = 200 * 1024 * 1024
#: folders the walk skips: they go in the OTHER_BLOCK, never in the history
SKIP_DIRS = ("dist", "build", "out", "target", "__pycache__", ".next", ".cache", "node_modules", ".venv")
OTHER = tuple(f"{d}/" for d in SKIP_DIRS) + (".DS_Store", "Thumbs.db", "Icon[\r]")
CLOUD = (("Dropbox", "Dropbox"), ("GoogleDrive", "Google Drive"), ("OneDrive", "OneDrive"))


class Unfit(ValueError):
    """The folder can't get a history; str is the plain reason followed by CHAT_ONLY."""


def _state(where: Path) -> str:
    if not where.is_dir():
        return "missing"
    top = workspace.repo_of(where)
    if top is None or os.path.realpath(top) != os.path.realpath(where):
        return "plain"                                   # a folder inside another repo gets its own
    if workspace.git(where, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False):
        return "git"
    return "no-commit"


def _synced(where: Path) -> Optional[str]:
    """The sync service that owns `where`, or None."""
    real = Path(os.path.realpath(where))
    home = Path(os.path.realpath(paths.user_home()))
    if real.is_relative_to(home / "Library/Mobile Documents"):
        return "iCloud Drive"
    store = home / "Library/CloudStorage"
    if real.is_relative_to(store) and real != store:
        sub = real.relative_to(store).parts[0]
        return next((name for pre, name in CLOUD if sub.startswith(pre)), sub.split("-")[0])
    for name in ("Dropbox", "Google Drive"):
        if real.is_relative_to(home / name):
            return name
    for up in (real, *real.parents):
        if (up / ".dropbox").is_file():                  # a Dropbox root marks itself; ~/.dropbox is a folder
            return "Dropbox"
    return None


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _walk(where: Path) -> Tuple[List[str], int, int, Optional[str]]:
    """(left-out paths, media bytes, all bytes, a live lock file), at most WALK files."""
    files: List[Tuple[str, bool]] = []
    media = total = 0
    lock = None
    for top, dirs, names in os.walk(where):
        dirs[:] = sorted(d for d in dirs if d != ".git" and d not in SKIP_DIRS)
        for name in sorted(names):
            if len(files) >= WALK:
                break
            p = Path(top) / name
            rel = p.relative_to(where).as_posix()
            if name.startswith("~$") or (name.startswith(".~lock.") and name.endswith("#")):
                lock = lock or rel
            try:
                st = p.lstat()
            except OSError:
                continue
            size = st.st_size if p.is_file() and not p.is_symlink() else 0
            is_media = p.suffix.lower() in MEDIA
            total += size
            media += size if is_media else 0
            files.append((rel, is_media or size >= BIG))
        if len(files) >= WALK:
            break
    out: List[str] = []
    tops: Dict[str, List[bool]] = {}
    for rel, left in files:
        if "/" in rel:
            tops.setdefault(rel.split("/")[0], []).append(left)
    whole = {t for t, lefts in tops.items() if all(lefts)}
    for rel, left in files:
        head = rel.split("/")[0]
        if "/" in rel and head in whole:
            if f"{head}/" not in out:
                out.append(f"{head}/")
        elif left:
            out.append(rel)
    return out, media, total, lock


def _quote(rel: str) -> str:
    """A path as a .gitignore line that matches only it."""
    q = "".join("\\" + c if c in "*?[\\" else c for c in rel)
    return "\\" + q if q[:1] in ("#", "!") else q


def _lines(where: Path) -> List[str]:
    try:
        return (where / ".gitignore").read_text(errors="replace").split("\n")
    except OSError:
        return []


def _ignore(where: Path, left: List[str]) -> List[str]:
    have = set(_lines(where))
    add: List[str] = []
    for header, entries in ((MEDIA_BLOCK, [_quote(r) for r in left]), (OTHER_BLOCK, list(OTHER))):
        new = [e for e in entries if e not in have]
        if new:
            add += ([] if header in have else [header]) + new
    return add


def survey(folder: str | Path) -> dict:
    """What start() would do to `folder`, and whether it may. Reads only."""
    where = Path(folder).expanduser()
    state = _state(where)
    if state == "git":
        return {"state": state, "fit": True, "why": None, "offer": OFFER,
                "left_out": left_out(where), "ignore": []}
    why = None
    service = _synced(where)
    left: List[str] = []
    if service:
        why = (f"This folder is in {service}, which keeps it in sync on its own; "
               "a history beside the sync would fight it.")
    elif state != "missing":
        left, media, total, lock = _walk(where)
        if lock:
            why = (f"Another app has a file here open ({lock}) and changes the folder live, "
                   "so agents working on copies of it would get in its way.")
        elif media >= MEDIA_MIN and media >= MEDIA_SHARE * total:
            why = (f"This folder is mostly large media ({_size(media)} of {_size(total)}), "
                   "which a history can't keep well.")
    return {"state": state, "fit": why is None, "why": why,
            "offer": OFFER if why is None else f"{why} {CHAT_ONLY}",
            "left_out": left, "ignore": _ignore(where, left) if why is None else []}


def _head(where: Path) -> dict:
    return {"branch": workspace.git(where, "symbolic-ref", "--short", "-q", "HEAD", check=False),
            "commit": workspace.git(where, "rev-parse", "HEAD")}


def start(folder: str | Path) -> dict:
    """Give `folder` a history; a folder with commits is returned as it is."""
    where = Path(folder).expanduser()
    seen = survey(where)
    if not seen["fit"]:
        raise Unfit(seen["offer"])
    if seen["state"] == "git":
        return {**_head(where), "left_out": seen["left_out"]}
    where.mkdir(parents=True, exist_ok=True)
    if seen["state"] in ("missing", "plain"):
        named = workspace.git(where, "config", "--global", "init.defaultBranch", check=False)
        workspace.git(where, "init", *([] if named else ["-b", "main"]))
    if seen["ignore"]:
        text = "\n".join(_lines(where))
        if text and not text.endswith("\n"):
            text += "\n"
        blocks: List[str] = []
        for line in seen["ignore"]:
            if line in (MEDIA_BLOCK, OTHER_BLOCK) and (blocks or text):
                blocks.append("")                       # a blank line before each block
            blocks.append(line)
        (where / ".gitignore").write_text(text + "\n".join(blocks) + "\n")
    who = []
    if not (workspace.git(where, "config", "user.name", check=False)
            and workspace.git(where, "config", "user.email", check=False)):
        who = ["-c", "user.name=eki", "-c", "user.email=eki@localhost"]
    workspace.git(where, "add", "-A")
    workspace.git(where, *who, "commit", "-q", "--allow-empty", "--no-verify", "-m", FIRST_COMMIT)
    return {**_head(where), "left_out": seen["left_out"]}


def left_out(root: str | Path) -> List[str]:
    """The lines under MEDIA_BLOCK in root/.gitignore, up to a blank line or the next header."""
    lines = _lines(Path(root).expanduser())
    if MEDIA_BLOCK not in lines:
        return []
    out = []
    for line in lines[lines.index(MEDIA_BLOCK) + 1:]:
        if not line.strip() or line.startswith("# "):
            break
        out.append(line)
    return out
