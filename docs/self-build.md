# eki builds eki — design

eki's whole job is handing work to agents that change code. Its own source
is code like any other, so eki changes itself: it takes a goal, splits it
into small pieces, has agents build them side by side, judges each piece,
lands what passes, puts the new version live, and goes back if it's worse.
This file says how, and why this way. The eki before the rebuild did all of
this too (`~/eki-2026-09-26/docs/self-build.md`); what it learned is in
*What the first eki taught*, at the end.

## Three planes

**Control**: the engine (a manager with no state), the SQLite file (the
truth), and a small launcher eki can't change alone. **Work**: detached
workers, each running an agent or a command in a git worktree of its own.
**Integration**: git — an integration repo eki owns, a merge queue,
immutable builds, and the train that puts a build live.

Agents only ever touch the work plane: they produce commits, nothing else.
Integration is mechanical. Going live is mechanical and gated. This is a
merge queue plus a blue-green deploy with health-gated rollback, driven by
the reconcile loop the engine already is.

## Items: the unit of work

A goal — something you asked for, a roadmap item, a fault from the journal
— is not built directly. A **plan run** (an agent in a read-only worktree)
turns it into **items**: each with a title, a spec, a declared *write-set*
(the files it expects to touch, as globs), dependencies on other items, and
any interface two items share, written down before either starts ("engine
exposes `spawn(conn, rid)`; item B calls it").

Items are small on purpose — a few files, an hour of agent work at most.
Conflicts scale with change size times time in flight; the first eki's
median change touched seven files and a third of them needed an agent to
resolve conflicts.

The scheduler starts every ready item whose declared write-set is disjoint
from the items already running, in parallel, up to the room the machine and
the subscriptions give (`MAX_PARALLEL`, `machine.room`, `capacity`).
Overlapping items go one after another; an item marked `independent` never
waits. The write-set is a prediction, not a lock: at commit time eki records
what was touched against what was declared, and the drift feeds the
planner's brief.

Every stage of an item is an ordinary run, so the reap/resume machinery
covers all of it and nothing needs a resumption system of its own:

```
plan     agent run, read-only worktree          → items
build    agent run in worktree self/<id>         → commit on branch self/<id>
judge    command run: bin/check in the worktree  → verdict           (gate 1)
queue    rebase onto the predicted head; a resolve run only on conflict
judge    command run: bin/check on that head     → verdict           (gate 2)
land     fast-forward the integration ref         (serial, milliseconds)
train    build + swap, every few minutes          (serial, seconds)
```

`judge` and `resolve` need the **command** provider: a worker that runs an
argv in a folder and records its output as events, done / failed /
interrupted like any run, resumed by running again — a check is
idempotent. It's a job runner, not a harness.

## The queue: where it's parallel and where it isn't

Only two things are serial: the order changes land on the integration ref,
and the swap. Both take seconds. Everything else is a computation whose
inputs are (a change's commits, the base it goes on), and it starts the
moment those exist — not when the changes ahead have *landed*.

Say `main` is at M and A, B, C join the queue in that order, each green on
its own (gate 1). The queue predicts the future: A on M, B on M+A, C on
M+A+B. Those bases are known at once, because A's and B's content is final
the moment they're queued. The three rebases start together; when they're
clean (milliseconds) the three test runs — of M+A, M+A+B, M+A+B+C — start
together too. A minute later all three are green and landing is three
fast-forwards and a push. One judge's duration for three changes, not three.

A change that can't be rebased *mechanically* isn't in the queue. If C's
rebase onto M+A+B conflicts, C is pulled out of line; the changes behind it
get their predicted bases recomputed without it and go on. A **resolve run**
starts for C, in C's own worktree, mid-rebase against M+A+B, told what C is
for and what A and B did in the conflicted files. When it's done, eki
continues the rebase, refuses a result with markers left or not on top of
the head it resolved against, runs gate 1 on it again, and C rejoins at the
back — cleanly now, since it contains A and B. Many changes resolve at once,
each beside the queue; a conflict never holds anyone else up. `git rerere`
records every resolution so a change that must rebase twice doesn't ask
twice for the same hunk.

A change judged on a predicted head that included a change which then
failed has its result thrown away and is judged again. That's the price of
speculating, and it's paid only on failures.

When several queued changes conflict on the same files, pairing them up
before resolving (the first eki's tree merge, `~/eki-2026-09-26/eki/treemerge.py`)
saves resolver runs. It's an optimisation of the side path, added only if
the measured conflict rate calls for it.

## Judging

Three gates, each answering a different question; a fourth for later.

**Gate 1 — on its own.** In the change's worktree, on the commit it made,
before it may join the queue: `bin/check` — the tests, the file-size test,
the restart drill — run by a command worker. The agent was told to run the
tests itself; eki doesn't take its word. Red goes back to the change's
thread with the failure; one retry with the failure in the brief, then it's
left for you. Cheap, parallel, keeps junk out of the predicted futures.

**Gate 2 — in company.** On the predicted head, in a fresh detached
worktree: `bin/check` again, plus what unit tests can't cover (`eki/candidate.py`):
the engine boots in an empty home on port 0 and answers `/api/health`; it
opens a *copy* of the real `eki.db` (SQLite's backup API) and lists the same
threads; and the build running right now can still open that migrated copy
afterwards — otherwise rollback would be a lie. When `mac/` changed, the
Swift shell typechecks. Green: it may land once everything ahead has. Red:
it's dropped, the changes behind it get new heads and are judged again.

**Gate 3 — live.** After the swap, the launcher watches: the new engine has
to come up, tick and write a healthy marker within the window; if it dies
first, the launcher flips `current` back to `previous` and records why.
Nothing waits for this.

**Gate 4 — better, not just working** (later, with the journal): every
build records a score before and after — the local models' share of work,
faults, redos, corrections. A build that makes it worse is flagged in the
digest and offered for undo. The `builds` table has the columns from the
start.

None of these judge whether the change is *good code* beyond passing
tests. The brief covers part of it (add tests for what you change; end with
a plain-words `SUMMARY:`). A review run — a second agent reads the diff and
objects or doesn't — can sit between gate 1 and the queue once the loop
runs; it costs a turn per change.

## Replacing the running engine

The engine holds no state and workers are detached, so it can be replaced
at any moment: no waiting for runs to end. A worker keeps running the code
it was started from until its run ends; new runs get new code. Three things
make that safe:

- **Builds are immutable.** `~/.eki/builds/<sha>/` is a `git archive` of a
  landed commit; `current` and `previous` are symlinks. The engine and its
  workers run from `current`'s path as it was when they started. A build is
  removed a week after it stopped being current or previous, and never
  while a worker started from it is alive.
- **The schema only grows.** New tables and columns with defaults; no
  renames, no drops. An old worker writes to a DB the new engine migrated
  and neither notices. Gate 2 checks that the previous build still opens
  the migrated copy.
- **The launcher supervises.** launchd runs a small shell script, not the
  engine. It loops: run `current`'s engine; if it exits with the *swap*
  code (the engine flipped the symlink first), run `current` again; if it
  dies within the watch window before the engine wrote its healthy marker,
  flip `current` to `previous`, record the rollback, run again. No separate
  watchdog, no port probing.

The web UI is read from disk on every request, so a swap makes it live on
refresh. The Swift shell is rebuilt only when `mac/` changed.

The **train** goes every `self_release_minutes` (5): if the integration ref
is ahead of the running build and its head is green, build it, flip
`current`, and ask the engine to step aside. Every change landed since the
last train goes live together. `eki swap <ref>` does the same by hand;
`eki swap --back` goes to `previous`.

## The integration repo

eki lands into a repo it owns — `~/.eki/self/repo`, a clone with `origin`
at GitHub — never into `~/eki`. As each change lands eki pushes `main`, and
fast-forwards `~/eki` only when it's clean and on `main`; otherwise you
pull. Your checkout is one more git peer, so your own commits reach eki the
usual way: eki fetches before every rebase. Item worktrees are made from
this repo, under `~/.eki/self/<id>`.

## What eki may not change alone

Hard-locked — proposed, never queued, at any setting: the launcher,
`eki/launchd.py`, `bin/check` and `eki/drill.py` (the judge), the
credentials rule in `eki/providers/base.py` and `eki/quota.py`, `LICENSE`.
Everything else, the self-build code included, eki applies alone when every
gate passes. The lock stops eki, not you: `eki self apply <id> --yes` takes
the ordinary path.

## Agents don't share minds

Nothing carries over between models except text. A local model's KV cache
lives in its server process; Claude Code's session is its own transcript;
one model's thinking can't be given to another as its own. So shared state
between agents is the database (items, runs, events), git (branches, the
integration ref) and plain notes every harness can search. Coordination
happens before (the planner writes contracts and dependencies) and after
(the queue), never as a live conversation between agents.

## How far it goes alone

`self_autonomy` in `routing.json`: `propose` (a branch, a diff, its
verdict; you apply) or `apply` (a green change joins the queue). Faults and
the loop's own picks never go further than the setting allows; what you
ask for with `--apply` does. Work eki starts on its own carries `owner:
eki` and can't raise the setting, turn the loop on, or touch a locked file.

## Building it, in order

1. ~~**Groundwork**~~ — worktrees on demand (`eki/workspace.py`); runs in
   one folder take turns; the command provider (`eki/providers/command.py`).
2. ~~**Go-live**~~ — builds, the launcher, `eki swap`, `eki builds`; the
   swap drill. The locked files exist from here.
3. ~~**`eki self "…"`, propose**~~ — goals and items, the plan run, the
   scheduler by write-sets, the build run with its brief, gate 1, `eki
   self`. eki proposes changes to itself, in parallel, as branches
   `self/<id>` of the source repo: merge one, or `eki swap self/<id>`.
4. **The queue** — the integration repo, speculative rebase, gate 2, the
   side path with `rerere`, landing and pushing, the train, `apply`.
5. **The loop closes** — faults become items, the daily digest, the score.

## What the first eki taught

Read from `~/.eki-2026-09-26/self/` (156 judged changes, Sep 22–26):

- 58 applied, 3 rolled back, 4 unfit. Claude Code wrote 147.
- Median change: 7 files. `cli.py` was in 88 of 141, `engine.py` in 57.
  Lanes couldn't keep parallel changes apart because every change went
  through the same two files. A third of queued changes needed a resolver.
  The 400-line rule and small items attack the cause; the tree merge
  treated the symptom.
- Queued-to-live p90 was 38 minutes, max an hour: one swap at a time, a
  two-minute wait for runs that always timed out, a three-minute watch, and
  a four-minute candidate check (1013 tests). The rebuild's check is 34 s.
- The self-build machinery grew to ~10k of 42k lines, because every stage
  (steps, workers, pipeline) had a resumption system of its own beside the
  run machinery. Here every stage is a run.
- Both rollbacks on 2026-09-24 came from the supervisor being a separate
  process: a leftover engine held the port; a restart during the watch
  counted as unhealthy. The launcher is the parent, so neither can happen.
- A dropped item's Claude Code came back after every swap. A worker is
  only ever the run's; a cancelled run's worker is killed, never reattached.
