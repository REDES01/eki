# The work model: what happens next to a goal and its items

The rules for "what happens next" live in one place: a pure
`step(goal, items, settings, event) -> (goal, items, actions)` in
`eki/workstep.py` (the queue's stages in `eki/workstep_queue.py`). eki's own
work and a project's work are two executors of the same rules; eki itself is
the default project. Two copies of the rules drift apart — a fix lands in one
and not the other, and the same event gets two answers — so there is one copy.

| Module | Holds |
|---|---|
| `eki/workmodel.py` | the nouns: phases, `GoalState`, `ItemState`, `Settings`, `Event`, `Action`, `label` |
| `eki/workstep.py`, `eki/workstep_queue.py` | the rules. Pure: no files, git, runs, clock, database or logging |
| `eki/workproject.py` | `WorkProject`, and `settings`: the one function that turns cards and settings into `Settings` |
| `eki/workrun.py` | `load`, `feed` (state and actions saved in one transaction), `carry` (actions done once, through `HANDLERS`) |
| `eki/workhandlers_build.py`, `eki/workhandlers_queue.py` | the handlers of runs and of the queue |
| `eki/buildfeed.py` | the build executor: which build, gate-1 and review runs ended, their facts as events (the fixer's in `eki/fixer.py`, the calls' in `eki/callwait.py`, plans and drafts in `eki/selfplan.py`, `eki/selfdraft.py`) |
| `eki/queue.py`, `eki/projectqueue.py`, `eki/queuestack.py`, `eki/resolve.py`, `eki/train.py` | the queue's executors: eki's (integration main, the train) and a project's (its branch, `projectland.land`); the stack's walk and gate 2 shared in `queuestack` |

An executor's tick looks at what changed — a run ended, git moved — does the
I/O that tells it the facts (commit the worktree, rebase, ask git whether a
commit is in the target), makes an `Event` of them, `feed`s it and lets
`carry` do the actions. Nothing is held in memory: the state is in `goals`
and `items` (`state` plus the JSON `work` column), the actions not yet done
are in `work_actions`, and the engine calls `carry` before the work ticks, so
a restart between saving an action and doing it does it then, exactly once
(every handler is idempotent; a run start is keyed by item, stage and tries).

## Phases

A goal is in one of three phases; its *stage* is today's `goals.state`.

| Goal phase | Stages |
|---|---|
| `shaping` | `drafting`, `planning` |
| `planned` | `planned`, `accepting`, `accepted`, `judgment` (final acceptance, eki/acceptance.py; step does not decide these yet) |
| `failed` | `failed`, `left` (a draft that found no goal; `goal_label` keeps it `left`) |

An item is in one of seven phases; its *stage* is today's `items.state` word,
plus `gate 2` (a queued item whose batch check is running — the board still
says `queued`). `label(item)` gives back the exact `items.state` word, so
`eki self`, selfview, the web board, the digest and notify don't change.

| Item phase | Stages (= labels) |
|---|---|
| `waiting` | `waiting` |
| `working` | `building`, `fixing`, `resolving`, `waiting on calls` |
| `checking` | `judging` (gate 1 and the review side by side), `reviewing` (legacy), `gate 2` (labelled `queued`), `rechecking` (legacy) |
| `ready` | `proposed`, `locked` — waiting for the merge decision |
| `merging` | `queued` |
| `done` | `applied`, `landed`, `live` |
| `stopped` | `left`, `unfit`, `dropped`, `rolled back` |

`ItemState` carries id, deps, files (declared write-set), touched, phase,
stage, tries, reviews, fixer_used, gate1, gate2, review, hold_until and why.
`why` is the board's words after `item <id>: `.

## Events

`Event(kind, at, data)`: `at` is the time, given from outside; `data` holds
the facts the executor found, and `item` when the event is about one item.

| Kind | Fed when | Facts in `data` |
|---|---|---|
| `goal_submitted` | a goal is asked for | `plan`, `draft`, `item` (the one item's id without a plan), `files` |
| `plan_ready` | a draft or plan run ended | `of` (draft \| plan), `ended` (run state), `items` [{id, deps, files}], `why`, `error`, `left` |
| `item_started` | the scheduler has room, its calls are back, or its hold is over | `stage` (the stage a wake goes back to), `why`, `recheck` (a broken setup's recheck: no try) |
| `item_finished` | a build, fixer, resolve or recheck run ended | `ended`, `verdict`, `reason`, `changed`, `ended_early`, `touched`, `calls`, `wakes`, `can_fix`, `commit_error`, `branch`, `said`, `no_review`; for a resolve `outcome` (resolved \| stopped \| bad \| broken), `runs`, `retried`, `person`, `files`, `on` |
| `check_verdict` | gate 1 and the review both ended; or a gate-2 check ended | `result` (green \| red), `ended`, `review`, `tail`, `can_fix`, `second` (the judge run is already the second); gate 2: `gate: 2`, `stack` [{id, result}], `tails` |
| `item_failed` | a run failed, its kind known | `{kind, retry_at, fix}` (kind in `FAILURE_KINDS`), `why`, `can_fix` |
| `run_lost` | a run was lost to a restart or a lease | — |
| `merge_result` | the item's rebase onto its predicted head | `result` (rebased \| conflict \| failed), `on`, `files`, `error`, `unbatched`, `stack` |
| `seen` | the executor looked at a ready or queued item's commit | `in_target`, `queueable` (its worktree is there and in the repo, it has a commit), `locked` (the locked paths it touches: eki's `locks.locked_in`, a project's none), `apply` (autonomy apply); without these two step reads `Settings.locked` / `merge`; `where`, `onto`, `sha`, `branch` |
| `landed` | the queue moved the target, a land waits, or the train settled | `went` (target \| live \| rolled back), `sha`, `branch`, `waits`, `why` |
| `person_decided` | `eki self retry / drop / apply`, `eki self goal retry` | `decision` (retry \| drop \| apply), `yes`, `fresh`, `plan` |

`FAILURE_KINDS = (quota, transient, environment, judgment, lost)` are the
failure-classes goal's kinds. An executor reads the kind `stopkind.classify`
stored on the run (`runs.stop`): `transient` → `transient`; `environment` →
`environment` with `retry_at` its recheck (`stopkind.ENV_RECHECK`) and `fix`
what is broken (eki/itemhold.py); a red check, a
build that ended early, nothing changed, a conflict or a crash → `judgment`;
a run lost to a restart or a lease → the `run_lost` event (never a try).

| Kind | step does |
|---|---|
| `quota` | `hold_until` at `retry_at`. A build goes back to `waiting`, its try not counted; any other stage keeps its stage and starts the same run again once `item_started` comes after the hold |
| `transient` | as `quota` when it has a `retry_at`; else as `judgment` |
| `environment` | `ask_person` (keyed by the fix: one ask per distinct fix); the fixer is not spent. With a `retry_at` (a broken setup, eki/itemhold.py) the item goes back to `waiting`, its try kept; the executor's `items.hold` keeps the scheduler off it till the recheck, and that recheck's `item_started{recheck}` is not a try. Without one, the item is `left` |
| `judgment` | today's ladder for the stage (below) |
| `lost` | as `run_lost`: the same run again, same tries |

## Actions

`Action(kind, item, why, data)`; `why` is the board's words. A goal's
draft or plan run (no item) carries `goal` in its data when the event did:
a handler gets only the project and the action. A start handler claims its
own `work_actions` row in the same transaction as the run it starts, so it
starts it once whatever restarts; an executor skips an item with an action
not yet carried (its `run_id` still names the run before).

| Kind | data | Handler (registered by) |
|---|---|---|
| `start_run` | `role` (build \| fix \| plan \| draft \| review \| resolve), `stage`, `tries`, `again`; fix: `failure`; resolve: `files`, `runs`, `retry` | L1 (build/plan/fix/review), L2 (resolve) |
| `run_gate` | `gate` (1 \| 2), `tries`; gate 2: `batch` (ids, front first; the item is the top) | L1 (1), L2 (2) |
| `merge` | `back` (it comes back at the back) | L2 |
| `land` | — (land the front of the stack when it is green and on the target's tip) | L2 |
| `notify` | `key`, `kind`, `what`, `act` | workrun |
| `hold_until` | `until` | workrun (the hold itself is in the saved state) |
| `ask_person` | `key` | L1 |

## Settings

`Settings` has six fields, built by `workproject.settings` only.

| Field | Values | eki | A project |
|---|---|---|---|
| `plan` | on \| off \| single | its card's plan (`split` → on) | its card's plan; no card: on |
| `gates` | from check, review, playtester, batch | check, review (when `self.review`), batch | its card's `command` (with text or rules) → check, review, playtester, `batch` (when it has a batch command). No card: check, review (`self.review`), batch when the row has a check; none at all without one |
| `merge` | auto \| person | `self.autonomy` apply → auto | `projects.autonomy` apply → auto (GitHub: person) |
| `parallel` | n (0: no cap of its own) | card or `self.parallel` | its card's; no card: 0 |
| `locked` | path globs | `locks.HARD_LOCKED` | none |
| `kind` | eki \| local \| github | eki | local, or github with a repo |

`kind` says where an item lands (difference a) and who gets a fixer
(difference h). Caps, round-robin order and write-sets stay in the scheduler:
they are capacity, not stage rules.

## Today's code, mapped

Each row: where the decision is today, what it decides, and how the model says it.

| Today | Decision | Event → stage / action |
|---|---|---|
| `selfwork.submit` | plan or not; draft first; plan single | `goal_submitted` → `drafting` + start_run draft, `planning` + start_run plan, or `planned` with one `waiting` item |
| `selfdraft.conclude` | draft run failed / no goal / project gone / goal | `plan_ready{of: draft}` → `failed`, `left`, `failed`, `planning` + start_run plan |
| `selfplan.conclude_plan` | plan failed / unreadable / items / nothing now | `plan_ready{of: plan}` → `failed`, `failed`, `planned` + items `waiting`, `planned` with "nothing worth doing now — …" |
| `selfdraft.retry` | rerun a failed or left goal | `person_decided{decision: retry}` (no item) → `planning` / `drafting` |
| `selfwork.FIT`, `scheduler.start_ready` (fit rule) | a dependency is fit once `landed`, `live` or `applied` | `item_started` → `building` + start_run build (tries + 1); blocked: why "after <dep>"; `workstep.blocked_by` |
| `selfwork._conclude_build` | run ended not done | `item_finished{ended}` → `left` |
| `callwait.hold` | calls out and wakes < 4 | `item_finished{calls, wakes}` → `waiting on calls` |
| `callwait.tick` / `_wake` | calls back or cap passed; unreadable waits | `item_started{stage}` → the stage again + start_run (again); no stage → `left` |
| `selfwork._built` | commit failed | `item_finished{commit_error}` → `left` |
| `selfwork._built` | person, or nothing changed | → `left` (+ notify needs_you for person) |
| `selfwork._built` + `fixer.offer` | ended early with nothing changed | → `fixing` + start_run fix (stage `nothing`) |
| `selfwork._built` (`workflowgates.gate` None, `projects.check_argv` None) | no gate 1 | → `proposed` "built — proposed on … (said)" |
| `selfwork._built` + `review.ask` | gate 1 and the review | → `judging` + run_gate 1 (+ start_run review) |
| `selfjudge.conclude` | red, tries − reviews < 2, run failed | `check_verdict{result: red}` → `waiting` "checks failed; one more try" |
| `selfjudge.conclude` + `fixer.offer` | red again | → `fixing` + start_run fix (stage `gate1`), else `unfit` |
| `selfjudge.judged` | green, not objected | → `proposed` "reviewed ok …" / "fit (not reviewed: …)" |
| `selfjudge.judged` | review no, first time | → `waiting`, reviews 1, "rebuilding once" |
| `selfjudge.judged` | review no again | → `proposed` with the objection |
| `selfjudge.unsure` | green, the judge uncertain (`review` "uncertain: …"), no second judge yet (`second` false) | → stays `judging` + start_run review `second: true` (`judge.second`) |
| `selfjudge.unsure` | green, still uncertain after the second judge, or none could start | → `proposed` with review "uncertain: …" + notify needs-you |
| `review.conclude` (legacy `reviewing`) | ok / none / no | `check_verdict{review}` → `proposed` / `proposed` / as `judged` |
| `fixer._conclude` | fixer run failed / git trouble | `item_finished{ended}` / `{leave}` → `left` |
| `fixer._conclude` → `selfwork._built` | fixer done | `item_finished` from `fixing` → as a build, "(after the fixer)" |
| `selfwork._settle_applied` | a proposed commit is in main / the project branch | `seen{in_target}` → `applied` |
| `queue._admit` | a proposed or locked commit in integration main | `seen{in_target}` → `applied` (proposed), `landed` (locked) |
| `queue._admit` | touches a locked file | `seen{queueable}` → `locked` + notify |
| `queue._admit`, `projectqueue._admit` | merge auto, not objected, queueable | `seen{queueable}` → `queued` + merge |
| `queue.apply` | a person queues it (locked needs yes) | `person_decided{decision: apply}` → `queued` + merge; else `Refused` |
| `queue._rebase` | rebased / conflict / failed | `merge_result` → `queued` / `resolving` + start_run resolve / `left` |
| `projectqueue._not_batched` | no batch gate | `merge_result{rebased}` → gate2 green, words from `unbatched`, + land |
| `projectqueue._judge` (no check) | not judged | same rule: `batch` not in gates |
| `queue._judge`, `projectqueue._judge` | the bisect: greens, the next batch, red at the front | `check_verdict{gate: 2, stack}` → gate2 green + land; items `gate 2` + run_gate 2; "waits for the green items under it" |
| `queue._unfit` + `fixer.offer` | the bad one at the front | → `fixing` (stage `gate2`), else `unfit` gate2 red |
| `queue._land` | fast-forward done | `landed{went: target}` → `landed` |
| `projectqueue._land`, `_landed` | the branch moved / the folder has local changes | `landed{went: target}` → `applied`; `{waits}` → stays, "waits: …" |
| `projectqueue._already_in` | a queued commit is in the branch (a restart) | `seen{in_target}` → as landing |
| `resolve._conclude_resolve` | resolve run failed | `item_finished{ended}` → leave |
| `resolve._resolved` | person / resolved / stopped again (3 runs) / broken | → `left` + notify / `queued` + merge (back) / `resolving` + start_run, or leave / leave |
| `resolve._failed` | markers or no ITEM line, first or second time | `item_finished{outcome: bad}` → `resolving` + start_run (retry) / leave |
| `resolve._leave` + `fixer.offer` | leave | → `fixing` (stage `resolve`), else `left` |
| `resolve._conclude_recheck` | legacy `rechecking` ended | `item_finished` → `queued` + merge (back) |
| `train.settle` (`_mark`) | the build went live / was rolled back | `landed{went: live / rolled back}` from `landed` |
| `selfwork.retry` | not while live; fresh when its worktree is gone | `person_decided{decision: retry, fresh}` → `waiting`, fixer cleared; else `Refused` |
| `selfwork.drop` | drop | `person_decided{decision: drop}` → `dropped` |

## Where the two copies differ today

Each difference and its fate: a **setting**, **kept** (an executor fact,
the same rule fed different facts), or a **fixed bug**. Behaviour stays the
same everywhere else.

- **(a) Landing.** eki lands as `landed` and the train makes it `live`; a
  project lands straight to `applied` with `landed_on`. *Setting:*
  `Settings.kind` (`workstep_queue.land`). The executor writes `landed_on`.
- **(b) Admit.** eki's admit needs autonomy apply and holds items that touch
  locked files (`locks.locked_in`); a project's has no lock check and needs
  the worktree folder. *Setting* for the merge (`merge: auto`) and the locks
  (`locked`: eki's hard list, a project's empty); *kept* for the folder —
  `seen.queueable` is the executor's fact (eki: the worktree belongs to the
  integration repo; a project: the folder is there and there is a commit).
  eki still locks items under `propose` (the lock check comes first), as today.
- **(c) Gate 2 red at the front.** eki waits for the greens under it to land,
  so the fixer starts on the main they made; a project called `queue._unfit`
  at once, on the old tip. **Fixed bug:** a project gets eki's rule
  (`workstep_queue.stack`: red at `lo > 0` waits).
- **(d) No check.** A project with no check marks gate 2 green as "not
  judged"; eki always runs `queue.GATE2`. *Setting:* `batch` is not in a
  project's gates without a batch command, and one rule — no `batch` gate,
  gate 2 green unjudged — covers it; the words come from the executor
  (`merge_result.unbatched`).
- **(e) Gate 1.** eki runs `selfwork.CHECK` or the doccheck whatever its card
  says; a project runs `workflowgates.gate`, then `projects.check_argv`, or is
  proposed unjudged. *Setting:* gate 1 is a run when the gates hold check,
  playtester or review (a card with review only runs one that says so); the
  command itself is the executor's (`run_gate 1`).
- **(f) Batch.** A project card without `batch` lands items unbatched
  (`projectqueue._not_batched`); eki always batches. *Setting:* the same
  rule as (d).
- **(g) Already in the target.** `integration.contains` or the source main
  (`selfwork._settle_applied`, `queue._admit`) against `projectwork.merged`
  and `projectqueue._already_in`. **One rule**, by stage, fed the git fact as
  `seen.in_target`: proposed → `applied`, locked → `landed`, queued → landed
  as (a). The executor asks git its own way.
- **(h) The fixer.** Only eki's items get one (`fixer.offer` returns None for
  a project). *Setting:* `kind == "eki"`; the executor adds `can_fix`
  (`self.fixer` on, the worktree there).
- **(i) The review.** eki and a project without a card read `self.review`; a
  card reads its own `review`; a project without a card or a check gets no
  review at all. *Setting:* `review` in the gates.
- **(j) Unjudged words.** A card with self only proposes with
  `workflow.said(wf)` and review "none: not in this project's workflow"; a
  project without a card says `projectwork.not_judged`. *Kept:* the executor
  gives `said` and `no_review`.
- **(k) Waiting to land.** A project's land can wait on local changes in the
  person's folder ("waits: …"); eki's fast-forward just stops. *Kept:*
  `landed{waits}`.
- **(l) When the wait ends.** After landing the greens, eki walks again in
  the same tick; a project on its next tick. *Kept:* executor timing.
- **(m) Which queue runs.** eki's queue holds eki's items; a project's queue
  runs only for a local project under apply (a GitHub one goes back as pull
  requests). *Setting:* `merge` (GitHub is `person`) and `kind`.

## Left beside the model for now

Nothing of the item rules: gate 2 and resolve feed the model too (L2), and
every fixer run is started by the one `start_run` fix handler
(`workhandlers_build`; the queue adds only `resolve` and gate 2 to its
`START` / `GATES` tables). What still decides outside `step` is the goal's
final acceptance (`eki/acceptance.py`: `accepting`, `accepted`, `judgment`).

## Behaviour the model adds

None changes today's paths until an executor feeds the event: `quota` and
`transient` holds, `environment` → `ask_person`, and `run_lost` restarting
the same run come with the failure-classes goal.

What the build executor's feeding changes, in words only or where today had
no answer:

- A build or fixer whose run row is gone (a build was stuck in `building`, a
  fixer was left "the fixer run ended missing") is `run_lost` now: the same
  run started again — a fixer told its first stage and failure — its try not
  counted and no second fixer spent.
- A build or fixer run whose `runs.stop` says `transient` is `item_failed{transient}` with no retry time (the worker's own
  retries are spent): left, as before, "left for you — the build run ended …".
- A fixer that git can't put on main is left "the fixer couldn't start: …"
  (before: left with the failure that called it).
- Board lines say the model's `why` without a run id, e.g. "gate1 failed — the
  fixer is on it"; a left draft's `goals.error` is "left — <why>"; a dropped
  item's `items.error` is "dropped". `items.error` of a left or unfit item
  keeps today's words (the executor writes them right after `feed`).
- A build's brief lists every item with a run going in its repo when its run
  starts, the ones started in the same scheduler pass included.
