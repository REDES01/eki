# Roadmap

The one plan for the rebuild. `docs/design.md` says what's built and the
invariants; this file says what comes next, in order. An item is a `- [ ]`
line; eki's own loop takes open items from here (docs/self-build.md) and
ticks them as they land. Items marked *(for a person)* are never taken.

## eki builds eki — `docs/self-build.md`

- [ ] **Groundwork.** A worktree per folder run (`eki/workspace.py`) so
      parallel runs on one repo never collide; the command provider
      (`eki/providers/command.py`) — an argv in a folder as a run.
- [ ] **Go-live.** Immutable builds under `~/.eki/builds/<sha>` with
      `current`/`previous`; the launcher launchd runs (loop, swap code,
      watch window, roll back); `eki swap`, `eki builds`; a drill case that
      swaps a sandboxed engine mid-run. *(for a person: creates the locked
      files)*
- [ ] **`eki self "…"`, propose.** The `items` table; the plan run that
      splits a goal into items with write-sets and dependencies; the
      scheduler runs disjoint items at once; the build run in a worktree
      with its brief; gate 1; `eki self` shows what's going.
- [ ] **The queue.** The integration repo; speculative rebase and gate 2;
      the side path for conflicts with `rerere`; landing and pushing the
      train every five minutes; `self_autonomy apply`.
- [ ] **The loop closes.** Faults from the journal become items; the daily
      digest; the score before and after every build (gate 4).
- [ ] **A review run** between gate 1 and the queue, once the loop runs.

## After

- [ ] Goals and the idle shift (background work while the Mac has room).
- [ ] Image generation as a provider (ComfyUI), routed like any other.
- [ ] Learned preferences: routing overrides learned from behaviour, with
      a notice and undo.
- [ ] The Swift shell rebuilt by eki when `mac/` changes.

## Not planned

- Asking two models the same thing to compare answers.
- Reading or reusing a CLI's login, in any form.
- A harness or tool of eki's own; a mesh backend; FERNme behind memory.
- Gemini CLI and other agent CLIs.
- A separate spending cap: budgets live in each provider's settings.
