"""Replay recorded work journals through step (docs/work-model.md): the
decisions eki really made, made again by the one copy of the rules.

Each tests/fixtures/work_journals/*.jsonl was rebuilt from eki's own history
by bin/export-work-journals (run by hand): a header with the goal's settings,
the events its executors fed, and the recorded outcome — each item's final
items.state and the runs started, in order. The replay feeds the events to
`step` under those settings and must reach the same labels and start the same
runs. A journal whose history predates today's rules (or a fixed bug) carries
`expected` in its header: why step now decides differently. The replay checks
that it still differs, so the note can't outlive the difference.
"""
import json
from pathlib import Path

import pytest

from eki.workmodel import Event, Settings, goal as _goal, label
from eki.workstep import Refused, step

HERE = Path(__file__).parent / "fixtures" / "work_journals"
JOURNALS = sorted(HERE.glob("*.jsonl"))


def read(path):
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    return lines[0], lines[1:-1], lines[-1]


def settings(head):
    s = head["settings"]
    return Settings(plan=s["plan"], gates=tuple(s["gates"]), merge=s["merge"], parallel=int(s["parallel"]),
                    locked=tuple(s["locked"]), kind=s["kind"])


def replay(path):
    """The final labels and the (item, role) of every start_run, as step decides them."""
    head, events, _ = read(path)
    s = settings(head)
    g, items, roles = _goal("planning"), (), []
    for e in events:
        data = dict(e["data"], item=e["item"]) if e["item"] else dict(e["data"])
        try:
            g, items, actions = step(g, items, s, Event(e["kind"], float(e["at"]), data))
        except Refused:
            continue                        # a person's word the stage didn't take: nothing changes
        roles += [[a.item, a.data["role"]] for a in actions if a.kind == "start_run"]
    return {x.id: label(x) for x in items}, roles


def by_item(roles):
    """The roles each item (None: the goal's own draft and plan) started, in order. Items run side
    by side, so only each one's own order is a decision."""
    out = {}
    for iid, role in roles:
        out.setdefault(iid, []).append(role)
    return out


def test_there_are_enough_journals():
    """Enough journals that must match, and between them the paths that matter."""
    sure = [p for p in JOURNALS if not read(p)[0].get("expected")]
    assert len(sure) >= 30
    kinds = {read(p)[0]["project_kind"] for p in sure}
    assert "eki" in kinds and kinds - {"eki"}, "eki and project goals both"
    finals = {v for p in sure for v in read(p)[2]["final"].values()}
    assert {"live", "applied", "unfit", "left", "dropped"} <= finals
    seen = {(e["kind"], k) for p in sure for e in read(p)[1] for k in e["data"]}
    assert ("item_finished", "calls") in seen, "a build waited on its calls"
    assert ("item_finished", "outcome") in seen, "a conflict was resolved"
    decided = {e["data"]["decision"] for p in sure for e in read(p)[1] if e["kind"] == "person_decided"}
    assert {"retry", "drop", "apply"} <= decided
    failed = {e["data"]["kind"] for p in sure for e in read(p)[1] if e["kind"] == "item_failed"}
    assert "environment" in failed, "a run that lost its sign-in"


def test_journals_hold_no_names():
    for p in JOURNALS:
        text = p.read_text()
        assert "/Users/" not in text and "title" not in text and "spec" not in text, p.name
        for e in read(p)[1]:
            assert e["item"] is None or e["item"].startswith("i")


@pytest.mark.parametrize("path", JOURNALS, ids=[p.stem for p in JOURNALS])
def test_step_makes_the_recorded_decisions(path):
    head, _, want = read(path)
    labels, roles = replay(path)
    if head.get("expected"):
        assert (labels, by_item(roles)) != (want["final"], by_item(want["roles"])), \
            f"{path.stem} now matches: drop its goal from KNOWN in bin/export-work-journals"
        return
    assert labels == want["final"]
    assert by_item(roles) == by_item(want["roles"])
