# eki builds eki — how

Design for the self-build track of the [roadmap](../ROADMAP.md). What's decided, what's
built, and what each remaining piece has to do.

## The shape

```
 you ask · a fault · the loop's next item (eki/selfloop.py)
        │
        ▼
   self-work run ── an agent (Claude Code, Codex, Codex on a local model)
        │            edits a checkout of eki that is NOT the one running
        ▼
    candidate ────── eki/candidate.py: is this checkout fit to run?   [built]
        │
        ▼
      swap ───────── done by the supervisor, after the run has ended
        │
        ▼
     watch ───────── healthy for a few minutes, or the old build comes back
```

Four processes matter, and keeping them apart is most of the design:

| | what it is | who may change it |
|---|---|---|
| the running engine | `eki serve`, under launchd | replaced only by the supervisor |
| the agent | a CLI the engine started | works only in the self-work checkout |
| the candidate | the new code, on a spare port, in a sandbox home | thrown away after the check |
| the supervisor | a small script that swaps and rolls back | a person, never eki alone |

## The problem that looked hard, and isn't

An engine restart marks live runs `interrupted` (`RunStore.__init__`, owner
path). So an engine that replaced itself *from inside a run* would kill the
run doing it.

It doesn't have to. The self-work run never swaps anything. It ends — state
`done`, output "candidate ready: <path>, <report>" — and the swap happens
afterwards, outside any run. Nothing is killed by its own success.

That leaves other people's runs. Two facts already in the code settle it:

- `/api/health` reports `running`. The supervisor waits a little (2 min by
  default) for a moment with none, then swaps anyway. Work is never paused
  for it: runs keep going and new ones keep starting.
- `Engine.resume` carries a thread on after an interruption when the backend
  keeps its own session (Claude Code, Codex): the new engine resumes the
  runs the swap cut off (see *Runs cut off by a swap*, below).

(An earlier version waited up to 10 minutes for zero runs. With self-work
going in parallel there were always runs, and every newer apply restarted
the wait — on 2026-09-24 the engine stayed on fba7d32 through three
applies.)

## The candidate check  (built: `eki/candidate.py`)

A checkout is fit to run if all of this holds. Each step is a named check in
the report; the first failure stops it.

1. **tests** — `python -m pytest -q` in the checkout, with the running
   install's interpreter.
2. **boots clean** — the candidate engine starts on a spare port with `HOME`
   pointed at an empty sandbox, so every `~/.eki` path in the code lands
   there and nothing of yours is touched. A seed config gives it one
   provider: a stub OpenAI-compatible server the checker runs itself.
3. **answers** — `POST /api/ask`, follow the run to `done`, and the stub's
   words come back. That is the whole path: label, route, adapter, run
   store, stream.
4. **takes your data** — a second boot, sandbox home again, this time on a
   *copy* of the real database (SQLite's backup API, so a live db is safe to
   copy); nothing fires twice — goals live in the home the candidate doesn't
   share, and an old database's schedules are switched off in the copy. It must
   come up healthy and list the same conversations the running engine has.
5. **can be left** — the running code opens that same copy afterwards. If
   the candidate migrated the schema somewhere the old build can't read,
   rollback would be a lie; this is where that shows.
6. **restart** — the quick restart drill (`eki/drill.py`, below): the
   checkout's engine, in a sandbox, restarted in the middle of a stub
   model's answer and in the middle of a candidate check. Nothing may be
   lost, doubled or reported failed. About fifteen seconds. A change that
   breaks *a restart loses nothing* is never fit. (The drill's own
   sandboxed engines skip it — `EKI_CHECK_SKIP=restart` — so a drill
   never runs inside a drill.)

Not checked yet: the Swift app (needs `mac/build_app.sh`, minutes and Xcode —
a separate, optional check), and real providers (the stub stands in; a real
CLI would spend quota and need your login inside a sandbox home, which is
exactly what eki must not arrange).

```
python -m eki.candidate /path/to/checkout            # human-readable
python -m eki.candidate /path/to/checkout --json     # for the supervisor
python -m eki.candidate . --skip tests               # just the boot checks
```

Exit code 0 means fit.

## The swap  (built: `eki/builds.py`, `eki swap`)

What runs is decided by one symlink, not by where the launch agent points:

```
~/.eki/builds/
    0.2.0-58fd719/        a full checkout + its venv, read-only once built
    0.2.0-a1b2c3d/
    current  -> 0.2.0-a1b2c3d
    previous -> 0.2.0-58fd719
```

As built: a build is a `git archive` export of one commit plus
`.eki-build.json`, and shares your checkout's venv (eki isn't installed into
it; it runs from its folder), so it is a few MB and no reinstall. The launch
agent runs your venv's python with `WorkingDirectory` and `PYTHONPATH` at
`builds/current`, `EKI_SOURCE` and `EKI_CONFIG` pointing at your checkout.
At install `current` *is* your checkout ("dev"), so editing and restarting
works as before; `eki swap <ref>` moves to a build, `eki swap --back` to the
previous one, `eki swap --dev` back to the checkout. `/api/health` says
which build is running. Builds that are neither current nor previous are
removed after a week.

```
eki builds                      # → current, ↩ previous, the last swap
eki swap HEAD                   # candidate check, then the supervisor swaps
eki swap swap-test --no-check   # (how the rollback was tested)
```

**The app follows** (`eki/appbuild.py`). The Mac app is its own program,
built from `mac/` (every `mac/*.swift` is part of it). When a change touches
`mac/`, the candidate check typechecks the app (*app*), so Swift that doesn't
compile is never fit. After a healthy swap whose `mac/` differs from what the
installed `Eki.app` was built from, eki rebuilds it from the running build
and puts it in place, quitting and reopening it — only while it isn't the app
in front; otherwise it waits and tries again every five minutes. A build that
fails leaves the app as it was. `~/.eki/app.json` says what it was built from.

**Nothing running is dropped.** A healthy build goes into your checkout only
when it can (edits you haven't committed stop it), so the engine can run
something your checkout lacks. Three things keep the next build from
dropping it (`builds.live` / `behind` / `base` / `catch_up`):

- *What runs* is the current build — or a swap still waiting or swapping,
  since it will run.
- New work goes on top of your checkout **with** what runs: HEAD if it has
  it, the running commit if your checkout is only behind, otherwise the two
  merged in git's store without touching your files. If they conflict, apply
  says where rather than guessing.
- The missed merge is brought in as soon as your checkout allows — after a
  healthy swap, every five minutes, and before `eki swap` (fast-forward, or a
  merge alongside commits you made since). `eki swap` refuses a build that
  would still drop something (`--force` overrides).

**The release train.** Applied changes don't each go live on their own
(on 2026-09-24 that was six restarts in an hour, each cutting off what ran
beside it). An applied change is built and *boards* the train
(`builds.board`); at most once every `self_release_minutes` (15) the engine
sends the newest build to the supervisor (`builds.depart`,
`Engine.self_release`) — each car is built on top of the one before (the
train counts as what runs, `builds.live`), so the newest carries them all.
Jobs keep running and starting throughout; nothing waits for the train but
the go-live itself. The first apply after a quiet window goes at once. When
the go-live settles, every change it carried is applied (or rolled back)
together. `eki self` and the Self board say *next go-live in N min,
carrying: …*; `eki self apply <id> --now`, `eki self release` or *Go live
now* on the board don't wait; `eki self release 30` changes the window.

One swap at a time, newest wins: a swap still waiting is superseded (the new
build contains it) **and its deadline stands** — the new one doesn't start a
wait of its own, so a steady stream of applies can't hold the engine back.
One already swapping (or past its wait) finishes first, and the next is
counted to the same deadline once it has. `eki self`, `eki builds` and the
Self board say *new version going live in N min* while one is on its way
(`builds.going_live`).

## The supervisor  (built: `eki/supervisor.sh`)

Small on purpose — a hundred lines of shell, no imports from eki, so that
no change to eki can break it. `eki agent install` copies it to
`~/.eki/bin/eki-supervisor`; nothing else does.

1. wait (default 2 min; `builds.swap` passes what's left of the deadline) for
   `/api/health` to show no runs — then swap anyway; from here on it is not
   superseded (it ignores TERM) but waited out
2. `previous` → what ran, `current` → the new build, `launchctl kickstart -k`
3. the engine must answer `/api/health` *as that build* within a minute,
   and be the same process after the watch window (default 3 min)
4. otherwise swap back and restart; the outcome is `~/.eki/self/swap.json`,
   the story `~/.eki/self/swap.log`. The engine reads the outcome once, says
   it (a notification), and — for a healthy `eki self` change —
   fast-forwards it into your checkout if nothing there is uncommitted or
   newer.

After every restart it makes sure the old engine is gone — its pid from
`/api/health`, or whoever listens on the port — and stops it if a restart
left it behind; and the engine, started by launchd, does the same before it
binds (`service.take_port`: only an `eki serve` is stopped). On 2026-09-24
(22:46–22:56) a killed launcher left the engine under it holding the port,
every new engine died with "address already in use", and the swap rolled
back onto a build that failed the same way.

**The watchdog.** launchd runs `eki-supervisor --watchdog` every 30 seconds
(`local.eki.watchdog`, written by `eki agent install`): an engine that
doesn't answer `/api/health` within 10 seconds, and was already there the
look before, is noted in `swap.log` and restarted. Not during a swap.

**Runs cut off by a swap** (or any restart) aren't cancelled — and mostly
aren't even cut off. The programs doing the work are **workers**
(`eki/workers.py`), not children of the engine: Claude Code and Codex, the
test runs of a check, the candidate engine, a rebase. Each is started in a
session of its own under a small keeper, with its output in files the
engine reads rather than a pipe it holds:

    ~/.eki/work/<id>/  spec.json (command, run, step, thread)
                       state.json (pid, start time, exit code once it ends)
                       out, err, in (a fifo, for a program eki talks to:
                       the keeper always drains it; the engine writes
                       without waiting, and a program that takes none of
                       it for a minute is stopped, its turn cut off)
                       read (how far the engine has read `out`)

launchd's stop ends the engine's process group — the eki app and the
engine under it — but not them: each is in a session of its own.
(`AbandonProcessGroup` stays off: with it on, a stop that killed the
launcher left the engine running.) The engine going away detaches from them (`Engine.close`)
and the next one takes them up at start (`Engine.reattach_workers`):

- a program still working is **followed again** — read on from where the
  last engine's reading stood, so no line twice and nothing dropped; a
  permission or a tool call it was still waiting on is asked again. Its
  thread keeps what was said, notes the restart, and the next run follows
  the turn in progress (nothing is sent to the program);
- one that finished its turn while no engine was up is read to its end, and
  the turn concluded as usual;
- a check joins the test run still going (the same code: folder, commit and
  uncommitted state) or takes the result that came in the gap; a rebase
  still going is joined;
- only a worker dead with nothing written goes the way below.

A pid is believed only with its start time (`ps -o lstart`): a pid reused by
another program is neither followed nor killed. A person's cancel kills the
run's workers, on purpose. Housekeeping, every minute: finished work dirs
go after a week; a worker nobody took up — no run of this engine, not in its
hand — is killed after ten minutes, with a line in the journal.

When there's nothing to follow, the next engine marks the run interrupted,
says so in its thread, and carries it on (`Engine._carry`,
`resume_interrupted`):

- in Claude Code or Codex, in their session and in the copy of the folder
  they were working in — the session id is saved as soon as the program
  says it, so a run cut off mid-turn has one;
- self-work goes on as self-work: the same item, worktree and thread, and the
  item points at the new run at once, so the loop never takes it up a second
  time; one cut off before its program had a session is started again from
  where its item stands;
- a run that never began is simply asked again — and so is an answer with
  no folder and no session to carry on in (a model's words: asking again
  repeats nothing);
- a run with a folder and no session stays yours to retry — it may have made
  half its edits.

Never lost, never twice: a run is carried on once (`RunStore.carried_on` —
resuming or retrying one already carried on does nothing). A carrying-on cut
off by the next swap carries on again, up to five in a row; one lost to a
crash (not handed over) isn't, so a crash can't loop.

## A restart loses nothing  (built: `eki/steps.py`)

Runs were carried on across a swap; the helper steps around them weren't.
On 2026-09-24 a conflict-resolving agent killed by a restart (Claude Code,
exit 143) was reported as "the agent couldn't resolve it", a test run cut
off as "tests ✗" with a row of dots, and base checks started over. Now
every piece of self-work is a **step**, written down in
`~/.eki/self/steps.json` before it starts:

| step | what | taken up again by |
|---|---|---|
| `begin` | the worktree, the base's own tests | the same worktree, the check run again |
| `agent` | the agent's turn | the agent, in its session ("carry on where you left off") |
| `check` | committed, then the candidate check | the check run again on that commit |
| `apply` | the merge queue: on top, judged again, boarded | applied again — a rebase already done does nothing; its place in line kept |
| `resolve` | conflicts resolved by an agent, mid-rebase | the agent in the worktree as it stands; a rebase already finished goes straight to applying |
| `swap` | the go-live | the supervisor runs outside the engine; only one lost before it said how it went is asked for again |

A step is *running*, then *succeeded*, *failed* or **interrupted**.
Interrupted — a program killed by a signal (143, 137, -15), a test run
cut off, a worker lost with nothing written — is never failed (the engine
going away alone no longer interrupts one: its workers carry on, see *Runs
cut off by a swap*): the check
raises `steps.Interrupted` instead of a verdict, and a run cut off that way
is marked interrupted, not failed. On start the new engine marks every step
the old one left running interrupted (`steps.recover`) and takes each up
again (`Engine.self_carry_on`, also every minute for a program lost while
the engine stayed up) — **once**: a step is claimed by the one caller that
takes it up (`steps.claim`), and one cut off five times in a row is failed,
so a crash can't loop. `eki self` and the board show a spinner only for a
step that is live in this engine; one cut off says *carries on by itself*.

The base check is shared: runs that need the same base wait for one check
of it (a lock per commit) instead of starting their own, a build that went
live and stayed healthy counts as a base that passed, and a run cut off
after its base passed starts again from that base without checking it.

It lives outside `builds/`, is installed once, and eki's self-work is refused
any diff that touches it, `agent.py`'s plist writer, or `secrets.py` and the
quota bridges (the credentials rule). Those go to a person as a proposal
whatever the autonomy setting says.

## The restart drill  (built: `eki/drill.py`, `eki self drill`)

*A restart loses nothing* is easy to break without noticing — a new code
path that holds a pipe, a step not written down, a cut-off read as a
failure. So it is tried, the only way that counts: a real engine, restarted
in the middle of real work, and a look at what's left.

Each case gets a sandbox of its own: a HOME under `/tmp/eki-drill-*` (every
`~/.eki` path lands there), a spare port, a copy of eki's code as the
source it works on (with a one-test suite that takes a few seconds, so a
check can be cut off inside it), stub models, the fake Claude Code and Codex
from `tests/`, and a scratch launchd job set up like yours — restarted with
`launchctl kickstart -k`, the way a swap restarts yours. The go-live uses
the real supervisor, pointed at the scratch job (`EKI_JOB`) with a short
watch. Nothing of yours is touched and nothing appears on your screen
(`EKI_NO_NOTIFY`).

| work | restarted |
|---|---|
| chat — a model's answer, streamed | before the first word; mid-answer |
| agent chat — Claude Code in a thread | as it starts; mid-turn |
| folder — Codex in a folder | mid-turn |
| models — a local server eki started | loaded; with eki's record of it lost |
| self-work — one change, `--apply` | base check; agent turn; candidate check (its tests, its engine); merge-queue apply; go-live |
| resolve — a change that conflicts | while the agent resolves it |

After each: the work finished (nothing lost); nothing happened twice — no
two copies of a program at once, no line of output said twice, one commit
for the change; nothing reported failed, *couldn't resolve* or *✗*; no
step still shown working with nothing going; the thread says the engine
restarted wherever a run was cut off; a model server still up, the same
process, and known as eki's. The report is a table, work × restart point →
*ok* or what went wrong; exit code 0 only if every case is ok. A case that
missed its moment says so (*the drill's timing, not a verdict*) rather than
passing.

```
eki self drill            # the full drill, ~15 min; the result is kept for the weekly note
eki self drill quick      # the candidate check's one (a stub run, a stub check)
python -m eki.drill --only self-work --keep    # one kind; a failing case's sandbox kept
```

While eki works on itself (`eki self on`) the full drill runs once a week as
the loop's housekeeping — a worker of its own, so a restart of your engine
doesn't cut it off — and its table goes in the weekly note as it ran.
Sandboxes a killed drill left behind are cleared by the next one.

What the first runs found, and this change fixed:

- **A model's answer cut off mid-stream waited for you.** A run with no
  folder and no session only said words; it is now asked again
  (`Engine._carry`), under the same question.
- **An agent whose turn had ended was followed as if mid-turn.** A
  self-work run cut off in its check (the agent long done) had the agent's
  answer written into the thread a second time. A program's worker now says
  whether a turn is open (`turn` in its spec), and only an open one is
  followed (`Engine.reattach_workers`).
- **A model server whose record was lost** was taken back as eki's only at
  the first idle check, a minute on; now at start.

Not drilled: a restart during the supervisor's watch window — there a new
engine process *is* the sign of an unhealthy build, and it rolls back, on
purpose.

## Self-work runs  (built: `eki/selfengine.py`)

Every change eki makes to itself, whoever wanted it, is one kind of run: an
ordinary run whose payload names a self-work *item*. The engine drives it
through four steps (`SelfLoop._self_work`):

1. **begin** — a fresh `git worktree` of eki's source on `self/<id>`, under
   `~/.eki/self/`; the base must pass its own tests before anything is spent
   (a base that passed is trusted for a day; a fault fix skips this — broken
   code may well fail its tests, which is the point).
2. **the agent** — the same run carries on as an ordinary folder run in that
   worktree: routed, streamed into the thread, resumable after a restart. It
   is told how to work on eki (`selfwork.brief`): read first, add tests, run
   them, commit nothing, ask nothing.
3. **conclude** — eki commits what the agent did and puts it through the
   candidate check. No ROADMAP tick goes in the change (see *The roadmap*). A change to
   documentation only (`*.md` outside `eki/`) needs no candidate engine.
4. **then** — applied or proposed, by the autonomy setting; the thread gets a
   line from eki saying what came of it, and — if nobody asked for it in a
   chat — a notification only when it needs you (*The daily digest*, below).

Who starts one:

| from | how | when |
|---|---|---|
| a chat | "eki, make the chat list show the project name" — a message aimed at eki itself (`selfloop.addressed`) | at once, in that thread; "…tonight" queues it |
| the board, `eki self "…"` | Goals → Self → *Do it now* / *When there's room*; `--later` | at once, in its own thread; or queued |
| a fault | an error in eki's own code, twice (once in an engine loop) — `eki/observe.py` | queued for the loop when it's on; otherwise at once |
| the loop | the goal "eki works on itself" | a turn whenever the machine has room |

## The loop  (built: `eki/selfloop.py`, `eki/roadmap.py`)

`eki self on` (or *Start* under Goals → Self) makes a goal of kind `self`.
It gets turns like any goal — only when the machine has room, only on power,
stepping aside the way goals do — but its turn is not a sentence: it is the
next piece of self-work, taken in this order:

1. anything already begun and cut off (you came back, the engine restarted)
   — carried on in the same worktree and thread;
2. what you asked for, queued;
3. faults in eki's own code;
4. the weekly note, when a week has passed;
5. the next open item in `ROADMAP.md`, in the file's order — inside a stage,
   only once the open items above it have landed (see *The roadmap*).

**Budget.** By default eki's own code is worked on by your subscriptions'
spare room only — under pace for the week, never the last 30% of a window —
because a broken eki hurts everything else. `self_local` lets the local
models (Qwen with Codex's hands) take it too. The weekly note is writing:
the local models may always write it.

**Several at once.** Up to `self_parallel` (2) pieces of self-work go at
once — `eki self parallel 3`, or *At most N at once* on the board. The first
is the goal's turn; the rest start beside it, on subscriptions only (only the
goal's own turn steps aside when you need the machine). Each turn the number
is held to what the spare room carries: `capacity.spare` counts the requests a
subscription can take before the part kept for you, and a change is reckoned
at three of them; a subscription whose cost per request isn't known yet
carries one. The local models are counted apart: one self run between them.

**Lanes.** Before an item starts beside others, eki guesses where it will
work (`selfloop.area_of`) — from the paths and files it names (a cheap look
at the repo finds `Views.swift` is the Mac app) and then its words: *the Mac
app*, *the board*, *the command line*, *routing*, *self-work*, *the engine*;
docs and tests go with the code they're about, and are a lane of their own
only when nothing else is named. An item that names none of these is looked
up in the repo: its distinctive words (*swipe*, *trackpad* — not *fix* or
*real*) are found in file names and with `git grep`, each hit counted to
the lane of its file (a file named after the word counts more; the docs and
the tests, which mention everything, don't count); most hits wins, a tie
gives several lanes. A word found all over only hints. An item whose area
still can't be told doesn't hold up the rest: it goes beside the others and
waits only for another like it — and for one in the Mac app, when the hints
point there. An item doesn't start while one working shares its area — so
the Mac app, of which there is one, is changed by one at a time — and a
later item in another area goes ahead of it. The guess only has to be good
enough: what collides anyway is resolved when it's applied.

**The merge queue.** A change that is to be applied (its autonomy says so,
or `--apply`) joins the queue when its checks finish; changes are applied
one at a time, in the order they finished. Each is put on top of your
checkout with whatever landed before it, its conflicts resolved if it no
longer goes on top (the next in line waits for that), judged again, then
swapped in. Waiting in line is never a failure, and takes no room from the
work still going; one cut off by a restart keeps its place and doesn't hold
up the rest. `eki self` and the board list it. A change you apply by hand
isn't in the queue, but never overlaps one being applied.

**Your attention is part of the budget.** While `self_review_max` (3) fit
changes are waiting for you, the loop starts nothing new of its own; what you
ask for still goes ahead. An item whose change fails its checks is tried once
more, told what failed; after two it is *left for you*.

**The roadmap.** `eki/roadmap.py` reads `ROADMAP.md`: an item is a `- [ ]`
line under a `## ` section, with its indented lines. Items that say
`(for a person)`, and anything under *Not planned*, are never taken; an item
that says `(waiting on …)` isn't taken until that's removed.

*Order inside a stage.* The items of a `## Stage …` section are in order: an
open item waits until every open item above it in that stage has landed
(ticked, or its change applied), because the one below usually builds on the
one above — two agents starting Stage 7's second and third items before its
first would each invent their own memory store, and clash when applied. An
item marked `(independent)` neither waits nor holds anything up, and can go
beside the others. Later stages don't wait on earlier ones — the file's
order is already the loop's — and items in any other section (*Alongside
every stage*, *What eki keeps current*, an *Inbox*) are independent anyway.
`eki self next`, `eki self` and the board show what a waiting item waits for:
`after: <the item above>` (`roadmap.after`).

The agent
gets the item, what its stage is for, and four ways to finish, as the last
line of its answer:

| `ITEM:` | what eki does |
|---|---|
| `done` | commits the change; once it has landed, the item is ticked — `- [x] … *(eki: self/<id>)*` |
| `partial` | commits the slice, no tick; once it's applied the rest is *left for you*, taken again only if you reword the entry |
| `already` | the file lagged the code: nothing to commit, the item is ticked |

**One writer for ticks.** A change never ticks the roadmap in its own
commit — ticks landing back to back used to conflict with each other. The
merge queue writes the tick once the change has landed, as its own small
commit in your checkout (`roadmap: tick “…” — self/<id> landed`,
`selfwork.write_ticks`), and takes it back the same way if the change is
undone. A tick an agent makes anyway is dropped when its change is
committed (its other edits to ROADMAP.md are kept), and so is one in a
change made before this; a request that asks for a tick ("tick the item …")
keeps it. While your checkout has uncommitted edits to ROADMAP.md, the tick
waits.
| `person` | nothing to commit; the item is *left for you* with the agent's reason |

An item that ends with no change — `ITEM: person`, or nothing said — is
*left for you* (`eki self`, and *Left for you* on the Self board) and isn't
taken again on its own unless its entry in `ROADMAP.md` is reworded since;
*Let eki try* (`eki self retry`) takes it at once. What needs your hands or
real hardware — trying something on a real trackpad — is asked to end as
`person`, not be tried again. One you marked *Leave for me* stays yours
whatever the file says. An item cut off by a restart after its change was
judged is closed with that change, not started over. An item a change was applied
for is never taken again on its own, ticked or not.

**How far it goes alone.** `self_autonomy` is *propose* (a branch, a diff and
its checks; you apply it) or *apply* (a fit change that touches nothing
protected is applied at once). `self_autonomy_areas` overrides it per path —
`{"ROADMAP.md": "apply", "docs/": "apply"}` — the longest match winning; a
change is applied only if every file it touches may be. Protected paths — now
including `selfloop.py` and `selfengine.py`, which decide what eki takes on
and how far it goes — are never applied by eki at any setting: not by
autonomy, not by `eki self --apply`, not by the merge queue. That keeps eki
from applying them alone; it doesn't keep you out.

**Applying a protected change yourself.** `eki self apply <id>` lists the
protected files the change touches and asks *apply it anyway? [y/N]*
(`--yes` skips the question; with no terminal to ask, it needs `--yes`). On
the Self board the button reads *Apply…* and opens a confirm step naming the
files. Once you say yes it takes the ordinary path — rebased onto your
checkout, conflicts resolved by an agent, judged again, built, swapped in
watched and rolled back if it isn't healthy — and the change's record says
`applied_by: you` (`eki self show`, the board). The engine refuses an
unconfirmed apply of a protected change (`{"confirm": true}` on
`POST /api/self/changes/<id>/apply`), and a change that comes to touch a
protected path once it's rebased goes back to waiting for you. No more
merging by hand and `eki swap`.

**Whose work it is.** A fault's fix is work eki started on its own, with
nobody asking, so it isn't yours (`selfloop.owner`): it goes exactly as far as
the autonomy setting lets it — never `--apply` for itself — and so does
everything below it. Its run carries `owner: eki`; the programs eki starts
for a thread are told the thread (`EKI_PARENT`, and eki's tool server under
Codex), so a run the agent starts through eki — `eki_ask`, `eki ask`, `eki
self` — is eki's too, at any depth. Such work is refused, not asked: eki's
own checkout or builds as a folder (a change to eki goes through self-work,
in a worktree of its own); applying or undoing a change, raising the
autonomy, turning the loop on, and changing the settings or the routing
policy (403, "only you can do that"). A change it asks for is queued as
eki's, never applied on its say-so. Roadmap items and the weekly note are
turns of the goal you set up, so they are yours. What the harness itself
may touch — the sandbox a narrowed run gets — is Stage 2's permissions.

**Applying.** If your checkout has moved on since the change was made, it is
rebased onto your checkout first and judged again. If the rebase stops on a
conflict, it isn't handed back to you: eki opens a run in the change's thread
that asks the program that wrote it (or the router's pick) to resolve the
conflicted files in the change's own worktree, mid-rebase — told what the
change does and what your checkout gained in those files since. eki then
finishes the rebase itself, refuses a result with markers left or not on top
of your checkout (putting the change back exactly as it was, *conflicts*),
judges it again and applies it. Apply is refused while that runs.
A change carries no ROADMAP tick, so a tick is never what conflicts.
A resolution cut off by a restart carries on in the worktree as it stands
(*A restart loses nothing*, above).
`self_resolve: false` turns it off. Documentation goes straight into your
checkout (fast-forward); code becomes a build that boards the release train
and is swapped in with the next go-live (runs still going carry on in the new engine), watches, and rolls back if it isn't healthy — and a
healthy one is fast-forwarded into your checkout. Files you never added to git
don't stop that; edits to tracked files do.

**Offering a change upstream** (`eki/upstream.py`). What eki changes on
this Mac is a local branch on top of the last release; the public repo is
something else, because other people install what's merged there. So eki
never offers anything on its own: `eki self offer <id>` — a person asking —
puts that change's own commits, and only those, on top of the public `main`
(`self_upstream`, default `origin/main`) as a branch `eki/<id>`, in a
scratch worktree so your checkout isn't touched; pushes it (to
`self_offer_remote` when you work from a fork); and opens a pull request with
`gh`, saying what the change does and how it was judged here. It asks before
it pushes (`--yes` from a script). A change that leans on work only this Mac
has doesn't go on top, and nothing is pushed — it says which files. Without
`gh` the branch is still pushed and the page to open the pull request is
given. Offering again pushes the same branch and finds the pull request
already open (`~/.eki/self/offers.json`). Merging it and cutting a release
stay with whoever looks after the repo.

**What's new.** Every change ends with a summary for you, in plain words:
what's new or different, how to use it, what to check before applying. The
agent writes it under a `SUMMARY:` line; eki shows it at the top of the
change's message in its thread, in the notification, and in `eki self show`.

**Undo** is a change of its own: the revert, on top of your checkout as it is
now, judged like any other, then applied — you asked. **Discard** removes the
worktree and the branch, and the item isn't taken again until you say *Let eki
try*.

**The weekly note.** Once a week the loop writes down what it noticed — from
the journal (faults, providers saying no, your corrections, requests nothing
could take), the hours each backend worked (the local models' share of the
week against the 1% baseline), its own changes and the roadmap — and two or
three suggestions with their evidence, leaning to adding a provider or an MCP
server. The week's restart drill goes in too, as its table (*The restart
drill*, above). It suggests; you pick: *Ask eki to do it* (a queued request), *Add to
ROADMAP* (a commit to your checkout, under the stage it names or an *Inbox*),
or *Dismiss*.

**The daily digest** (`eki/digest.py`). One short page a day, at `digest_at`
(09:00): what changed in eki since the last page and why (you asked, a fault,
the next ROADMAP item), what was tried and didn't land, what helped (the hours
this Mac's own models worked, against the week's average; goal turns
finished), and what waits for you (proposals, and items left to you since the
last page). It is built from what eki wrote down, not by a model. It is on the
board (Goals → Self → *Today's digest*), in `eki self digest`, and said in one
notification — none on a quiet day. Per-change notifications are only for what
needs you: a change waiting to be applied, or an item left to you. A change
applied, gone live, or tried again later is a line in the next page; a
rollback is still said at once. `digest: off` turns it off.

What it keeps, all in `~/.eki/self/`: `work.json` (the items), `merge.json` (the merge queue), `log.jsonl`
(every change as it was judged), `changes.json` (where each stands now),
`steps.json` (every step, and whether it's live), `train.json` (the release
train), `ticks.json` (the ticks written), `notes/` (the weekly notes), `digests/` (the daily pages),
`base-ok.json` / `base-bad.json` (bases that passed, or failed), `drill.json`
(the last full restart drill).

```
eki self                         what it's doing, what waits for you, what's next
eki self on | off
eki self "make eki runs show durations" [--later] [--apply]
eki self -r "…" -r "…"           several at once, queued; --batch FILE: one per line
eki self parallel [N]            the most at once (default 2)
eki self release [N]             go live now; N: at most one go-live every N min (15)
eki self apply <id> --now        applied and live at once, not with the next train
eki self diff|show|apply|discard|undo <id>
eki self offer <id>              offer it upstream as a pull request (asks first)
eki self next
eki self retry|drop|mine <item>
eki self autonomy apply ROADMAP.md=apply docs/=apply
eki self note [now]
eki self digest [now]            the day's page; now: write it again
eki self drill [quick]           restart a sandboxed engine mid-work: is anything lost?
```

## Order of building

1. ~~candidate check~~ — done
2. ~~`builds/` layout + supervisor + `agent.py` pointing at `current`~~ — done
3. ~~`self` runs, *propose* only~~ — done (`eki self`)
4. ~~*apply here*~~, ~~protected paths~~ — done (`eki self --apply`,
   `self_autonomy`)
5. ~~faults as requests; roadmap items as requests~~, ~~the loop~~, ~~the Self
   view~~, ~~per-area autonomy~~, ~~undo~~, ~~the weekly note~~ — done (the
   loop, 2026-09-23)
6. ~~offering a change upstream as a pull request~~ — done (`eki self
   offer`); still open: the Swift app build as
   a candidate check; the app updating itself from releases through the same
   swap

Checked live on 2026-09-22: a healthy swap; a build that can't start rolled
back within a minute; Claude Code and Codex runs cut off by a restart
carried on in their sessions; and `eki self --apply` end to end — a change
written by Claude Code, 484 tests and the candidate check, swapped in,
healthy after 3 minutes, fast-forwarded into the checkout (f906934).
