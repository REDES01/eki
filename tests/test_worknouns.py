"""The work model stays small and pure (eki/workmodel.py, eki/workstep.py, eki/workstep_queue.py)."""
import ast
import dataclasses
from pathlib import Path

from eki import workmodel

EKI = Path(__file__).resolve().parent.parent / "eki"
PURE = ("workmodel.py", "workstep.py", "workstep_queue.py")
ALLOWED = {"__future__", "dataclasses", "typing", "enum"}
OURS = {"workmodel", "workstep", "workstep_queue"}
BANNED = {"time", "os", "subprocess", "sqlite3", "pathlib", "logging", "datetime", "random", "json"}


def test_the_nouns_are_few():
    assert len(workmodel.GOAL_PHASES) <= 3
    assert len(workmodel.ITEM_PHASES) <= 7
    assert len(workmodel.EVENT_KINDS) <= 12
    assert len(workmodel.ACTION_KINDS) <= 8
    assert len(dataclasses.fields(workmodel.Settings)) <= 6
    assert workmodel.FAILURE_KINDS == ("quota", "transient", "environment", "judgment", "lost")
    assert set(workmodel.ITEM_STAGES.values()) == set(workmodel.ITEM_PHASES)
    assert set(workmodel.GOAL_STAGES.values()) == set(workmodel.GOAL_PHASES)
    for kind in ("goal_submitted", "plan_ready", "item_started", "item_finished", "check_verdict",
                 "item_failed", "run_lost", "merge_result", "landed", "person_decided"):
        assert kind in workmodel.EVENT_KINDS
    for kind in ("start_run", "run_gate", "merge", "land", "notify", "hold_until", "ask_person"):
        assert kind in workmodel.ACTION_KINDS


def test_the_states_are_frozen():
    for cls in (workmodel.GoalState, workmodel.ItemState, workmodel.Settings, workmodel.Event, workmodel.Action):
        assert cls.__dataclass_params__.frozen
    names = {f.name for f in dataclasses.fields(workmodel.ItemState)}
    assert {"phase", "stage", "tries", "reviews", "fixer_used", "gate1", "gate2", "review", "hold_until",
            "why"} <= names


def test_the_model_and_the_step_import_nothing_that_touches_the_world():
    for name in PURE:
        tree = ast.parse((EKI / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad = [a.name for a in node.names if a.name.split(".")[0] not in ALLOWED]
                assert not bad, f"{name} imports {bad}"
            elif isinstance(node, ast.ImportFrom):
                if node.level:                      # relative: only each other
                    mods = [node.module] if node.module else [a.name for a in node.names]
                    assert set(mods) <= OURS, f"{name} imports .{mods}"
                else:
                    assert node.module.split(".")[0] in ALLOWED, f"{name} imports {node.module}"
            elif isinstance(node, ast.Name):
                assert node.id not in BANNED, f"{name} uses {node.id}"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("open", "print", "__import__", "exec", "eval"), \
                    f"{name} calls {node.func.id}"
