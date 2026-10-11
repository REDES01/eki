"""A worktree's files as APFS clones of the source checkout.

`git worktree add` writes every tracked file out fresh: a worktree of a
project with 80 MB of art costs 80 MB. On APFS a file can be a clone of
another (`clonefile`): the two share their blocks until one is changed. So
a worktree is made with `--no-checkout` and `fill` writes its files: each
tracked file that is the same in the source checkout as at the commit is
cloned from there, the rest (changed in the source, symlinks, filtered by
LFS or another attribute, submodules) are checked out by git itself.

The result is what a plain checkout gives — same bytes, same executable
bits, symlinks as symlinks — and `git status` is clean, checked before the
index is put in place. Nothing untracked or ignored is ever copied. Not on
macOS, across volumes, or when a clone fails: a plain checkout of the whole
tree.

Safe after a kill: the index is written last, so a worktree without one is
half made and `fill` (or `workspace.add`) does it again.
"""
from __future__ import annotations

import ctypes
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Set

#: off: never clone, always a plain checkout (tests turn it off)
CLONE = True
#: attributes that make the working file differ from the blob: git writes those
FILTERS = ("filter", "ident", "working-tree-encoding", "eol", "text")
#: what a plain checkout gives a new file, under this process's umask
#: clones at once: clonefile is a syscall, and waits mostly on the disk
WORKERS = 8
_UMASK = os.umask(0)
os.umask(_UMASK)

_libc: List[Optional[object]] = []


def _git(where: str | Path, *args: str, index: Optional[str] = None,
         stdin: Optional[str] = None, check: bool = True) -> str:
    from . import workspace
    env = dict(workspace._env[0] or os.environ)
    if index:
        env["GIT_INDEX_FILE"] = index
    out = subprocess.run(["git", *workspace.QUIET, "-C", str(where), *args], input=stdin,
                         capture_output=True, text=True, env=env)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def _clone(src: str, dst: str) -> None:
    """clonefile(2): `dst` shares `src`'s blocks. Raises OSError when it can't."""
    if not _libc:
        _libc.append(ctypes.CDLL(None, use_errno=True))
    lib = _libc[0]
    if lib is None or not hasattr(lib, "clonefile"):
        raise OSError("no clonefile here")
    if lib.clonefile(os.fsencode(src), os.fsencode(dst), 1) != 0:   # CLONE_NOFOLLOW
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), dst)


def _entries(dest: Path, sha: str) -> Dict[str, str]:
    """path -> mode of every entry in the tree at `sha`."""
    out = _git(dest, "ls-tree", "-r", "-z", "--full-tree", sha)
    entries = {}
    for rec in out.split("\0"):
        if rec:
            meta, path = rec.split("\t", 1)
            entries[path] = meta.split(" ", 1)[0]
    return entries


def _differ(src: Path, sha: str) -> Set[str]:
    """Paths whose file in the source checkout isn't what `sha` has."""
    out = _git(src, "diff", "--name-only", "-z", "--no-renames", "--ignore-submodules=all",
               sha, "--")
    return {p for p in out.split("\0") if p}


def _filtered(dest: Path, sha: str, paths: List[str]) -> Set[str]:
    """Paths an attribute at `sha` makes git rewrite on checkout (LFS, eol, ...)."""
    if not paths:
        return set()
    out = _git(dest, "check-attr", "--source", sha, "-z", "--stdin", *FILTERS,
               stdin="\0".join(paths) + "\0").split("\0")
    hit = set()
    for i in range(0, len(out) - 2, 3):
        path, attr, value = out[i], out[i + 1], out[i + 2]
        if value not in ("unspecified", "unset") and not (attr == "text" and value == "auto"):
            hit.add(path)
    return hit


def _index(dest: Path) -> Path:
    p = Path(_git(dest, "rev-parse", "--git-path", "index").strip())
    return p if p.is_absolute() else dest / p


def _plain(dest: Path, sha: str) -> str:
    _git(dest, "reset", "-q", "--hard", sha)
    return "checkout"


def _can_clone(src: Path, dest: Path) -> bool:
    if not CLONE or sys.platform != "darwin":
        return False
    try:
        if os.stat(src).st_dev != os.stat(dest).st_dev:
            return False
    except OSError:
        return False
    conf = _git(src, "config", "--get-regexp", r"^core\.(autocrlf|eol)$", check=False).lower().split()
    return not {"true", "1", "yes", "on", "crlf"} & set(conf[1::2])


def fill(src: str | Path, dest: str | Path, sha: str) -> str:
    """Write the tracked files of `sha` into `dest` (a `worktree add --no-checkout`),
    cloning from the checkout `src` what is the same there. "cloned", "partly
    cloned (N checked out)" or "checkout"."""
    dest = Path(dest)
    sha = _git(dest, "rev-parse", "--verify", f"{sha}^{{commit}}").strip()
    top = _git(src, "rev-parse", "--show-toplevel", check=False).strip()
    if not top or not _can_clone(Path(top), dest):
        return _plain(dest, sha)
    src = Path(top)
    index = _index(dest)
    tmp = f"{index}.eki-fill"
    try:
        n = _fill(src, dest, sha, tmp)
    except Exception:
        n = None
    if n is None:
        Path(tmp).unlink(missing_ok=True)
        return _plain(dest, sha)
    os.replace(tmp, index)
    return "cloned" if n == 0 else f"partly cloned ({n} checked out)"


def _one(src: Path, dest: Path, path: str, mode: str) -> Optional[str]:
    """Clone one file; the path when it isn't a plain file in `src` (git writes it)."""
    source, target = os.path.join(src, path), os.path.join(dest, path)
    st = os.lstat(source)
    if not stat.S_ISREG(st.st_mode):
        return path
    _clone(source, target)
    want = (0o777 if mode == "100755" else 0o666) & ~_UMASK
    if stat.S_IMODE(st.st_mode) != want:
        os.chmod(target, want)
    return None


def _fill(src: Path, dest: Path, sha: str, tmp: str) -> Optional[int]:
    """Clone what can be, let git write the rest into a side index; the number
    of files checked out because they differ in `src`, or None: do a plain one."""
    entries = _entries(dest, sha)
    files = [p for p, m in entries.items() if m in ("100644", "100755")]
    differ = _differ(src, sha)
    by_git = differ | _filtered(dest, sha, files)
    by_git |= {p for p, m in entries.items() if m == "120000"}
    jobs, dirs = [], set()
    for path, mode in entries.items():
        if mode == "160000":                       # a submodule: an empty folder, as git leaves it
            (dest / path).mkdir(parents=True, exist_ok=True)
        elif path in by_git or mode not in ("100644", "100755"):
            by_git.add(path)
        else:
            jobs.append((path, mode))
            dirs.add(os.path.dirname(path))
    for d in sorted(dirs):
        (dest / d).mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        for path in pool.map(lambda job: _one(src, dest, *job), jobs):
            if path:
                by_git.add(path)
    Path(tmp).unlink(missing_ok=True)
    _git(dest, "read-tree", sha, index=tmp)
    wanted = sorted(p for p in by_git if entries.get(p) not in (None, "160000"))
    if wanted:
        _git(dest, "checkout-index", "-f", "-z", "--stdin", index=tmp,
             stdin="\0".join(wanted) + "\0")
    # status hashes what has no stat yet, and keeps that in the index it writes
    if _git(dest, "status", "--porcelain", "--ignore-submodules=all", index=tmp).strip():
        return None
    return len([p for p in wanted if p in differ and entries[p] != "120000"])
