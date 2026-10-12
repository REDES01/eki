# eki

A station on your Mac that runs AI agents for you. You ask in plain words; eki
routes the request to a local model or a rented agent (Claude Code, Codex), runs
it, and keeps it running through restarts. For bigger work it plans the change,
builds the pieces in parallel worktrees, checks them, merges them, and puts the
result live — including changes to eki itself.

Stdlib Python 3.11+, a plain HTML/JS window at http://127.0.0.1:7788 and a thin
native Mac shell. Apache 2.0.

## What it does

- **Ask** — one queue for local models (MLX, ComfyUI) and agents. eki picks who
  answers and says why (`eki route`), keeps threads, and survives restarts.
- **Projects made of cards** — a project is a folder plus the cards its agents
  share: instructions (AGENTS.md), knowledge documents, skills, tools and
  services (voice, ComfyUI picture workflows, Ableton over MCP), a media pool
  kept outside git, preview links (one server per worktree), a playtester agent
  that only sees the screen, and a **workflow** card that says how the project's
  work runs (router, planner, parallel, checks, merge).
- **Goals, built in parallel** — `eki self "…"` (or `-p <project>`) drafts the
  goal, plans it into items with the files each will touch, builds each item in
  its own copy-on-write worktree, checks it, and merges it:
  - **gate 1** — the item alone: the checks its changed files call for
    (per-path rules on the workflow card) and, for eki, a second reader;
  - **gate 2** — the stack of waiting items together, one batch at a time,
    bisecting to the item that broke it;
  - **conflicts** go to a resolve agent told what each side was for;
  - **the train** (eki only) builds, drills and swaps the running eki, with a
    way back.
- **Machine caps** — agents at once, test runs at once and browsers at once,
  set in Settings → Machine and applied on the next tick; checks go first.

## Quick start

```sh
bin/eki engine install            # always on (login agent); or: bin/eki open
bin/eki open                      # the window
bin/eki ask "write a haiku about snow"
bin/eki ask -C ~/games/rpg "fix the failing test"
bin/eki project                   # your projects; make one from a folder
bin/eki self -p <project> "…"     # a goal on a project: planned, built in parallel, merged
bin/eki self                      # goals, items and the queue
bin/eki answer                    # questions eki is waiting on you for
bin/eki machine --agents 8 --checks 6 --browsers 3
```

More commands: `eki -h`. Useful ones: `runs | follow | show | cancel`,
`use <card>` (call a project card), `component …`, `slot -- <cmd>` (run tests in a
machine test slot), `swap | builds` (eki's own releases), `drill` (prove a restart
loses nothing), `bin/check` (eki's tests, in parallel).

Design: [docs/design.md](docs/design.md) · how eki changes itself:
[docs/self-build.md](docs/self-build.md) · plan: [ROADMAP.md](ROADMAP.md).

## Where it stands (October 2026)

Rebuilt from zero in late September 2026; about 33k lines of Python, 10k of web
UI and 2,600 tests. In the two days to 2026-10-12 it ran 327 items across eki
itself and one game project (a story-driven JRPG): 289 landed (88%), 17 were
dropped on purpose, and the rest were in flight. The game got a full Chapter 1
polish pass (every NPC rewritten, a smaller Dry Day, partner skills, travel and
healing as one system) and a data-driven quest system with eight kinds of
objective, mostly without a person touching the code.

**What works well**
- Parallel building at volume: dozens of agents, each in its own worktree, with
  a merge queue that catches what two changes break together (gate 2 found real
  cross-item bugs the items' own checks passed).
- Nothing is kept in memory: a restart, a crash or a release mid-work loses
  nothing (`eki drill`).
- Per-project workflows: the game checks itself in seconds by what changed; eki
  checks itself with full tests, a reviewer and a release train.

**What doesn't, yet**
- **Too much still needs a person.** On a busy day a person cleared about twenty
  stuck items: tests that a new rule made stale, two changes that each passed
  alone but clashed, an agent that ended its turn to wait for a call and was
  never woken. eki should send the common cases back to an agent with the
  error before asking you. The fixer is built: before an item is left for you
  it gets one fixer run on the current head (docs/self-build.md, "The fixer"),
  and the Station counts fixed-by-fixer against left-for-you per day.
- **Machine resources are managed by counts, not by need.** Twenty headless game
  browsers at about 1 GB each filled memory and swap; caps came one by one
  (agents, test runs, browsers). The scheduler should admit work by memory and
  CPU budget.
- **Repository hygiene decides how parallel a project can be.** Generated files
  in git and single shared files (one tags table, one quest list) made every
  item conflict until they were split. The planner should warn about files many
  items will touch before fanning out.
- **One place to see everything.** The Station shows eki's own work; a project's
  work is on its page. (Being built: every project on the Station.)
- **Checks prove it builds, not that it's good.** Player-facing quality still
  needs the owner, or the playtester agent, which is heavy.

## Layout

```
eki/            the engine: scheduler, queue, gates, train, providers, projects, components
eki/web/        the window (no build step)
mac/            the native shell (bin/build-mac)
components/     eki's own cards (its workflow)
docs/           design and self-build
tests/          bin/check runs them
```
