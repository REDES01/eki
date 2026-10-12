"""`eki self show`'s checks: one line per check of a goal, and every verdict of one check."""
from __future__ import annotations

import time

from .. import acceptance, verdicts

FIELDS = ("evidence", "reasoning", "counter_evidence", "limitation", "suggestion")


def block(c, g) -> None:
    """The goal's `checks:` block (verdicts.lines), then how acceptance stands."""
    print("\nchecks:")
    for line in verdicts.lines(c, g["id"]):
        print(f"  {line}")
    if not g["accept"]:
        return
    acc = acceptance.state_of(g)
    said = f"acceptance: round {acc['round']}"
    if acc.get("by"):
        said += f", accepted by {acc['by']}"
    if acc["fixes"]:
        said += f", fix items {', '.join(acc['fixes'])}"
    print(said)


def show_check(c, ref: str) -> int:
    """`eki self show <goal>:<check>`: every verdict for that check, newest first, with all fields."""
    gref, cid = ref.rsplit(":", 1)
    g = c.execute("SELECT * FROM goals WHERE id=? OR id LIKE ? ORDER BY id=? DESC",
                  (gref, gref + "%", gref)).fetchone()
    if g is None:
        raise KeyError(f"no goal {gref}")
    check = c.execute("SELECT * FROM checks WHERE goal_id=? AND id=?", (g["id"], cid.lower())).fetchone()
    if check is None:
        raise KeyError(f"goal {g['id']} has no check {cid}")
    print(f"goal {g['id']}  check {check['id']}: {check['text']}")
    rows = verdicts.history(c, g["id"], cid)
    if not rows:
        print("\nnot judged yet")
    for v in rows:
        at = time.strftime("%Y-%m-%d %H:%M", time.localtime(v["created_at"]))
        print(f"\n{v['verdict']}  ({v['scope']} · {v['provider'] or '?'} · {v['model'] or '?'} · "
              f"round {v['round']} · {at} · run {v['run_id']}{' · item ' + v['item_id'] if v['item_id'] else ''})")
        for k in FIELDS:
            print(f"  {k}: {v[k] or '-'}")
    return 0


def accept(c, gid: str) -> int:
    """`eki self accept <goal>`: a goal waiting for your judgment, accepted by you."""
    g = c.execute("SELECT id FROM goals WHERE id=? OR id LIKE ? ORDER BY id=? DESC",
                  (gid, gid + "%", gid)).fetchone()
    if g is None:
        raise KeyError(f"no goal {gid}")
    print(f"goal {g['id']}: {acceptance.accept_by_you(c, g['id'])} by you")
    return 0
