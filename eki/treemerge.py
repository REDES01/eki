# SPDX-License-Identifier: Apache-2.0
"""Changes that land together, merged in levels like a binary tree.

Until 2026-09-26 the merge queue took one finished change at a time: put it
on top of your checkout, resolve its conflicts, judge it again, land it —
and only then the next. Of 61 changes since 2026-09-24, 11 needed an agent
to resolve conflicts (21 resolver runs), almost all in the same five files
(cli.py was touched by 29 of them). N changes overlapping cost N resolves in
a row, each waiting for the one before, since B can't be put on A until A's
own conflicts are resolved.

Now every release train takes what's ready at once:

  1. Changes whose files overlap no other's go straight into the batch —
     git's three-way merge handles them.
  2. The rest are grouped by the files they share, and each group is merged
     in levels: paired up, each pair merged at the same time (up to
     `self_merge_workers`) — a clean three-way merge needs no agent; a real
     conflict gets one resolver run, told what both sides are for — then
     the results paired again, until one per group: log2(N) rounds, not N.
  3. The batch — your checkout plus every group's result — is judged once.
  4. If it fails, it is bisected: halves judged on their own, so only the
     change(s) that break it are dropped (back to their threads, with why)
     and the rest go live.

This module is the tree and the git under it; what an agent is asked, and
what a check is, the engine passes in (eki/selfengine.py `_self_tree`). The
tree as it stands is written to ~/.eki/self/tree.json for `eki self` and the
Self board: its groups, its pairs round by round, and which worker merges
what.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from . import selfwork

#: pairs merged at the same time, unless `self_merge_workers` says otherwise
WORKERS = 4

Node = Dict[str, Any]
#: an agent resolving a pair: (worktree mid-merge, one side, the other, conflicted files) -> how
Resolve = Callable[[Path, Node, Node, List[str]], Awaitable[str]]
#: a batch judged: commit -> (fit, why not)
Check = Callable[[str], Awaitable[Tuple[bool, str]]]


class MergeError(RuntimeError):
    pass


# ---- the plan: what goes straight in, what's merged in groups ------------------------

def leaf(cid: str, commit: str, files: List[str], title: str = "", what: str = "") -> Node:
    """One change, as the tree holds it. `what`: what it's for, for a resolver."""
    return {"ids": [cid], "commit": commit, "files": sorted(set(files)), "title": title[:120],
            "what": (what or title)[:600], "kids": None}


def plan(leaves: List[Node]) -> Tuple[List[Node], List[List[Node]]]:
    """(straight, groups): changes whose files no other touches, and the rest
    grouped so that two changes sharing a file — directly or through a third
    — are in the same group. Each keeps the order it came in."""
    owner: Dict[str, int] = {}
    parent = list(range(len(leaves)))

    def top(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for n, lf in enumerate(leaves):
        for f in lf["files"]:
            if f in owner:
                parent[top(n)] = top(owner[f])
            else:
                owner[f] = n
    sets: Dict[int, List[Node]] = {}
    for n, lf in enumerate(leaves):
        sets.setdefault(top(n), []).append(lf)
    straight = [g[0] for g in sets.values() if len(g) == 1]
    groups = [g for g in sets.values() if len(g) > 1]
    return straight, groups


def shared(group: List[Node]) -> List[str]:
    """The files that put a group together: touched by more than one of it."""
    seen: Dict[str, int] = {}
    for lf in group:
        for f in lf["files"]:
            seen[f] = seen.get(f, 0) + 1
    return sorted(f for f, n in seen.items() if n > 1)


def pairs(nodes: List[Any]) -> Tuple[List[Tuple[Any, Any]], Optional[Any]]:
    """One round: neighbours paired in order; an odd one out waits for the next round."""
    return [(nodes[i], nodes[i + 1]) for i in range(0, len(nodes) - 1, 2)], \
        (nodes[-1] if len(nodes) % 2 else None)


# ---- git: two commits made one ------------------------------------------------------

def _git(root: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    got = subprocess.run(["git", "-C", str(root), "-c", "user.name=eki", "-c", "user.email=eki@localhost",
                          *args], capture_output=True, text=True)
    if check and got.returncode != 0:
        raise MergeError(f"git {' '.join(args[:2])}: {(got.stderr or got.stdout).strip()[:300]}")
    return got


def _ancestor(root: Path, a: str, b: str) -> bool:
    return _git(root, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def merge_clean(root: Path, a: str, b: str, message: str) -> Tuple[str, List[str]]:
    """a and b made one without a worktree, when git can: (commit, []) — or
    ("", the conflicted files) when it takes someone to resolve them."""
    if _ancestor(root, b, a):
        return a, []
    if _ancestor(root, a, b):
        return b, []
    got = _git(root, "merge-tree", "--write-tree", "--name-only", "--no-messages", a, b, check=False)
    lines = [x for x in got.stdout.splitlines() if x.strip()]
    if got.returncode == 1 and lines:
        return "", sorted(set(lines[1:]))
    if got.returncode != 0 or not lines:
        raise MergeError(f"git merge-tree: {(got.stderr or got.stdout).strip()[:300]}")
    made = _git(root, "commit-tree", lines[0], "-p", a, "-p", b, "-m", message)
    return made.stdout.strip(), []


def open_merge(root: Path, a: str, b: str, where: Path) -> List[str]:
    """A worktree on `a`, stopped mid-merge with `b`: where a resolver works.
    The files still conflicting."""
    if where.exists():
        close_merge(root, where)
    where.parent.mkdir(parents=True, exist_ok=True)
    _git(root, "worktree", "add", "-q", "--detach", str(where), a)
    _git(where, "merge", "--no-ff", "--no-commit", "-q", b, check=False)
    return selfwork.unmerged(where)


def finish_merge(where: Path, files: List[str], message: str) -> str:
    """After the resolver: nothing left conflicting, no markers — committed.
    The commit; MergeError says what still isn't resolved."""
    still = selfwork.marked(where, sorted(set(files) | set(selfwork.touched(where))))
    if still:
        raise MergeError("conflict markers are still in " + ", ".join(still))
    _git(where, "add", "-A")
    left = selfwork.unmerged(where)
    if left:
        raise MergeError("still conflicting: " + ", ".join(left))
    _git(where, "commit", "-q", "--no-verify", "-m", message)
    return _git(where, "rev-parse", "HEAD").stdout.strip()


def open_at(root: Path, commit: str, where: Path) -> Path:
    """A worktree on `commit`, detached: where a batch is judged."""
    if where.exists():
        close_merge(root, where)
    where.parent.mkdir(parents=True, exist_ok=True)
    _git(root, "worktree", "add", "-q", "--detach", str(where), commit)
    return where


def files_between(root: Path, a: str, b: str) -> List[str]:
    return [f for f in _git(root, "diff", "--name-only", a, b).stdout.splitlines() if f]


def close_merge(root: Path, where: Path) -> None:
    _git(root, "worktree", "remove", "--force", str(where), check=False)
    _git(root, "worktree", "prune", check=False)


# ---- the tree ------------------------------------------------------------------------

def _path(home: Optional[Path]) -> Path:
    return (home or selfwork.HOME) / "tree.json"


def state(home: Optional[Path] = None) -> Dict[str, Any]:
    """The tree of the current (or last) train, for `eki self` and the board."""
    try:
        got = json.loads(_path(home).read_text())
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _names(ids: List[str]) -> str:
    return " + ".join(f"self/{i}" for i in ids)


class Tree:
    """One train's merge. `run()` does it all; the rest are its parts."""

    def __init__(self, root: Path, leaves: List[Node], *, base: str, resolve: Resolve, check: Check,
                 workers: int = WORKERS, home: Optional[Path] = None, train: str = ""):
        self.root, self.base, self.home = Path(root), base, home
        self.leaves, self.resolve, self.check = leaves, resolve, check
        self.workers = max(1, int(workers))
        self.slots = asyncio.Semaphore(self.workers)
        self.free = list(range(1, self.workers + 1))
        self.dropped: Dict[str, str] = {}
        self.straight, self.groups = plan(leaves)
        self.results: List[Node] = []
        # two commits already made one — kept across a restart, so a
        # resolve that finished isn't asked for again
        self.made: Dict[str, str] = dict(list((state(home).get("made") or {}).items())[-200:])
        self.view: Dict[str, Any] = {
            "train": train or time.strftime("%Y%m%d-%H%M%S"), "at": int(time.time()), "state": "merging",
            "base": base, "workers": self.workers,
            "straight": [{"id": lf["ids"][0], "title": lf["title"]} for lf in self.straight],
            "groups": [{"files": shared(g), "changes": [{"id": lf["ids"][0], "title": lf["title"]} for lf in g]}
                       for g in self.groups],
            "rounds": [], "checks": [], "landed": [], "dropped": {}, "made": self.made}
        self.save()

    def save(self) -> None:
        path = _path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.view["dropped"] = dict(self.dropped)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.view, indent=2, ensure_ascii=False))
        tmp.replace(path)

    # -- one merge: clean if git can, else one resolver run --

    async def merge(self, a: Node, b: Node, row: Dict[str, Any]) -> Node:
        """a and b made one, on a worker. A conflict goes to one resolver run
        told what both are for; if it can't be resolved, b's changes are
        dropped (with why) and a goes on alone."""
        async with self.slots:
            slot = self.free.pop(0)
            row.update(state="merging", worker=slot, started=int(time.time()))
            self.save()
            try:
                commit = await self._join(a, b, row)
            except (MergeError, selfwork.SelfWorkError) as e:
                commit = ""
                row.update(state="failed", why=str(e)[:300])
            finally:
                self.free.append(slot)
                self.free.sort()
                row["ended"] = int(time.time())
                self.save()
        if not commit:
            for cid in b["ids"]:
                self.dropped[cid] = f"it couldn't be merged with {_names(a['ids'])}: {row.get('why') or 'a conflict'}"
            self.save()
            return a
        return {"ids": a["ids"] + b["ids"], "commit": commit, "files": sorted(set(a["files"]) | set(b["files"])),
                "title": f"{a['title']} + {b['title']}"[:120], "what": "", "kids": (a, b),
                "whats": _whats(a) + _whats(b)}

    async def _join(self, a: Node, b: Node, row: Dict[str, Any]) -> str:
        key = f"{a['commit']}+{b['commit']}"
        if key in self.made:
            row.update(state="made before")
            return self.made[key]
        message = f"eki: merge {_names(a['ids'])} with {_names(b['ids'])}"
        commit, files = await asyncio.to_thread(merge_clean, self.root, a["commit"], b["commit"], message)
        if commit:
            row.update(state="clean")
        else:
            row.update(state="resolving", files=files)
            self.save()
            where = (self.home or selfwork.HOME) / "tree" / f"{a['commit'][:8]}-{b['commit'][:8]}"
            try:
                files = await asyncio.to_thread(open_merge, self.root, a["commit"], b["commit"], where) or files
                how = await self.resolve(where, a, b, files)
                commit = await asyncio.to_thread(finish_merge, where, files, message)
                row.update(state="resolved", how=str(how or "")[:300])
            finally:
                await asyncio.to_thread(close_merge, self.root, where)
        self.made[key] = commit
        return commit

    # -- the levels --

    async def levels(self) -> List[Node]:
        """Every group merged down to one result, round by round; all the
        pairs of a round — across groups — at once, up to `workers`."""
        tiers = [list(g) for g in self.groups]
        while any(len(t) > 1 for t in tiers):
            jobs, rows, byes = [], [], []
            for gi, nodes in enumerate(tiers):
                two, bye = pairs(nodes)
                byes.append(bye)
                for a, b in two:
                    row = {"group": gi, "a": a["ids"], "b": b["ids"], "state": "waiting"}
                    rows.append(row)
                    jobs.append((gi, a, b, row))
            self.view["rounds"].append(rows)
            self.save()
            got = await asyncio.gather(*(self.merge(a, b, row) for _, a, b, row in jobs))
            nxt: List[List[Node]] = [[] for _ in tiers]
            for (gi, _, _, _), node in zip(jobs, got):
                nxt[gi].append(node)
            for gi, nodes in enumerate(tiers):
                if len(nodes) == 1:
                    nxt[gi] = nodes
                elif byes[gi] is not None:
                    nxt[gi].append(byes[gi])
            tiers = nxt
        self.results = [t[0] for t in tiers]
        return self.results

    # -- the batch, and any part of it --

    async def _compose(self, node: Node, keep: Set[str]) -> Optional[Node]:
        """A group's result with only the changes in `keep`: what the tree
        already merged is used as it is; only the path down to a change
        left out is merged again."""
        ids = set(node["ids"])
        if ids <= keep:
            return node
        if not node.get("kids") or not ids & keep:
            return None
        a, b = (await self._compose(k, keep) for k in node["kids"])
        if a is None or b is None:
            return a or b
        row = {"group": -1, "a": a["ids"], "b": b["ids"], "state": "waiting"}
        self.view.setdefault("again", []).append(row)
        return await self.merge(a, b, row)

    async def batch(self, keep: Set[str]) -> str:
        """Your checkout, with every straight change in `keep` and every
        group's result for them on top — one commit."""
        units: List[Node] = [lf for lf in self.straight if lf["ids"][0] in keep]
        for g in self.results:
            part = await self._compose(g, keep)
            if part is not None:
                units.append(part)
        mine = {"ids": [], "commit": self.base, "files": [], "title": "your checkout",
                "what": "your checkout as it is now — what's current", "kids": None}
        for u in units:
            if not set(u["ids"]) - set(self.dropped):
                continue
            row = {"group": -1, "a": [], "b": u["ids"], "state": "waiting"}
            self.view.setdefault("again", []).append(row)
            got = await self.merge(mine, u, row)
            mine = {**mine, "commit": got["commit"]}
        return mine["commit"]

    async def judge(self, ids: List[str]) -> Tuple[bool, str]:
        keep = [i for i in ids if i not in self.dropped]
        commit = await self.batch(set(keep))
        row = {"ids": keep, "commit": commit, "state": "checking"}
        self.view["checks"].append(row)
        self.save()
        ok, why = await self.check(commit)
        row.update(state="passed" if ok else "failed", why=str(why or "")[:300])
        self.save()
        return ok, why

    async def bisect(self, ids: List[str]) -> List[str]:
        """What lands: the batch if it passes; else halves, each judged on
        top of the part already found good, so a change that only breaks in
        company is found too. Those that fail alone are dropped, with why."""
        good: List[str] = []

        async def walk(part: List[str]) -> None:
            part = [i for i in part if i not in self.dropped]
            if not part:
                return
            ok, why = await self.judge(good + part)
            if ok:
                good.extend(part)
                return
            if len(part) == 1:
                self.dropped[part[0]] = f"the batch failed its checks with it: {why}"[:400]
                return
            self.view["state"] = "bisecting"
            mid = len(part) // 2
            await walk(part[:mid])
            await walk(part[mid:])

        await walk(list(ids))
        return [i for i in good if i not in self.dropped]

    async def run(self) -> Dict[str, Any]:
        """The whole train: levels, the batch, its check, a bisect if it
        fails. {"batch": commit or "", "landed": [ids], "dropped": {id: why}}"""
        await self.levels()
        self.view["state"] = "checking"
        self.save()
        ids = [lf["ids"][0] for lf in self.leaves]
        landed = await self.bisect(ids)
        commit = await self.batch(set(landed)) if landed else ""
        self.view.update(state="merged", landed=landed, batch=commit)
        self.save()
        return {"batch": commit, "landed": landed, "dropped": dict(self.dropped)}


def _whats(n: Node) -> List[Dict[str, str]]:
    return n.get("whats") or [{"id": n["ids"][0] if n["ids"] else "", "what": n.get("what") or n.get("title") or ""}]


def pair_brief(a: Node, b: Node, files: List[str], python: str, where: Path | str) -> str:
    """What a resolver run is told: both sides, and what each is for."""
    def side(n: Node) -> str:
        return "\n".join(f"- {('self/' + w['id']) if w['id'] else 'your checkout'}: {w['what']}" for w in _whats(n))
    return (
        f"You are resolving a merge in eki's own source code, in {where}.\n\n"
        "eki lands finished changes together, merging them two at a time. This worktree holds "
        "one side and is stopped mid-merge with the other — conflicts in: "
        f"{', '.join(files)}.\n\n"
        f"One side (HEAD):\n{side(a)}\n\n"
        f"The other side (being merged in):\n{side(b)}\n\n"
        "How to work here:\n"
        "- Resolve every <<<<<<< / ======= / >>>>>>> block in those files so both sides' intent "
        "survives — each change is meant to land whole.\n"
        "- Touch only what the conflicts need. Leave no scratch files behind.\n"
        "- Don't run `git merge --continue`, `--abort`, commit, or touch branches — eki "
        "commits the merge itself once the files are clean.\n"
        f"- Run the tests with `{python} -m pytest -q` and leave them passing.\n"
        "- Don't ask questions — nobody is watching.\n"
        "- Finish with one line per file: how you resolved it.\n")
