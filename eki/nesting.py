# SPDX-License-Identifier: Apache-2.0
"""How deep a request is: an agent asking eki asking an agent…

Every run has a depth. Yours is 0. The program that answers it is started
with `EKI_DEPTH` one more than that and `EKI_RUN` naming the run, so when it
calls `eki ask` (or eki's tools) its request arrives one level down and says
where it came from. The engine refuses a request at `MAX_DEPTH` — the chain
stops somewhere — and a nested run takes the budget of the run that made it,
so a goal's agent asking eki spends what the goal may spend and no more.

The depth travels with the run, not the engine's own environment: the engine
is one long-lived process, so the run being executed is kept in a context
variable (each run is its own task) and read when a program is started.
"""
from __future__ import annotations

import contextvars
import os
from typing import Dict, Optional, Tuple

VAR = "EKI_DEPTH"
RUN = "EKI_RUN"
#: requests at this depth are refused: yours, an agent's, an agent's agent's
MAX_DEPTH = int(os.environ.get("EKI_MAX_DEPTH", "3") or 3)

#: (depth, run id) of the run this task is executing
_current: contextvars.ContextVar[Tuple[int, str]] = contextvars.ContextVar("eki_run", default=(0, ""))


class TooDeep(Exception):
    """A request past MAX_DEPTH."""


def check(depth: int) -> None:
    if depth >= MAX_DEPTH:
        raise TooDeep(f"refused: nesting depth {depth} reached (EKI_MAX_DEPTH={MAX_DEPTH}) — "
                      "an agent asking eki asking an agent stops here; do this step yourself")


def enter(depth: int, run: str) -> None:
    """Called as a run starts executing, in its own task."""
    _current.set((max(0, int(depth or 0)), run or ""))


def current() -> Tuple[int, str]:
    return _current.get()


def child_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment for a program answering the current run: one level
    deeper, and naming the run so what it asks is tied back to it."""
    depth, run = _current.get()
    env = dict(os.environ if base is None else base)
    env[VAR] = str(depth + 1)
    if run:
        env[RUN] = run
    else:
        env.pop(RUN, None)
    return env


def caller() -> Tuple[int, str]:
    """(depth, parent run) of a process eki didn't start itself — `eki ask`
    or `eki mcp` run by an agent: read from its environment."""
    try:
        depth = int(os.environ.get(VAR, "0") or 0)
    except ValueError:
        depth = 0
    return max(0, depth), os.environ.get(RUN, "")
