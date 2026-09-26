"""The builds on this Mac: which runs, which it would go back to, how the last swap went."""
from __future__ import annotations

import time
from pathlib import Path

from .. import builds, score, traincheck
from .common import ago, conn

NAME = "builds"
HELP = "list eki's builds: current, previous, and how the last swap went"


def add(p) -> None:
    pass


def run(args) -> int:
    st = builds.status()
    cur, prev = st.get("current"), st.get("previous")
    print(f"running: {builds.running_id()} from {builds.running()}")
    verdicts = _verdicts()
    for b in st["builds"]:
        mark = "→" if b["path"] == cur else ("↩" if b["path"] == prev else " ")
        print(f"{mark} {b['id']:<14} {b.get('commit', '')[:12]:<12} {ago(b.get('made_at')):>8}"
              f"  {'healthy' if b.get('healthy') else '-':<8} {traincheck.label(Path(b['path'])):<9} {verdicts.get(b['id'], '-'):<9} {b['path']}")
    if cur and all(b["path"] != cur for b in st["builds"]):
        print(f"→ dev            checkout                                 {cur}")
    sw, rb = st.get("swap"), st.get("rollback")
    if sw:
        print(f"last swap: {sw['state']} → {sw['target']} ({ago(sw.get('at'))}; {sw.get('why', '')})")
    if rb and (not sw or rb.get("at", 0) >= sw.get("at", 0)):
        print(f"rolled back {time.strftime('%a %H:%M', time.localtime(rb['at']))}: "
              f"{rb['from']} exited {rb['exit']} after {rb['after']}s → {rb['to']}")
    for bid, v in verdicts.items():
        if v == "worse":
            print(f"build {bid} made things worse — eki self undo {bid}")
    return 0


def _verdicts() -> dict:
    """Gate 4 per build: better/same/worse, 'measuring' until judged."""
    try:
        c = conn()
        rows = c.execute("SELECT build, verdict FROM build_scores ORDER BY healthy_at").fetchall()
    except Exception:
        return {}
    return {r["build"]: r["verdict"] or "measuring" for r in rows}
