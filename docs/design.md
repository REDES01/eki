# eki — design (rebuild, milestone 1)

eki keeps the Mac you own working for you, and rents frontier agents
(Claude Code, Codex) only for what the local models can't do. This is the
rebuild from zero. The old eki (`~/eki`) is the reference for hard-won
details, not a code source.

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
eki-next ask "…"  ──►  runs table (queued)
                          │
engine (launchd, one)  ───┤  every tick: reap → route → gate → spawn
                          ▼
worker (detached, one per run) ── runs the provider ── writes events
                          │
                          ▼
eki-next follow <run>  ◄── events table
```

| Module | Job |
|---|---|
| `paths` | where everything lives (`EKI_HOME`, default `~/.eki-next` while the old eki runs) |
| `db` | schema, WAL, busy timeout — the one source of truth |
| `threads`, `runs`, `events` | the data: a thread is a conversation, a run is one turn by one provider, events are what it said and did |
| `worker` | one detached process per run: starts the program, reads its output, writes events, records the session id so a resume is possible |
| `engine` | a manager, not a parent: reap dead workers (interrupted → queued for resume), route queued runs, check gates, spawn workers |
| `providers/*` | how to run each program and read what it prints: `claude_code`, `codex`, `local` (OpenAI-compatible, e.g. MLX), `fake` (tests) |
| `routing/*` | the prompt check (which row) and the table (where each row goes), each checkable on its own |
| `capacity` | can a provider take work right now: installed, not cooling down after a limit |
| `machine` | power, memory pressure, load — background work only runs when the Mac has room and is plugged in |
| `skills`, `mcp` | one store, handed to every program per run (Claude: `--plugin-dir`, `--mcp-config`; Codex: `~/.agents/skills`, `-c mcp_servers…`) |
| `cli/*` | one file per command |

## Runs

States: `queued → running → done | failed | cancelled | handed_off`, and
`running → interrupted → queued` when a worker dies without finishing.
A run carries its `attempt`; after 3 interruptions in a row it fails.
`priority` is `now` (you're waiting) or `background` (gated by the machine).

## Threads

A thread stays with the provider that answered it. It moves only when it
must: the provider hands off (the local model's one tool), you pick
another, it hit a limit, or it isn't available. A provider joining a thread
mid-way gets the transcript so far as context. Each provider keeps its own
session id per thread, so returning to it resumes its session.

## Routing

1. **Prompt check** → a row. Uses the local model when it's up (one short
   classification call, logged); otherwise the `general` row. `eki-next
   route explain "…"` shows the row and why.
2. **Table** → first target in the row that's available. Moving down the
   row is failover. The table is a JSON file you can read and edit
   (`~/.eki-next/routing.json`); defaults are written on first run.

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
  command can be started and stopped by eki (`eki-next models`, or the
  sidebar). With `keep_up`, the engine keeps it running while memory is
  normal and *steps it out* when memory comes under pressure and nothing
  is using it, coming back five minutes after pressure eases. A model you
  stop stays stopped until you start it. eki never stops a server it
  didn't start. A run sent to a stopped model starts it and waits.

## Not built yet

Goals / idle shift, self-build loop, image generation, learned preferences.
Each is built on the pieces above, not beside them.
