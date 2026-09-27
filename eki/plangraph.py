"""The shape of a plan: how wide and how deep its dependency graph is
(docs/self-build.md, "Items"). Stored in goals.shape when a plan concludes."""
from __future__ import annotations

from fnmatch import fnmatch
from typing import Any, Dict, List


def _overlap(a: List[str], b: List[str]) -> bool:
    """Whether two write-sets may touch the same file. An empty one overlaps everything."""
    if not a or not b:
        return True
    return any(x == y or fnmatch(x, y) or fnmatch(y, x) for x in a for y in b)


def shape(planned: List[Dict[str, Any]]) -> Dict[str, int]:
    """{"items", "depth", "width", "chained"} of a plan as `selfbrief.items_in` returns it:
    depth is the longest dependency chain, width the most items on one level, chained
    the deps between items whose write-sets are disjoint (a dep that may not be needed)."""
    by_title = {it["title"]: it for it in planned}
    deps = {it["title"]: [d for d in it.get("deps") or [] if d in by_title and d != it["title"]]
            for it in planned}
    level: Dict[str, int] = {}

    def level_of(t: str, seen: frozenset = frozenset()) -> int:
        if t in level:
            return level[t]
        if t in seen:                      # a cycle: count it once, don't loop
            return 0
        up = [level_of(d, seen | {t}) for d in deps[t]]
        level[t] = 1 + max(up, default=0)
        return level[t]

    for t in by_title:
        level_of(t)
    counts: Dict[int, int] = {}
    for n in level.values():
        counts[n] = counts.get(n, 0) + 1
    chained = sum(1 for t, ds in deps.items() for d in ds
                  if not _overlap(by_title[t].get("files") or [], by_title[d].get("files") or []))
    return {"items": len(planned), "depth": max(level.values(), default=0),
            "width": max(counts.values(), default=0), "chained": chained}


def describe(s: Dict[str, Any]) -> str:
    """"5 items · 2 deep · 4 wide"."""
    n = s.get("items", 0)
    return f"{n} item{'' if n == 1 else 's'} · {s.get('depth', 0)} deep · {s.get('width', 0)} wide"
