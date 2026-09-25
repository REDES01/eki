# SPDX-License-Identifier: Apache-2.0
"""Offering one of eki's changes to itself upstream, as a pull request.

What eki changes on this Mac is a local branch on top of the last release;
the public repo is something else — other people install what's merged
there, so merging and releasing stay with a person. The most eki does is
what a person asks for with `eki self offer <id>`: the change's own commits,
and only those, put on top of the public `main` (`self_upstream`, default
`origin/main`) on a branch `eki/<id>`, pushed (to `self_offer_remote` when
you work from a fork), and a pull request opened with `gh`. Nothing here
runs on its own — the loop never offers anything.

A change that leans on work only this Mac has doesn't go on top of the
public `main`; that's said, with the files, and nothing is pushed. Without
`gh` (or when it can't open the pull request) the branch is still pushed
and the page to open one by hand is given instead.

Offers are kept in ~/.eki/self/offers.json, so `eki self offer` again pushes
the same branch again and finds the pull request already open.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import selfwork
from .selfwork import SelfWorkError, git, _ok

#: states of a change worth offering: it passed its checks
OFFERABLE = ("proposed", "applying", "applied")

#: runs `gh` with these arguments in a folder: (returncode, stdout, stderr)
Gh = Callable[[List[str], str], Tuple[int, str, str]]


def _gh(args: List[str], cwd: str) -> Tuple[int, str, str]:
    if not shutil.which("gh"):
        return 127, "", "gh isn't installed (brew install gh)"
    got = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True)
    return got.returncode, got.stdout.strip(), got.stderr.strip()


def github_repo(url: str) -> str:
    """OWNER/REPO from a remote's URL, or "" when it isn't GitHub."""
    m = re.match(r"^(?:https?://|ssh://)?(?:[^@/]+@)?github\.com[:/]([^/]+)/(.+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _offers_path(home: Optional[Path]) -> Path:
    return (home or selfwork.HOME) / "offers.json"


def offers(home: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_offers_path(home).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _note(cid: str, got: Dict[str, Any], home: Optional[Path]) -> None:
    data = offers(home)
    data[cid] = got
    path = _offers_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def commits_of(c: Dict[str, Any]) -> List[str]:
    """The change's own commits, oldest first — not what this Mac had under it."""
    root, base = c["root"], c.get("base") or f"{c['commit']}^"
    out = git(root, "rev-list", "--reverse", "--no-merges", f"{base}..{c['commit']}")
    return out.split()


def body_of(c: Dict[str, Any]) -> str:
    """What the pull request says: the change in plain words, and how it was judged here."""
    out = []
    if c.get("summary"):
        out += [c["summary"].strip(), ""]
    request = (c.get("request") or "").strip()
    if request:
        out += ["What was asked:", "", *("> " + x for x in request.splitlines()[:40]), ""]
    checks = (c.get("report") or {}).get("checks") or []
    if checks:
        out.append("Checked on the Mac it was made on:")
        for k in checks:
            mark = "skipped" if k.get("skipped") else ("ok" if k.get("ok") else "FAILED")
            out.append(f"- {k.get('name')}: {mark} {k.get('detail') or ''}".rstrip())
        out.append("")
    if c.get("protected"):
        out += ["Touches what eki may not change alone: " + ", ".join(c["protected"]), ""]
    out.append(f"Made by eki working on itself (self/{c['id']}, by {c.get('backend') or 'an agent'}); "
               "offered by a person with `eki self offer`.")
    return "\n".join(out)


def _put_on_top(root: str, onto: str, commits: List[str], branch: str) -> List[str]:
    """`commits` picked onto `onto` as `branch`, in a scratch worktree so the
    checkout isn't touched. Returns the files that conflicted ([] = done)."""
    where = Path(tempfile.mkdtemp(prefix="eki-offer-")) / "wt"
    git(root, "worktree", "add", "-q", "--detach", str(where), onto)
    try:
        for sha in commits:
            if not _ok(where, "cherry-pick", "--allow-empty", "--keep-redundant-commits", sha):
                files = git(where, "diff", "--name-only", "--diff-filter=U").split()
                _ok(where, "cherry-pick", "--abort")
                return files or ["?"]
        git(root, "branch", "-f", branch, git(where, "rev-parse", "HEAD"))
        return []
    finally:
        _ok(root, "worktree", "remove", "--force", str(where))
        shutil.rmtree(where.parent, ignore_errors=True)


def offer(cid: str, *, upstream: str = "origin/main", push_to: str = "",
          home: Optional[Path] = None, gh: Gh = _gh) -> Dict[str, Any]:
    """Offer self/<cid> upstream: its commits on top of `upstream`, pushed
    as eki/<id>, and a pull request. Raises SelfWorkError when it can't."""
    c = selfwork.change(cid, home)
    if c["state"] not in OFFERABLE or not c.get("commit"):
        raise SelfWorkError(f"self/{c['id']} is {c['state']} — only a change that passed its checks "
                            "can be offered")
    remote, _, into = upstream.partition("/")
    if not remote or not into:
        raise SelfWorkError(f"self_upstream is {upstream!r} — it should be REMOTE/BRANCH, like origin/main")
    push_to = push_to or remote
    root = c["root"]
    git(root, "fetch", "-q", remote, into)
    onto = f"{remote}/{into}"
    commits = commits_of(c)
    if not commits:
        raise SelfWorkError(f"self/{c['id']} has no commits of its own to offer")
    if all(selfwork.is_in(root, sha, onto) for sha in commits):
        raise SelfWorkError(f"self/{c['id']} is already in {onto}")
    branch = f"eki/{c['id']}"
    clash = _put_on_top(root, onto, commits, branch)
    if clash:
        raise SelfWorkError(f"self/{c['id']} doesn't go on top of {onto} as it is — it leans on work "
                            f"only this Mac has, in {', '.join(clash)}. Nothing was pushed.")
    git(root, "push", "-q", "--force", push_to, f"refs/heads/{branch}:refs/heads/{branch}")
    # as configured, not as `insteadOf` rewrites it for fetching
    target = github_repo(git(root, "config", "--get", f"remote.{remote}.url"))
    head_repo = github_repo(git(root, "config", "--get", f"remote.{push_to}.url"))
    got: Dict[str, Any] = {"id": c["id"], "branch": branch, "onto": onto, "remote": push_to,
                           "commits": len(commits), "at": int(time.time()), "url": "", "opened": False}
    if target and head_repo:
        head = branch if head_repo == target else f"{head_repo.split('/')[0]}:{branch}"
        got["url"] = _pull_request(c, target, into, head, branch, root, gh, got)
        if not got["url"]:
            got["compare"] = f"https://github.com/{target}/compare/{into}...{head}?expand=1"
    else:
        got["why"] = "the upstream isn't on GitHub — open the pull request there by hand"
    _note(c["id"], got, home)
    return got


def _pull_request(c: Dict[str, Any], target: str, into: str, head: str, branch: str,
                  root: str, gh: Gh, got: Dict[str, Any]) -> str:
    """The pull request's URL — the one already open for this branch, or a new one."""
    code, out, _ = gh(["pr", "list", "--repo", target, "--head", branch, "--state", "open",
                       "--json", "url", "--jq", ".[0].url"], root)
    if code == 0 and out.startswith("http"):
        return out
    title = (c.get("title") or f"self/{c['id']}")[:100]
    code, out, err = gh(["pr", "create", "--repo", target, "--base", into, "--head", head,
                         "--title", title, "--body", body_of(c)], root)
    urls = re.findall(r"https://\S+/pull/\d+", out)
    if code == 0 and urls:
        got["opened"] = True
        return urls[-1]
    got["why"] = (err or out or "gh didn't say")[:300]
    return ""
