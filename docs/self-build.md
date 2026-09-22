# eki builds eki — how

Design for Stage 1 of the [roadmap](../ROADMAP.md). What's decided, what's
built, and what each remaining piece has to do.

## The shape

```
 request or fault
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
   copy), schedules switched off in the copy so nothing fires twice. It must
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

## Self-work runs  (not built)

- A run `kind` of `self` beside `ask` and `deploy`. Its folder is a fresh
  `git worktree` of eki's own repo on a branch `self/<run id>`.
- Requested by saying so ("eki, change yourself so that…" — a label in
  `classify.py`), by a fault (an exception whose traceback is inside
  `eki/`, with the traceback as the prompt's evidence), or by a schedule
  taking the next unchecked roadmap item.
- Routed like repo work, with one extra requirement: the agent must be one
  that can edit files. Pace and quota apply as to anything else — eki does
  not spend a nearly-gone window on itself.
- When the agent finishes, the engine runs the candidate check and appends
  the report. Autonomy decides what's next: *propose* stops here with the
  diff; *apply here* hands the path to the supervisor.

## Order of building

1. ~~candidate check~~ — done
2. ~~`builds/` layout + supervisor + `agent.py` pointing at `current`~~ — done
3. ~~`self` runs, *propose* only~~ — done (`eki self`)
4. ~~*apply here*~~, ~~protected paths~~ — done (`eki self --apply`,
   `self_autonomy`); the Self pane is not built
5. faults as requests; roadmap items as requests

Checked live on 2026-09-22: a healthy swap; a build that can't start rolled
back within a minute; Claude Code and Codex runs cut off by a restart
carried on in their sessions; and `eki self --apply` end to end — a change
written by Claude Code, 484 tests and the candidate check, swapped in,
healthy after 3 minutes, fast-forwarded into the checkout (f906934).
