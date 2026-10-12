"""GitHub as the front of a project: every call eki makes to it, one function each.

eki reaches GitHub only through the official `gh` CLI, run as a subprocess,
and plain `git push` / `git fetch` against origin. It never reads a token
(`gh auth status` runs without --show-token) and makes no HTTP call itself.

A project is on the GitHub path when its configured origin is on github.com
and gh is installed and logged in (`path_of`); anything else keeps the local
path (a branch the person merges by hand). The answer is never cached. Pushes
go only to `eki/*` branches and are never forced. Fetch updates only
`refs/remotes/origin/<branch>`, never the project's own branches.

`due` keeps the interval between GitHub passes in memory, like
housekeep._prune: a restart only means an early look.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import workspace

#: marks what eki wrote on GitHub, so it never reads its own words as the person's
MARK = "<!-- eki -->"
TIMEOUT = 60
_last: Dict[str, float] = {}

_URL = re.compile(r"^(?:https?://(?:[^@/]+@)?github\.com/|ssh://(?:[^@/]+@)?github\.com(?::\d+)?/"
                  r"|(?:[^@/:]+@)?github\.com:)([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_PULL = re.compile(r"https://\S+/pull/\d+")
#: what a person types for a repository: OWNER/REPO, or a github.com URL with any path after it
_NAME = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9._-]+?)(?:\.git)?$")
_ANY_URL = re.compile(r"^(?:(?:https?|ssh|git)://(?:[^@/]+@)?(?:www\.)?github\.com(?::\d+)?/"
                      r"|(?:[^@/:]+@)?github\.com:|(?:www\.)?github\.com/)([^/\s?#]+)/([^/\s?#]+)(?:[/?#]\S*)?$")


class GhError(RuntimeError):
    """A gh or git call that failed; the message is the first line of its stderr."""


def _first(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def _gh(args: List[str], cwd: Optional[str | Path] = None) -> str:
    try:
        out = subprocess.run(["gh", *args], cwd=str(cwd) if cwd else None, capture_output=True,
                             text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise GhError("gh isn't installed") from None
    except subprocess.TimeoutExpired:
        raise GhError(f"gh {' '.join(args[:2])} timed out after {TIMEOUT}s") from None
    if out.returncode != 0:
        raise GhError(_first(out.stderr) or _first(out.stdout) or f"gh exited {out.returncode}")
    return out.stdout


def _git(where: str | Path, *args: str) -> None:
    out = subprocess.run(["git", *workspace.QUIET, "-C", str(where), *args], capture_output=True,
                         text=True, timeout=TIMEOUT)
    if out.returncode != 0:
        raise GhError(_first(out.stderr) or _first(out.stdout) or f"git {args[0]} failed")


# ---- which path ----------------------------------------------------------------------------

def parse(url: str) -> Optional[str]:
    """OWNER/REPO from a github.com remote URL, or None."""
    m = _URL.match((url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def repo_of(path: str | Path) -> Optional[str]:
    """The configured origin URL (not `remote get-url`), as OWNER/REPO when it is github.com."""
    return parse(workspace.git(path, "config", "--get", "remote.origin.url", check=False))


def available() -> Tuple[bool, str]:
    if shutil.which("gh") is None:
        return False, "gh isn't installed"
    try:
        _gh(["auth", "status"])
    except GhError as e:
        return False, "gh isn't installed" if str(e) == "gh isn't installed" else "gh is not logged in"
    return True, ""


def path_of(path: str | Path) -> Tuple[Optional[str], str]:
    """(OWNER/REPO, why) on the GitHub path, else (None, why). Checked fresh every time."""
    repo = repo_of(path)
    if repo is None:
        return None, "origin isn't on GitHub"
    ok, why = available()
    if not ok:
        return None, why
    return repo, f"GitHub {repo}"


def whoami() -> str:
    return _gh(["api", "user", "--jq", ".login"]).strip()


def named(text: str) -> Optional[str]:
    """OWNER/REPO from 'OWNER/REPO' or any github.com URL (https, ssh, git@, .git or not, a
    /tree/... or /pull/... after it ignored); None otherwise."""
    text = (text or "").strip()
    m = _ANY_URL.match(text)
    got = f"{m.group(1)}/{m.group(2)}" if m else text
    m = _NAME.match(got)
    return f"{m.group(1)}/{m.group(2)}" if m and m.group(2) not in (".", "..") else None


# ---- repositories --------------------------------------------------------------------------

def repo_list(limit: int = 100) -> List[str]:
    """The signed-in person's repositories, OWNER/REPO, in gh's order."""
    out = _gh(["repo", "list", "--limit", str(limit), "--json", "nameWithOwner"])
    got = json.loads(out) if out.strip() else []
    return [r["nameWithOwner"] for r in got if isinstance(r, dict) and r.get("nameWithOwner")]


def repo_create(name: str, folder: str | Path, private: bool = True) -> str:
    """Make the repository on GitHub with `folder` as its source and origin; pushed only when the
    folder has a commit. Returns OWNER/REPO as the folder's origin now says."""
    args = ["repo", "create", name, "--private" if private else "--public", "--source", str(folder),
            "--remote", "origin"]
    if workspace.git(folder, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False):
        args.append("--push")
    _gh(args)
    repo = repo_of(folder)
    if repo is None:
        raise GhError(f"gh repo create {name} left no GitHub origin in {folder}")
    return repo


def remote_add(folder: str | Path, repo: str) -> None:
    """`git remote add origin https://github.com/<repo>.git`."""
    _git(folder, "remote", "add", "origin", f"https://github.com/{repo}.git")


# ---- issues --------------------------------------------------------------------------------

def issues(repo: str) -> List[dict]:
    out = _gh(["issue", "list", "--repo", repo, "--label", "eki", "--state", "open", "--limit", "100",
               "--json", "number,title,body,labels,author"])
    return json.loads(out) if out.strip() else []


def search_issues(login: str) -> List[dict]:
    """Open issues labelled eki in every repo `login` owns; each carries repository.nameWithOwner."""
    out = _gh(["search", "issues", "--label", "eki", "--state", "open", "--owner", login, "--limit", "100",
               "--json", "number,title,body,labels,author,repository"])
    return json.loads(out) if out.strip() else []


def issue_state(repo: str, n: int) -> str:
    return _gh(["issue", "view", str(n), "--repo", repo, "--json", "state", "--jq", ".state"]).strip()


def contents(repo: str, path: str) -> List[dict]:
    """`gh api repos/<repo>/contents/<path>`: a folder's entries, or a file as one entry."""
    out = _gh(["api", f"repos/{repo}/contents/{path.strip('/')}"])
    got = json.loads(out) if out.strip() else []
    return got if isinstance(got, list) else [got]


# ---- pull requests -------------------------------------------------------------------------

def pr_find(repo: str, branch: str) -> Optional[dict]:
    out = _gh(["pr", "list", "--repo", repo, "--head", branch, "--state", "all",
               "--json", "url,number,state", "--limit", "1"])
    got = json.loads(out) if out.strip() else []
    return got[0] if got else None


def pr_create(repo: str, base: str, branch: str, title: str, body: str) -> str:
    out = _gh(["pr", "create", "--repo", repo, "--base", base, "--head", branch,
               "--title", title, "--body", body])
    urls = _PULL.findall(out)
    if not urls:
        raise GhError(f"gh pr create printed no pull URL: {_first(out)}")
    return urls[-1]


def pr_view(url: str) -> dict:
    out = _gh(["pr", "view", url, "--json", "state,mergedAt,comments,reviews"])
    return json.loads(out) if out.strip() else {}


def pr_comment(url: str, body: str) -> None:
    _gh(["pr", "comment", url, "--body", body])


def pr_close(url: str, comment: str) -> None:
    _gh(["pr", "close", url, "--comment", comment])


# ---- git against origin --------------------------------------------------------------------

def push(where: str | Path, src: str, branch: str) -> None:
    """`git push origin <src>:refs/heads/<branch>` — only eki/* branches, never forced."""
    if not branch.startswith("eki/"):
        raise ValueError(f"eki only pushes eki/* branches, not {branch}")
    if not src or src.startswith("+"):
        raise ValueError(f"eki never force-pushes: {src!r}")
    _git(where, "push", "origin", f"{src}:refs/heads/{branch}")


def fetch(where: str | Path, branch: str) -> None:
    """Update refs/remotes/origin/<branch> only; the project's own branches are left alone."""
    _git(where, "fetch", "origin", f"{branch}:refs/remotes/origin/{branch}")


# ---- the interval --------------------------------------------------------------------------

def due(name: str, now: Optional[float] = None) -> bool:
    """True (and remembered) when self.issues_minutes have passed since the last True for `name`."""
    from . import selfwork                              # selfwork imports projects, which may import us
    t = time.time() if now is None else now
    every = float(selfwork.settings().get("issues_minutes") or 0) * 60
    last = _last.get(name)
    if last is not None and t - last < every:
        return False
    _last[name] = t
    return True


def soon(name: str) -> None:
    """Forget when `name` last ran, so the next `due(name)` is True."""
    _last.pop(name, None)
