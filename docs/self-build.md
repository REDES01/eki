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

**The draft.** A wish from `eki self "…"` first becomes a goal. A **draft
run** — an agent in a read-only worktree `draft-<goal>` — reads
`docs/design.md`, this file, `ROADMAP.md`, the last digest, a week of faults
and corrections (`eki observe --since 7d --kind fault,correction`), the code
the wish touches, and, when `~/eki-2026-09-26` exists, the old eki's version
of the same thing as reference. Where the wish leaves a real choice open it
asks you — at most three questions, 2–4 options each, through
AskUserQuestion; they arrive as cards in eki's window and in the CLI's
`eki answer`, like a build run's questions — and folds the answers in. It
changes no file and ends with `GOAL:` and the goal text, then
`DRAFT: done` or `DRAFT: person <why>`. The goal is stored (`goals.wish` is
what you typed, `goals.text` the goal as drafted, `goals.drafted_at` when)
and planned on the same thread, draft → plan. No `GOAL:` block, or
`DRAFT: person`, leaves the goal `left` with the reason; a failed draft run
fails the goal. A plan or draft run that fails on a passing error (a dropped
connection, overload) is retried by the worker up to three times; after that
the goal fails, and `eki self retry <goal id>` re-runs its plan (or its draft,
when the draft never produced a goal). `--as-is` (the text is already the goal), `--one`, faults and
other eki-owned goals skip the draft. Like the plan stage, it resumes from
the goals table alone.

Draft and plan runs are the thinking, so they go to the strongest model:
`routing.json` `"self": {"planner": {"provider": "claude", "model": "opus"}}`.
The provider defaults to the `code` row's first target, the model to the
provider's default. Both runs are pinned to that provider and carry the
model on the run (`--model` for Claude Code, `-m` for Codex); build, judge
and resolve runs are unchanged.

A goal has four parts, and a planner can split it without guessing:

- **What must be true when done** — the outcomes, numbered, each one
  checkable: behaviour, states, names, commands.
- **Read first** — the docs, modules and functions (real paths) the work
  starts from.
- **Item shape and shared files** — how it splits into items, which module
  holds what, and the files more than one item touches.
- **Tests** — what the tests prove, with which fixtures (the fake provider,
  temp homes), and that `bin/check` stays green.

Items are small on purpose — a few files, an hour of agent work at most.
Conflicts scale with change size times time in flight; the first eki's
median change touched seven files and a third of them needed an agent to
resolve conflicts.

**How a plan is shaped.** Plans are wide, not long (`selfbrief.SPLIT_RULES`,
shared by the self and project plan briefs; the draft's item-shape guidance
says the same). Items that don't depend on each other are preferred; a dep
is declared only when the later item's tests can't pass without the earlier
item's code. A shared interface — the exact function signature, table and
column, JSON key — is written into the spec of every item that uses it, so
each can build without waiting to see the other. When several items need the
same new names, one small root item adds them, with working minimal bodies
and tests, and the rest depend only on it: two levels deep unless the goal
truly can't be. Two items share a file only if one depends on the other.
When a plan concludes, `plangraph.shape` measures it — depth is the longest
dependency chain, width the most items on one level, `chained` the deps
between disjoint write-sets — and stores it in `goals.shape`; the `eki self`
board shows it on the goal line as "5 items · 2 deep · 4 wide".

**The scheduler** (`eki/scheduler.py`) starts ready items across all goals
at once. Waiting items are taken goal by goal, round-robin: the goal with the
fewest items building goes first, then the older goal, then the older item.
An item starts when its write-set is disjoint from the items already running
*in the same repo* — items of different projects never block each other —
up to the room the machine and the subscriptions give (`machine.room`,
`capacity`). `self.parallel` (4) counts building items only; judging is
limited by `self.check_slots`. Overlapping items go one after another; an
item marked `independent` never waits. An item that stays waiting says why
in `items.why`, written only when it changes and cleared when it starts —
"after <dep title>", "shares eki/engine.py with <item id>", "no write-set:
waits until its repo is quiet", "4 building (self.parallel)" — and the board
shows it. The write-set is a prediction, not a lock: at commit time eki
records what was touched against what was declared, and the drift feeds the
planner's brief.

Every stage of an item is an ordinary run, so the reap/resume machinery
covers all of it and nothing needs a resumption system of its own:

```
draft    agent run, read-only worktree          → goal
plan     agent run, read-only worktree          → items
build    agent run in worktree self/<id>         → commit on branch self/<id>
judge    command run: bin/check in the worktree  → verdict           (gate 1)
queue    rebase onto the predicted head; a resolve run only on conflict
judge    command run: fast bin/check on that head → verdict          (gate 2)
land     fast-forward the integration ref         (serial, milliseconds)
train    build, full check + candidate, swap  (serial; gate 3)
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
clean (milliseconds) the three test runs — the fast tests of M+A, M+A+B,
M+A+B+C; the drills and the candidate checks wait for the train — start
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
Judge runs share a few check slots.

**Gate 1 — on its own.** In the change's worktree, on the commit it made,
before it may join the queue: `bin/check` — the tests, the file-size test,
the restart drill — run by a command worker. The agent was told to run the
tests itself; eki doesn't take its word. Red goes back to the change's
thread with the failure; one retry with the failure in the brief, then it's
left for you. Cheap, parallel, keeps junk out of the predicted futures. A
**docs-only** item — every touched file is `*.md` (any folder) or under
`docs/` — runs no tests: its judge is `python -m eki.doccheck <base>..<sha>`
(`eki/doccheck.py`), which checks that every changed file is UTF-8 text and
that nothing outside the lane changed. Its board line says "docs only".

**Gate 2 — in company.** On the predicted head, in a fresh detached
worktree: the fast tests only, `EKI_CHECK_FAST=1 bin/check` — the suite
minus the tests marked `drill` (the ones that start a real engine or
launcher). A docs-only item gets the doccheck again instead. Green: it may
land once everything ahead has. Red: it's dropped, the changes behind it get
new heads and are judged again.

**Gate 3 — before the swap, then live.** The train (`eki/train.py`,
`eki/traincheck.py`) runs one command run on the very build it is about to
ship, before `current` moves: the full `bin/check`, drills included, plus
what unit tests can't cover (`python -m eki.candidate`): the engine boots in
an empty home on port 0 and answers `/api/health`; it opens a *copy* of the
real `eki.db` (SQLite's backup API) and lists the same threads; and the
build running right now can still open that migrated copy afterwards —
otherwise rollback would be a lie. The build has no `.venv`, so the run's
`EKI_PYTHON` is the source checkout's. Green: swap as before. Red: no swap;
the newest item the build carries is reverted on integration main as a new
commit (`git revert --no-edit`; main was pushed, so no reset), pushed, and
marked unfit with the check's tail in its thread; the next tick builds the
new main and tries again with the items left, until green or none are. A
train that carries only docs-only items skips this check. The result lives
beside the build's `.eki-build.json` as `.checked` (running, green or red),
so a restart mid-check finds the run or starts it again; `eki builds` shows
it per build. After the swap the launcher watches: the new engine has to
come up, tick and write a healthy marker within the window; if it dies
first, the launcher flips `current` back to `previous` and records why.
Nothing waits for the watch.

**Check slots.** At most `self.check_slots` (`routing.json`, default 2)
judge runs — gate 1, gate 2 and the train's check together — run at once;
the rest stay queued, held by the engine's scheduling (`eki/checkslots.py`),
not the queue's, and the board says "waiting for a check slot". `bin/check`
runs the suite in parallel (`-n auto`), so one check already fills the
cores: two fast checks beat five crawling ones.

**Gate 4 — better, not just working** (built; see the next section): every
build that goes live gets a score before and after, from the journal — the
local models' share of work, faults, corrections and redos, handoffs. A
build that made things worse is flagged in `eki builds` and the digest and
offered for undo. It is a trailing judgment, never a gate: nothing waits
for it and nothing is undone by itself.

**The review — a second reader** (`eki/review.py`). The gates judge
whether a change works, not whether it does what it was asked. When gate 1
is green the item goes to `reviewing`, not `proposed`, and a `review` chore
(docs/design.md, "Chores") asks the local model — at `now` priority for
your goals, `background` for eki's. It is given the goal (first 2000
characters), the item's title, spec and `SUMMARY:`, and the diff
`<base>..<commit>` from the integration repo (40 000 characters at most, with
a note of what was cut), and asked whether the diff does what the spec and
summary say, without obvious bugs, missing tests or stray changes. Its last
`REVIEW:` line decides:

- `REVIEW: ok` → `proposed`, review `ok`.
- `REVIEW: no <why>`, the first time → back to `waiting` for one rebuild,
  with "A second reader objected: … Answer it in the change, or say in
  SUMMARY why it's wrong" in the build brief. The rebuild doesn't cost gate
  1's one retry (`tries - reviews` is what's compared).
- `REVIEW: no` a second time → `proposed` with the objection kept; the queue
  never takes it by itself, even under `apply`. The long digest page lists it
  under **Waits for you** with the objection; `eki self apply <id>` is the
  person's call.
- A review that can't judge — no local model, `self.review: false`, a failed
  or cancelled run, an answer without the line — → `proposed` with
  `none: <why>`. The reviewer never holds an item it couldn't judge.

Docs-only items are reviewed too. The board shows `reviewing` with the
chore's run, and the verdict under a proposed item. Like every stage it
resumes from the item and its runs: a restart mid-review loses nothing.

## The journal, the score and the digest

**The journal** (`eki/observe.py`, table `journal`) is what eki writes down
about itself, as it happens, from where it happens:

- a **fault** — a traceback caught by the worker, the engine's tick, the
  selfwork, queue and train ticks, a housekeeping step or a server request
  handler; with where it was caught, the run, the innermost frame in eki's
  own code (`eki/x.py:12`) and the exception type;
- a **handoff** — a run ended handed off: the provider and its reason;
- a **correction** — a new run in a chat thread within 10 minutes of the one
  before whose prompt starts with "no", "not", "wrong", "actually",
  "instead" or "I meant", or is a redo (90% the same text). Self-work
  threads, command runs and sub-runs don't count;
- a **limit** — a provider hit its limit;
- a **run** row for every run that ended — provider, state, seconds, whether
  a local model ran it, whether it handed off, seconds to the first text —
  so the score is computed from the journal alone;
- a **regression** — a build judged worse (below).

Tracebacks keep their last 4000 characters; everything written is scrubbed
of what looks like a secret (keys, tokens, `password=…`). Writing never
raises: the journal must not break what it watches. Rows are kept 90 days
(the housekeeping pass prunes at most once an hour). `eki observe [--since
24h] [--kind fault|handoff|correction|limit|run|regression] [--full]` lists
it; `--full` prints a fault's traceback.

**Housekeeping** (`eki/housekeep.py`) runs on the engine's duty pass: prune,
settle the scores, turn faults into items, write the digest when due. Each
step runs on its own; one that raises is written down as a fault.

**Faults become items** (`eki/faults.py`). A fault in eki's own code seen
twice within 24 hours — same file:line, same exception type — opens a goal
(source `fault`, owner `eki`) with one item, "fix fault eki/x.py:12
KeyError": the latest traceback and the request of the run it happened in
are the spec, and the write-set is the file plus `tests/test_<name>.py`. At
most `self.fault_items_per_day` (3) such goals in 24 hours; a fault whose
item is still open, or landed in the last 7 days, isn't opened again. Owner
`eki` builds at background priority and, under `propose`, is only
proposed. Faults outside eki's code (a rebase conflict, say) are written
down, never made items.

**A fault's first brief.** When a fault opens its goal, a `brief` chore
(background) gives the local model the template spec, the traceback and the
fault's file ±60 lines around the frame, and asks for `BRIEF:` and a spec:
the likely cause, where to look, what the reproducing test should do. The
item isn't started while its goal has an open brief younger than
`self.brief_wait` minutes (10). A done brief becomes the item's spec,
followed by the template's traceback and request sections verbatim;
anything else — a failed chore, no local model, the wait run out — and the
template stands.

**The score** (`eki/score.py`) of a window of time, from its `run` rows:

- `local_share` — runs a local model finished without handing off, of all
  finished (done or handed off) runs;
- `fault_rate`, `correction_rate`, `handoff_rate` — per 100 runs;
- `median_first_text` — median seconds from a run's creation to its first
  text.

When the train sees a build go healthy it writes `before` into
`build_scores`: the score from the previous build's healthy time to this
one's, or, if that window has fewer than 30 runs, the last 30 runs before
it. `after` — the window since — is recomputed on every housekeeping pass
until it holds 30 runs; then it is frozen with a verdict. **Worse**: the
fault or correction rate rose by more than 50% of itself and by at least 2
per 100 runs, or `local_share` fell by more than 10 points. **Better**: the
same the other way. Otherwise **same**. A failure to score is logged and
never holds a build.

**What 'worse' does**: a `regression` row in the journal, `worse` in the
verdict column of `eki builds` (`measuring` until frozen), a line in the
digest, and the offer of `eki self undo <build>` — a goal "undo build X"
with one revert item for each item the build carried, newest first, each
after the one before, going the ordinary path (gates, queue, train).
Nothing about it is automatic, and it never blocks landing.

**The digest** (`eki/digest.py`, `eki/patchnotes.py`). Once a day after
`self.digest_at` (default `09:00`, local time) the housekeeping pass writes
`EKI_HOME/self/digests/YYYY-MM-DD.md`: a short page in the style of a game's
patch notes, readable in twenty seconds. It has `# eki MM-DD` (the local
date of the window's end), a headline of at most 20 words, a section for
each area that changed — in the order Self-build, Web UI, Models, Routing,
Pictures, Engine, Docs, each a bare header with `- ` lines of at most 14
words, at most 5 a section, 15 a page and 1 for Docs — and a closing line
eki writes itself, never the model: `Waits for you: N.  Score: <verdict>.`
(`nothing` when N is zero). A day with one change is a page with one line.

What counts as a change: an item on eki itself (not on a person's project)
that became landed, live or applied in the window. Its area comes from the
files it touched (`items.touched`, else `items.files`), `tests/` left out,
by the path table in `patchnotes.area_of` (docs and top-level `*.md` → Docs,
`eki/routing/` → Routing, `eki/web/` and the Station and Settings APIs → Web
UI, ComfyUI and the gallery → Pictures, `eki/providers/` and `models` →
Models, the self-build modules → Self-build, anything else → Engine); the
area most of its paths map to wins, ties going to the earlier area. An item
that only touched tests is left off; one that only touched docs gives at
most the one Docs line. The verdict is `worse: build <sha7> judged worse,
see --long` when a build was judged worse; otherwise the last 24 hours
against the 24 before: `better than yesterday`, `same as yesterday`, or
`worse: more faults | more corrections | less done locally`. "Waits for
you" counts what waits now: locked and proposed items, and drafting goals
with an open ask.

`YYYY-MM-DD.long.md` beside it (`# eki digest — YYYY-MM-DD (long)`) is the
old per-item page over the same window: every item whose state changed, one
line each with its id — **Landed and live**, **Went wrong** (with why),
**Waits for you** (with the `eki self apply` to run), **Still moving** —
then **Picked by eki**, the score table with "not counted", the faults and
corrections, the builds judged worse, each with its `eki self undo`, and the
model's `## Triage`. Items on a person's project and items that went wrong
appear only here. `digest.write` always writes the rules-only short page
first, then the long page, then `.last`, each atomically, so the page is
never missing. `eki self digest` prints the latest short page (writing one
if there is none); `eki self digest now` writes a fresh one; `--long` on
either prints the long page (a page from before the long one existed prints
itself). The web UI's sidebar links the latest page; the Station's Digest
heading shows its headline and links the long page.

**The digest's notes and triage** (`eki/digestprose.py`). The rules-only
page gives each changed area one line — the titles of its changes joined
with `; `, cut to 14 words — under the headline `N changes landed: <areas>.`
(`Nothing new landed.` and no sections on a quiet day). On the daily
housekeeping write only, a `digest` chore then gives the local model the
rules above and the example page, the changes one per line as `[Area]
(kind) title` with no ids, and the journal's faults, corrections, handoffs
and limits over the window (scrubbed, 20 000 characters at most). It answers
`NOTES:` — the headline, then the section headers and `- ` lines — and
`TRIAGE:`, one line per thing worth a look: `- <what> — fix | watch |
ignore — <why>`, a `fix` naming the `eki self "…"` wish to run. Notes that
pass `patchnotes.check` replace the page between its title and closing
line. Otherwise the rules-only page stands and the chore closes `failed`
with the reason: a headline or line too long, too many lines, an area not
on the page, an id, a file name, or the words item, goal, gate or worktree.
Triage goes on the long page before `## Score`, once, either way. A failed
chore or no local model leaves both pages byte-identical. `eki self digest
now` and the Station's Write now give the rules-only page. Triage only
suggests: `faults.py` stays the only thing that opens items.

**What the score counts.** Only what you asked for: runs of scope `chat`.
Self-work (command runs, goal, item and chore threads) and picture runs are
left out of every rate and share, and so are faults of those runs; the
long digest page says "not counted: n self-work, m picture runs" under the table
(docs/design.md, "The score's scope").

**Settings for the local model's work** (`routing.json` `self`): `local` —
the provider chores go to (default: the first of kind `local`; `"off"`
means none, and each chore is `skipped`); `review` — `false` turns the
second reader off, items go straight on with `none: self.review is off`
(default `true`); `brief_wait` — minutes a fault item waits for its first
brief (default 10).

**Sync on a moved origin.** When integration main and `origin/main` have
both moved (someone pushed in between), `integration.sync` rebases main onto
`origin/main` in a worktree of its own, so the repo is never mid-rebase;
main moves only if the rebase went through and main didn't move meanwhile,
and landed items whose commits were rewritten follow them. A conflict aborts
the rebase, leaves main as it was, and is written to the journal as a fault.

## The loop: eki picks its own work

When nothing is queued, eki can pick its next piece of self-work
(`eki/selfpick.py`), and every pick says why. It is **off** until you turn
it on.

**The switch.** `routing.json` `self.loop`, default `false`. `eki self loop
on|off` sets it and prints the new state; nothing else writes it — not the
picker, faults, selfwork or the queue. With the loop off the picker does
nothing.

**Where it runs.** `housekeep.STEPS` has a `pick` step right after `faults`,
so it runs on the engine's duty pass like the other steps, and an exception
in it is written down as a fault without stopping the rest. The engine
itself doesn't know about it. The goals table is all the state there is: a
restart loses nothing.

**The idle test.** The picker only picks when:

- no goal is `drafting` or `planning`;
- no item is `waiting`, `building`, `judging`, `reviewing`, `queued`,
  `resolving` or `rechecking`;
- no run with priority `now` is `queued` or `running`.

It opens at most one goal per pass, and that goal makes eki busy, so picks
happen one at a time. At most `self.picks_per_day` (6) goals in any 24
hours, counting every goal eki opened for itself — `faults.tick`'s
included.

**Waiting on you stops it.** `selfpick.waiting` counts items that are
`proposed` or `locked`, plus open asks on the threads of goals and items.
When that reaches `self.review_max` (3), nothing is picked, and the board
says `loop: stopped — N wait for you (eki self apply …)`. Apply, drop or
answer, and it picks again.

**The three stages, in order.** The first that has something wins.

1. **Faults.** Any fault in eki's own code seen at least once in the last
   7 days (`faults.pending`) that isn't open or recently landed gets a goal
   through `faults.open_goal` — the same function `faults.tick` uses — and
   `self.fault_items_per_day` still caps it. Why: "fault eki/x.py:12
   KeyError, seen N times in 7 days".
2. **The journal's costliest cluster** (`eki/costs.py`, last 7 days):
   - `handoff` on a row that could stay local: handed-off runs on a row
     one of whose targets is a local provider able to do what the row
     needs;
   - `correction` on a row: corrections, keyed by the row of the run you
     corrected (`data.previous`).

   A cluster needs `self.pick_min_cluster` (3) rows; the biggest wins, and
   on a tie corrections come first. The winner opens a goal (source
   `journal`, pick key `journal:<kind>:<row>`) that goes straight to
   planning. Its text names the cluster, the row, the count and up to 5
   example prompts (scrubbed, 200 characters each) with their run ids, and
   asks the planner to make these requests stay local, or be routed and
   answered right. A cluster isn't picked again while its goal has open
   items or within 7 days of it landing; after a landing only newer
   journal rows count.
3. **ROADMAP.md** (`eki/roadmap.py`), read from integration `main`. The
   open `- [ ]` entries are eligible, except those marked *(for a person)*,
   everything under `## Not planned`, and any already picked
   (`roadmap:<key>`, however that goal ended). The key is a hash of the
   entry's text, so an edited entry is eligible again. The picker opens a
   goal (source `roadmap`, state `drafting`) whose draft run, on the
   planner model in a read-only worktree `draft-<goal>`, gets the numbered
   entries, the last digest and the 7-day score. It is told not to ask
   questions, and answers `PICK: <n>`, `WHY: <one line>`, `GOAL:` with the
   entry's goal in the four-part shape, and `DRAFT: done` (or `DRAFT:
   person <why>`). The goal then plans as usual, told to add a last
   docs-only item that ticks the entry's box and depends on every other
   item. A missing or out-of-range `PICK` leaves the goal `left` with
   `pick_key` `roadmap:none`; `DRAFT: person` or a missing goal leaves it
   `left` with the picked key recorded.

**`pick_key` and `why`.** Every goal the picker opens has both on the goals
table: `fault:<file:line type>`, `journal:<kind>:<row>` or
`roadmap:<key>`, and a one-line reason. The why shows on `eki self` under
the goal line, in `eki self show <goal>`, and in the digest's **Picked by
eki** section (goal id, first line, why).

**The dry run.** `eki self pick` shows what the picker would do now without
doing it: idle or not and why, the waiting count against `review_max`, and
the stage it would take with its why. For the roadmap stage it only lists
the eligible entries: the ranking is a run.

**How far a pick goes.** Picked goals are owner `eki`: background priority,
and no further than `self_autonomy` allows. The queue and the lock list are
the same as for any other change.

| Setting (`self.` in routing.json) | Default | |
|---|---|---|
| `loop` | `false` | the switch; only `eki self loop on\|off` writes it |
| `review_max` | 3 | stop while this many results wait for you |
| `picks_per_day` | 6 | goals eki opens for itself in any 24 hours |
| `pick_min_cluster` | 3 | journal rows a cluster needs |
| `fault_items_per_day` | 3 | fault goals in any 24 hours (as before) |

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
  and neither notices. The train's check (gate 3) makes sure the previous
  build still opens the migrated copy.
- **The launcher supervises.** launchd runs a small shell script, not the
  engine. It loops: run `current`'s engine; if it exits with the *swap*
  code (the engine flipped the symlink first), run `current` again; if it
  dies within the watch window before the engine wrote its healthy marker,
  flip `current` to `previous`, record the rollback, run again. No separate
  watchdog, no port probing.

The web UI is read from disk on every request, so a swap makes it live on
refresh. The Swift shell is rebuilt only when `mac/` changed.

The **train** goes every `self_release_minutes` (5): if the integration ref
is ahead of the running build and its head is green, build it, run the
full check on the build (gate 3; skipped when it carries only docs), flip
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
4. ~~**The queue**~~ — the integration repo, speculative rebase, gate 2, the
   side path with `rerere`, landing and pushing, the train, `apply`. eki
   lands into its own clone at `~/.eki/self/repo`, and the engine drives
   `eki/queue.py`, `eki/resolve.py`, `eki/candidate.py` and `eki/train.py`.
   Built by eki itself: one goal planned into ten items, three waves of
   parallel worktrees, the person as the merge queue for those; the queue
   then took its first change goal → live in 25 minutes.
5. ~~**The loop closes**~~ — the journal (`eki/observe.py`), faults become
   items (`eki/faults.py`), the score and gate 4 (`eki/score.py`), the daily
   digest (`eki/digest.py`), all driven by `eki/housekeep.py`; sync rebases
   onto a moved origin.
6. ~~**The local model's share**~~ — chores (`eki/chores.py`): the review
   between gate 1 and the queue (`eki/review.py`), the digest's prose and
   triage (`eki/digestprose.py`), fault items' first briefs; the score
   counts only chat runs.

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
