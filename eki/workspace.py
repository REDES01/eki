# SPDX-License-Identifier: Apache-2.0
"""A copy of the folder for each thread, so parallel runs don't collide
(ROADMAP, Stage 2).

Two agents sent into the same folder at once overwrite each other. So a run
that is given a git repo doesn't work in it: it works in its thread's own
`git worktree` of that repo, under ~/.eki/worktrees/, and its changes are
brought back when it ends.

    your folder ──sync──▶ the thread's worktree ──agent works──▶ changes
         ▲                                                          │
         └──────────── applied back (or kept as a branch) ◀─────────┘

- **One worktree per thread, not per run.** A Claude Code session belongs to
  the folder it runs in; a thread that moved every turn would lose its
  session. So the thread keeps its copy, and at the start of every run the
  copy is made to match your folder exactly as it is then — your
  uncommitted edits and new files included.
- **Bringing it back is explicit, and says what happened:**
  *applied* — the change is in your folder, uncommitted, as if the agent
  had worked there (commits the agent made are kept as commits when your
  folder hadn't moved); *conflicted* — it doesn't apply to your folder as
  it is now (another run, or you, changed the same lines), so it is left on
  a branch `eki/kept/<run>` for you to merge (the copy itself is detached
  and adds no branch of its own); *kept* — the run failed or was
  stopped, and what it had done is on such a branch rather than in your
  folder; *unchanged* — it changed nothing.
- **Dependencies aren't copied.** A fresh checkout has no `node_modules`
  or `.venv`; those (and `.env` files) are linked from your folder, since
  they're ignored by git and would otherwise have to be reinstalled.
- **A folder that isn't a git repo** (or has no commit yet, or uses
  submodules) can't be copied this way. Runs there take turns instead: a
  lock per folder, so the second waits for the first.

Setting `worktrees` (default on) switches this off: runs then work in the
folder itself, still taking turns.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path("~/.eki/worktrees").expanduser()
EKI_HOME = Path("~/.eki").expanduser()
#: ignored folders a checkout needs to run, linked from yours
DEPS = ("node_modules", ".venv", "venv")
#: ignored files that configure it, linked from yours
CONFIG = (".env", ".env.local", ".env.development", ".env.development.local")
#: new files bigger than this in all are not copied into the copy
UNTRACKED_LIMIT = 200 * 1024 * 1024
#: a worktree unused this long, with nothing left in it, is removed
IDLE_DAYS = 7
QUIET = ["-c", "core.hooksPath=/dev/null", "-c", "user.name=eki",
         "-c", "user.email=eki@localhost", "-c", "commit.gpgsign=false"]


class WorkspaceError(RuntimeError):
    pass


@dataclass
class Workspace:
    folder: str                     # what you gave
    mode: str                       # "worktree" | "lock"
    path: str = ""                  # where the agent works
    repo: str = ""                  # your repo's top level
    tree: str = ""                  # the worktree's top level
    branch: str = ""
    key: str = ""
    start: str = ""                 # the commit the run started from, in the copy
    base: str = ""                  # your HEAD when it started
    note: str = ""                  # anything worth saying about the sync

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def git(where: str, *args: str, input: Optional[bytes] = None, check: bool = True) -> str:
    got = subprocess.run(["git", "-C", where, *QUIET, *args], capture_output=True,
                         input=input, timeout=120)
    if check and got.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args[:2])}: "
                             f"{got.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return got.stdout.decode("utf-8", "replace").strip()


def git_bytes(where: str, *args: str) -> bytes:
    got = subprocess.run(["git", "-C", where, *QUIET, *args], capture_output=True, timeout=120)
    if got.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args[:2])}: "
                             f"{got.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return got.stdout


# ---- can this folder be copied? ---------------------------------------------------

def repo_of(folder: str) -> Optional[str]:
    """The repo top level if this folder can have a worktree, else None."""
    try:
        real = os.path.realpath(folder)
        if real == str(EKI_HOME.resolve()) or real.startswith(str(EKI_HOME.resolve()) + os.sep):
            return None                         # eki's own copies aren't copied again
        top = git(real, "rev-parse", "--show-toplevel")
        git(top, "rev-parse", "--verify", "-q", "HEAD")      # has a commit
    except (WorkspaceError, OSError, subprocess.SubprocessError):
        return None
    if (Path(top) / ".gitmodules").exists():
        return None
    return top


def _where(repo: str, key: str) -> Path:
    tag = hashlib.sha1(repo.encode()).hexdigest()[:8]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(repo).name)[:40] or "repo"
    return ROOT / f"{name}-{tag}" / key


# ---- opening ------------------------------------------------------------------------

def open(folder: str, key: str) -> Workspace:     # noqa: A001  (the verb is the point)
    """The thread's copy of `folder`, made to match it as it is right now.
    A folder that can't be copied gets a lock-mode workspace instead."""
    repo = repo_of(folder)
    if repo is None:
        return Workspace(folder=folder, mode="lock", path=folder)
    tree = _where(repo, key)
    # detached: the copy puts no branch of its own in your repo — the only
    # branches eki leaves are the eki/kept/<run> ones it tells you about
    branch = ""
    base = git(repo, "rev-parse", "HEAD")
    known = {line[len("worktree "):] for line in git(repo, "worktree", "list", "--porcelain").splitlines()
             if line.startswith("worktree ")}
    if tree.exists() and str(tree) not in known and str(tree.resolve()) not in known:
        shutil.rmtree(tree, ignore_errors=True)            # left over from a pruned worktree
    if not tree.exists():
        git(repo, "worktree", "prune")
        tree.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-q", "--detach", str(tree), base)
    else:
        git(str(tree), "checkout", "-q", "--detach", base)
        git(str(tree), "reset", "-q", "--hard", base)
        git(str(tree), "clean", "-fdq")
    note = _sync(repo, str(tree))
    _link_ignored(repo, folder, str(tree))
    if git(str(tree), "status", "--porcelain"):
        git(str(tree), "add", "-A")
        git(str(tree), "commit", "-q", "--no-verify", "-m",
            "eki: the folder as it was when this run started")
    rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(repo))
    path = str(tree) if rel == "." else str(tree / rel)
    Path(path).mkdir(parents=True, exist_ok=True)
    ws = Workspace(folder=folder, mode="worktree", path=path, repo=repo, tree=str(tree),
                   branch=branch, key=key, start=git(str(tree), "rev-parse", "HEAD"),
                   base=base, note=note)
    _touch(ws, "open")
    return ws


def _sync(repo: str, tree: str) -> str:
    """Your uncommitted edits and new files, into the copy."""
    patch = git_bytes(repo, "diff", "--binary", "HEAD")
    if patch.strip():
        git(tree, "apply", "--binary", "--whitespace=nowarn", "-", input=patch)
    names = [n for n in git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z")
             .decode("utf-8", "replace").split("\0") if n]
    total, skipped = 0, []
    for n in names:
        src, dst = Path(repo) / n, Path(tree) / n
        try:
            if src.is_symlink():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists() and not dst.is_symlink():
                    dst.symlink_to(os.readlink(src))
                continue
            size = src.stat().st_size
            if total + size > UNTRACKED_LIMIT:
                skipped.append(n)
                continue
            total += size
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError:
            skipped.append(n)
    if skipped:
        return f"{len(skipped)} new file(s) too big or unreadable to copy, e.g. {skipped[0]}"
    return ""


def _link_ignored(repo: str, folder: str, tree: str) -> None:
    """node_modules, .venv, .env… — ignored by git, needed to run: linked."""
    rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(repo))
    for base_rel in {".", rel}:
        src_base, dst_base = Path(repo) / base_rel, Path(tree) / base_rel
        for name in (*DEPS, *CONFIG):
            src, dst = src_base / name, dst_base / name
            if not src.exists() or dst.exists() or dst.is_symlink():
                continue
            ignored = subprocess.run(["git", "-C", repo, "check-ignore", "-q",
                                      os.path.join(base_rel, name)], capture_output=True)
            if ignored.returncode == 0:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src)


# ---- bringing it back -----------------------------------------------------------

def close(ws: Workspace, run: str, prompt: str = "", ok: bool = True) -> Dict[str, Any]:
    """Commit what the agent did in the copy and bring it back to your
    folder, or keep it on a branch. Returns what happened."""
    if ws.mode != "worktree":
        return {"state": "in place"}
    tree = ws.tree
    agent_head = git(tree, "rev-parse", "HEAD")
    git(tree, "add", "-A")
    if git(tree, "diff", "--cached", "--name-only"):
        first = " ".join((prompt or "").split())[:60]
        git(tree, "commit", "-q", "--no-verify", "-m",
            f"eki run {run}: {first}".rstrip(": "))
    end = git(tree, "rev-parse", "HEAD")
    if end == ws.start:
        _touch(ws, "unchanged")
        return {"state": "unchanged"}
    files = [f for f in git(tree, "diff", "--name-only", ws.start, end).splitlines() if f]
    commits = int(git(tree, "rev-list", "--count", f"{ws.start}..{agent_head}") or 0)
    out: Dict[str, Any] = {"files": files, "commits": commits}
    kept = f"eki/kept/{run}"
    if not ok:
        git(ws.repo, "branch", "-f", kept, end)
        _touch(ws, "kept")
        return {**out, "state": "kept", "branch": kept}
    repo = ws.repo
    head = git(repo, "rev-parse", "HEAD")
    clean = not git(repo, "status", "--porcelain")
    if commits and clean and head == ws.start:
        # your folder hadn't moved: the agent's commits become yours as they are
        git(repo, "merge", "--ff-only", "-q", agent_head)
        rest = git_bytes(repo, "diff", "--binary", agent_head, end)
        if rest.strip():
            git(repo, "apply", "--binary", "--whitespace=nowarn", "-", input=rest)
        _touch(ws, "applied")
        return {**out, "state": "applied", "kept_commits": True}
    patch = git_bytes(repo, "diff", "--binary", ws.start, end)
    fits = subprocess.run(["git", "-C", repo, "apply", "--check", "--binary", "-"],
                          input=patch, capture_output=True)
    if fits.returncode != 0:
        git(repo, "branch", "-f", kept, end)
        _touch(ws, "conflicted")
        why = fits.stderr.decode("utf-8", "replace").strip().splitlines()
        return {**out, "state": "conflicted", "branch": kept, "why": (why[0] if why else "")[:200]}
    git(repo, "apply", "--binary", "--whitespace=nowarn", "-", input=patch)
    if commits:
        git(repo, "branch", "-f", kept, agent_head)     # its commit messages, kept
        out["branch"] = kept
    _touch(ws, "applied")
    return {**out, "state": "applied"}


def home_paths(ws: Workspace, text: str) -> str:
    """An answer written in the copy talks about the copy's paths; the
    person's files are in their folder, so that's where it should point."""
    if ws.mode != "worktree" or not text:
        return text
    return text.replace(ws.path, ws.folder).replace(ws.tree, ws.repo)


def summary(ws: Workspace, result: Dict[str, Any]) -> str:
    """One line for the thread, saying where the run's changes went."""
    state, n = result.get("state"), len(result.get("files") or [])
    files = f"{n} file{'s' if n != 1 else ''}"
    where = ws.folder.replace(str(Path.home()), "~")
    if state == "applied":
        extra = (" (its commits too)" if result.get("kept_commits") else
                 f"; its commits are on `{result['branch']}`" if result.get("branch") else "")
        return f"eki: applied {files} to `{where}`{extra}."
    if state == "conflicted":
        return (f"eki: {files} didn't apply cleanly to `{where}` as it is now — kept on "
                f"`{result['branch']}` (`git merge {result['branch']}`).")
    if state == "kept":
        return f"eki: the run didn't finish; what it changed ({files}) is on `{result['branch']}`."
    return ""


# ---- housekeeping ------------------------------------------------------------------

def _state_file(tree: str) -> Path:
    return Path(tree).parent / f"{Path(tree).name}.json"


def _touch(ws: Workspace, last: str) -> None:
    try:
        _state_file(ws.tree).write_text(json.dumps(
            {"repo": ws.repo, "folder": ws.folder, "branch": ws.branch, "used": int(time.time()),
             "last": last}))
    except OSError:
        pass


def sweep(days: float = IDLE_DAYS, now: Optional[float] = None) -> List[str]:
    """Remove copies unused for `days` whose last run left nothing behind.
    Branches kept for you (`eki/kept/…`) are never removed."""
    now = now or time.time()
    removed = []
    if not ROOT.is_dir():
        return removed
    for state in ROOT.glob("*/*.json"):
        try:
            info = json.loads(state.read_text())
        except (OSError, ValueError):
            continue
        tree = state.with_suffix("")
        if now - float(info.get("used") or 0) < days * 86400:
            continue
        if info.get("last") == "open":
            continue                            # a run may still be in it
        repo = info.get("repo") or ""
        try:
            if tree.exists():
                git(repo, "worktree", "remove", "--force", str(tree))
            if info.get("branch"):          # copies made before they were detached
                git(repo, "branch", "-D", info["branch"], check=False)
        except (WorkspaceError, OSError):
            continue
        state.unlink(missing_ok=True)
        removed.append(str(tree))
    return removed


# ---- what the agent is told --------------------------------------------------------

def brief(ws: Workspace) -> str:
    """A line in front of the request: where it is, and what its folder is."""
    if ws.mode != "worktree":
        return ""
    home = str(Path.home())
    return (f"[eki: you are working in {ws.path.replace(home, '~')}, eki's copy of "
            f"{ws.folder.replace(home, '~')} for this thread. Make every change here; paths "
            f"the person gives inside {ws.folder.replace(home, '~')} mean the same files "
            f"here. eki brings your changes back when you finish.]\n\n")
