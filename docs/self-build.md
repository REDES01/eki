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

- `/api/health` reports `running`. The supervisor **waits for zero**, up to a
  limit, before it swaps. Most swaps interrupt nothing.
- `Engine.resume` already carries a thread on after an interruption when the
  backend keeps its own session (Claude Code, Codex). If the wait runs out,
  the supervisor swaps anyway, and the new engine resumes exactly those —
  the ones `note_interruptions` finds with a session. A run with a folder and
  no session stays manual, for the reason `retry` gives: it may have made
  half its edits.

No change to `runs.py` is needed for the first version.

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

The app bundle is swapped the same way when a change touches `mac/`, and only
while the app isn't frontmost.

## The supervisor  (built: `eki/supervisor.sh`)

Small on purpose — a hundred lines of shell, no imports from eki, so that
no change to eki can break it. `eki agent install` copies it to
`~/.eki/bin/eki-supervisor`; nothing else does.

1. wait (default 10 min) for `/api/health` to show no runs
2. `previous` → what ran, `current` → the new build, `launchctl kickstart -k`
3. the engine must answer `/api/health` *as that build* within a minute,
   and be the same process after the watch window (default 3 min)
4. otherwise swap back and restart; the outcome is `~/.eki/self/swap.json`,
   the story `~/.eki/self/swap.log`. The engine reads the outcome once, says
   it (a notification), and — for a healthy `eki self` change —
   fast-forwards it into your checkout if nothing there is uncommitted or
   newer.

Runs cut off by a swap (or any restart) aren't cancelled: the next engine
marks them interrupted and carries on those in Claude Code or Codex in
their session and in the copy of the folder they were working in
(`resume_interrupted`, once — never a loop).

It lives outside `builds/`, is installed once, and eki's self-work is refused
any diff that touches it, `agent.py`'s plist writer, or `secrets.py` and the
quota bridges (the credentials rule). Those go to a person as a proposal
whatever the autonomy setting says.

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
3. **conclude** — eki commits what the agent did (with the ROADMAP tick, if
   the item is done) and puts it through the candidate check. A change to
   documentation only (`*.md` outside `eki/`) needs no candidate engine.
4. **then** — applied or proposed, by the autonomy setting; the thread gets a
   line from eki saying what came of it, and a notification if nobody asked
   for it in a chat.

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
5. the next open item in `ROADMAP.md`, in the file's order.

**Budget.** By default eki's own code is worked on by your subscriptions'
spare room only — under pace for the week, never the last 30% of a window —
because a broken eki hurts everything else. `self_local` lets the local
models (Qwen with Codex's hands) take it too. The weekly note is writing:
the local models may always write it.

**Your attention is part of the budget.** While `self_review_max` (3) fit
changes are waiting for you, the loop starts nothing new of its own; what you
ask for still goes ahead. An item whose change fails its checks is tried once
more, told what failed; after two it is *left for you*.

**The roadmap.** `eki/roadmap.py` reads `ROADMAP.md`: an item is a `- [ ]`
line under a `## ` section, with its indented lines. Items that say
`(for a person)`, and anything under *Not planned*, are never taken. The agent
gets the item, what its stage is for, and four ways to finish, as the last
line of its answer:

| `ITEM:` | what eki does |
|---|---|
| `done` | commits the change with the item ticked — `- [x] … *(eki: self/<id>)*` — so the tick lands exactly when the change does, and goes if you discard it |
| `partial` | commits the slice without a tick; the item comes back after it's applied |
| `already` | the file lagged the code: a commit that only ticks the item |
| `person` | nothing to commit; the item is *left for you* with the agent's reason |

**How far it goes alone.** `self_autonomy` is *propose* (a branch, a diff and
its checks; you apply it) or *apply* (a fit change that touches nothing
protected is applied at once). `self_autonomy_areas` overrides it per path —
`{"ROADMAP.md": "apply", "docs/": "apply"}` — the longest match winning; a
change is applied only if every file it touches may be. Protected paths — now
including `selfloop.py` and `selfengine.py`, which decide what eki takes on
and how far it goes — are never applied by eki at any setting.

**Applying.** If your checkout has moved on since the change was made, it is
rebased onto your checkout first and judged again; if it no longer fits, it
says so (*conflicts*) and waits. Documentation goes straight into your
checkout (fast-forward); code becomes a build the supervisor swaps in once
nothing is running, watches, and rolls back if it isn't healthy — and a
healthy one is fast-forwarded into your checkout. Files you never added to git
don't stop that; edits to tracked files do.

**Undo** is a change of its own: the revert, on top of your checkout as it is
now, judged like any other, then applied — you asked. **Discard** removes the
worktree and the branch, and the item isn't taken again until you say *Let eki
try*.

**The weekly note.** Once a week the loop writes down what it noticed — from
the journal (faults, providers saying no, your corrections, requests nothing
could take), the hours each backend worked (the local models' share of the
week against the 1% baseline), its own changes and the roadmap — and two or
three suggestions with their evidence, leaning to adding a provider or an MCP
server. It suggests; you pick: *Ask eki to do it* (a queued request), *Add to
ROADMAP* (a commit to your checkout, under the stage it names or an *Inbox*),
or *Dismiss*.

What it keeps, all in `~/.eki/self/`: `work.json` (the items), `log.jsonl`
(every change as it was judged), `changes.json` (where each stands now),
`notes/` (the weekly notes), `base-ok.json` (bases that passed).

```
eki self                         what it's doing, what waits for you, what's next
eki self on | off
eki self "make eki runs show durations" [--later] [--apply]
eki self diff|show|apply|discard|undo <id>
eki self next
eki self retry|drop|mine <item>
eki self autonomy apply ROADMAP.md=apply docs/=apply
eki self note [now]
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
6. still open: offering a change upstream as a pull request (this Mac's
   changes and the public repo are different things); the Swift app build as
   a candidate check; the app updating itself from releases through the same
   swap

Checked live on 2026-09-22: a healthy swap; a build that can't start rolled
back within a minute; Claude Code and Codex runs cut off by a restart
carried on in their sessions; and `eki self --apply` end to end — a change
written by Claude Code, 484 tests and the candidate check, swapped in,
healthy after 3 minutes, fast-forwarded into the checkout (f906934).
