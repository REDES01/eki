# eki — design (rebuild, milestone 1)

eki keeps the Mac you own working for you, and rents frontier agents
(Claude Code, Codex) only for what the local models can't do. This is the
rebuild from zero. The eki before the rebuild is kept, dated, in `~/eki-2026-09-26`
(data in `~/.eki-2026-09-26`): the reference for hard-won details, not a code source.

## Invariants — every change is checked against these

1. **A restart at any moment loses nothing.** The engine holds no state.
   Everything is in one SQLite file; the programs doing the work are
   detached *workers* that write straight to it. Killing the engine
   leaves every worker running. Killing a worker makes its run
   *interrupted*, never *failed*, and it is resumed in the same program
   session. A test (the drill) proves this on every change.
2. **eki integrates; it doesn't build tools or harnesses.** Models come
   from providers, harnesses from their makers, tools from MCP servers,
   instructions from skills. The one tool eki gives a model is *hand this
   thread to a harness*.
3. **Credentials stay yours.** Subscriptions are reached only by running
   the official CLIs as you. No token is read, copied or re-exposed.
4. **Everything is a run.** Written down before it starts, visible from
   the command line, explainable ("why did this go to Codex?").
5. **Small files.** No module over 400 lines (a test enforces it). Big
   files are what made parallel self-changes conflict in the old eki.
6. **Tests never touch your real home.** Every test runs in a temp
   `EKI_HOME`; the suite refuses to run otherwise.

## Pieces

```
eki ask "…"  ──►  runs table (queued)
                          │
engine (launchd, one)  ───┤  every tick: reap → route → gate → spawn
                          ▼
worker (detached, one per run) ── runs the provider ── writes events
                          │
                          ▼
eki follow <run>  ◄── events table
```

| Module | Job |
|---|---|
| `paths` | where everything lives (`EKI_HOME`, default `~/.eki`) |
| `db` | schema, WAL, busy timeout — the one source of truth |
| `threads`, `runs`, `events` | the data: a thread is a conversation, a run is one turn by one provider, events are what it said and did |
| `worker` | one detached process per run: starts the program, reads its output, writes events, records the session id so a resume is possible |
| `engine` | a manager, not a parent: reap dead workers (interrupted → queued for resume), route queued runs, check gates, spawn workers |
| `providers/*` | how to run each program and read what it prints: `claude_code`, `codex`, `local` (OpenAI-compatible, e.g. MLX), `fake` (tests), `command` (an argv in a folder, built in — a check or a build as a run) |
| `workspace` | git worktrees under `~/.eki/work/<key>` for work that must not collide; runs in one folder otherwise take turns |
| `routing/*` | the prompt check (which row) and the table (where each row goes), each checkable on its own |
| `capacity` | can a provider take work right now: installed, not cooling down after a limit |
| `machine` | power, memory pressure, load — background work only runs when the Mac has room and is plugged in |
| `skills`, `mcp` | one store, handed to every program per run (Claude: `--plugin-dir`, `--mcp-config`; Codex: `~/.agents/skills`, `-c mcp_servers…`) |
| `builds` | which eki runs: immutable exports under `~/.eki/builds/<id>`, `current`/`previous` links, the healthy mark, the sweep |
| `bin/eki-launcher` | what launchd runs: starts the engine from `current`, again after a swap, and goes back to `previous` if a new build dies before its watch window is up. Free of eki's code; eki never changes it alone |
| `selfwork`, `selfbrief` | eki builds eki (docs/self-build.md): goals, items, and the plan / build / judge runs that carry each item to a proposed branch; what the agents are told and how their answers are read |
| `integration` | the repo eki lands into (`~/.eki/self/repo`): `main` is fast-forwarded, pushed to origin, and the source checkout follows only when clean |
| `queue` | proposed items in order: speculative rebase onto each one's predicted head, gate 2, and landing the front on integration `main` |
| `rebase` | the git steps of the queue: rebase a branch onto its predicted head, carry on after a resolve, a fresh worktree for gate 2 |
| `resolve` | the queue's side path: a run resolves a conflicted rebase, eki checks it, runs gate 1 again and puts the item at the back |
| `candidate` | the train's own checks before a swap (gate 3): the checkout's engine boots, it opens and migrates a copy of the db, and the running build opens the copy |
| `locks` | the hard lock list: files eki may never change on its own say; an item touching one waits for a person's yes |
| `train` | integration `main` goes live every few minutes as a build; `settle` marks what it carried live or rolled back |
| `cli/*` | one file per command |

## Runs

States: `queued → running → done | failed | cancelled | handed_off`, and
`running → interrupted → queued` when a worker dies without finishing.
A run carries its `attempt`; after 3 interruptions in a row it fails.
`priority` is `now` (you're waiting) or `background` (gated by the machine).

## Threads

A thread stays with the provider that answered it. It moves only when it
must: the provider can't do what the next request needs (a picture for a
text-only model; tools, which the local model asks for with its one
`handoff` tool), you pick another, it hit a limit, or it isn't available.
A provider joining a thread mid-way gets the transcript so far as context.
Each provider keeps its own session id per thread, so returning to it
resumes its session.

## Routing

A request passes through five layers, in order; each adds its reason to the
run's `why` ("rule: names a path → code → claude (codex last: five_hour
82%)"). `eki route "…"` prints the chain; `routing.explain` returns it as
JSON.

1. **Thread.** A thread stays with its provider, unless that provider
   can't take the request ("local can't take it (needs vision)").
2. **Constraints** — pure code. What the request needs against what each
   provider can do, plus availability, cooldowns and quota. A provider
   that fails is skipped with the reason ("can't do images", "limit",
   "off"); an on-demand model that's off still counts, it starts for the
   run.
3. **Intent** picks the row. Rules (`eki/routing/rules.py`) take the
   obvious cases — a folder or a path → `code`, a picture → `vision`,
   "draw …" → `image` — logged as "rule: <reason>". Only when no rule
   fires does a small model classify. `checker_wakes` in `routing.json`
   (default true) decides whether an off checker is started for that.
4. **Preference.** The row's target order, bent by capacity: a target near
   its limit moves to the back.
5. **Failover** down the row.

When the check can't run (no checker, an off checker not woken, no row
given), the `general` row is used, cheapest first: local < subscriptions
(Claude Code, Codex) < API. The table is `~/.eki/routing.json`, written
with defaults on first run. Migration: an existing `routing.json` is left
as the person wrote it; new defaults (row `needs`, the `general` order)
only reach a fresh file.

**Capabilities.** Each provider declares `can` from {text, tools, web,
vision, image, image-edit}. Each kind has defaults (Claude Code: text,
tools, web, vision; Codex: text, tools, web; local: text; command: none);
an entry's own `can` in `providers.json` replaces them, and its optional
`about` is a sentence or two on what it's for. Rows declare `needs`
(default `text`; `code` needs tools, `web` needs web); a picture attached
adds `vision`. `eki providers`, `eki route` and the prompt check's menu
show the same tags and `about` the code enforces.

## Milestone 2: the window and the machine

- **Web UI served by the engine** (`eki/server.py`, `eki/api.py`,
  `eki/web/`) on `127.0.0.1:7788`. The files are read from disk on every
  request: a UI change is live on refresh. No build step, no framework. A
  POST needs the `X-Eki: 1` header, so only eki's own page can act.
  Nothing lives in the page that the engine doesn't have: refresh, or
  restart the engine mid-run, and the page picks up where it was.
- **A thin Mac shell** (`mac/main.swift`, built by `bin/build-mac`): a
  window and a menu bar item around the web UI; it starts the engine if
  it isn't up. It rarely needs to change.
- **Local models** (`eki/models.py`): a local provider with a `serve`
  command can be started and stopped by eki (`eki models`, or the
  sidebar). With `keep_up`, the engine keeps it running while memory is
  normal and *steps it out* when memory comes under pressure and nothing
  is using it, coming back five minutes after pressure eases. A model you
  stop comes back after the same pause (`--hold` keeps it down). Without
  `keep_up` a model is *on demand*: off until a run is sent its way — the
  worker starts it and waits, the prompt check does the same — and stopped
  again `idle_stop` minutes (5) after its last run — it tends to be off. eki never stops a
  server it didn't start.

## Going live

`eki engine install` puts the launcher (not the engine) under launchd and
points `builds/current` at this checkout: dev mode, edit and `eki engine
restart` as before. `eki swap <ref>` exports that commit into
`~/.eki/builds/<id>`, runs `bin/check` in it, and moves the links; the engine
sees a different `current` at its next tick and exits with the swap code;
the launcher starts the new one. Workers are untouched — each runs from the
folder it started in until its run ends. After `EKI_WATCH` seconds (180) up,
the engine marks its build healthy; if it dies before that, the launcher
flips back to `previous` and writes `rollback.json`. `eki swap --back` goes
back by hand, `eki swap --dev` returns to the checkout. The train runs the
full check plus the candidate checks on a build before it swaps to it, and
reverts the newest item when that check is red. The drill tries all of it in
a sandbox (`eki drill`, `drill.swaps`). The schema only grows
(`db.ADDED`), so a worker from an older build keeps writing to a file a
newer engine opened.

## Milestone 4: eki builds eki

Designed in [self-build.md](self-build.md): items with write-sets built in
parallel worktrees, a speculative merge queue, three judging gates, immutable
builds swapped by a launcher that rolls back. Built so far: `eki self "…"`
plans a goal into items, builds them side by side in worktrees of the source
repo, judges each with `bin/check` (gate 1), and proposes the fit ones as
branches `self/<id>`; the go-live path (`eki swap`). Next: the queue and
gate 2. Order of building in [ROADMAP.md](../ROADMAP.md).

## Not built yet

Goals / idle shift, image generation, learned preferences. Each is built on
the pieces above, not beside them.
