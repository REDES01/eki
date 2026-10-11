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
| `engine` | a manager, not a parent: reap dead workers (interrupted → queued for resume), route queued runs, check gates, spawn workers within the per-kind limits, with no overall cap unless `EKI_MAX_PARALLEL` is set |
| `providers/*` | how to run each program and read what it prints: `claude_code`, `codex`, `local` (OpenAI-compatible, e.g. MLX), `comfyui` (pictures, over ComfyUI's HTTP API), `codex_local` (Codex's harness on the local model, rung 2), `fake` (tests), `command` (an argv in a folder, built in — a check or a build as a run); `oneshot` (one call with no tools through the `claude` or `codex` CLI, for eki's own text jobs) |
| `workspace` | git worktrees under `~/.eki/work/<key>` for work that must not collide, made cheap: `git worktree add --no-checkout`, then `clonetree.fill`; `.venv`/`node_modules` linked from the source folder; runs in one folder otherwise take turns |
| `clonetree` | fills a `--no-checkout` worktree with APFS clones (`clonefile`) of the source checkout's tracked files, so no data is copied until a file changes; a file that differs from the base is checked out normally, the whole tree when clones aren't possible; byte-identical, `git status` clean |
| `deplinks` | `.venv`/`node_modules` stay links: one an item replaced with a real folder is put back before gate 1, and the board says `relinked …` |
| `worktreegc` | the housekeeping step `worktrees`: removes finished, unreferenced worktrees (and their branches) a few per pass; a failed item's kept 2 days; the disk worktrees use, for `eki self` |
| `routing/*` | the prompt check (which row) and the table (where each row goes), each checkable on its own |
| `targets` | one way to name where work goes, as a string (`provider`, `provider:model`, the old `provider:tier`) or an object `{provider, model?, effort?}`: `parse`, `fmt` (the shortest written form), `key` (a stable one-line dict key, `claude:opus@high`), `provider_of`, `label` (`claude · Opus 5.5 · high`), `choose` (model and effort against the read list, with a note when either falls back to the default), `resolve` (the old `(model, note)`), `problems`; pure |
| `roles` | eki's own jobs — `router`, `planner`, `reviewer`, `chores` — and the target each goes to: `get`, `default` (the old settings), `set_role`, `pick` (with the fallback and its why), `record`/`timings` (table `role_calls`) |
| `modellist` | each program's own model list (Claude Code's `initialize`, Codex's `model/list`, local's `/v1/models`) cached in `provider_models`: per model its label, `description`, `resolved` (the pinned model an alias stands for), `efforts`, `default_effort`, whether it is the default, and `floating` (an alias that follows the newest); no suggestions |
| `capacity` | can a provider take work right now: installed, not cooling down after a limit |
| `machine` | power, memory pressure, load — background work only runs when the Mac has room and is plugged in |
| `skills`, `mcp` | one store of what every run gets, handed to every program per run (Claude: `--plugin-dir`, `--mcp-config`; Codex: `~/.agents/skills`, `-c mcp_servers…`); a project's own skills are its `skills` card |
| `builds` | which eki runs: immutable exports under `~/.eki/builds/<id>`, `current`/`previous` links, the healthy mark, the sweep |
| `bin/eki-launcher` | what launchd runs: starts the engine from `current`, again after a swap, and goes back to `previous` if a new build dies before its watch window is up. Free of eki's code; eki never changes it alone |
| `selfwork`, `selfbrief` | eki builds eki (docs/self-build.md): goals, items, and the plan / build / judge runs that carry each item to a proposed branch; what the agents are told and how their answers are read |
| `selfpick` | the loop (docs/self-build.md): when eki is idle and the switch is on, open its own next goal — a fault, then the journal's costliest cluster, then the ROADMAP entry a model ranks first — each with its why |
| `costs` | the journal's costly clusters: handoffs on rows that could stay local, corrections by the row they corrected |
| `roadmap` | ROADMAP.md at integration `main` as open entries, each keyed by a hash of its text |
| `selfview` | the `eki self` board's words as data (stage, drafting, where, note, the whole board), shared by the CLI and the Station page |
| `api_models` | the Settings page's Models section: each provider's read list with the new fields (floating names first, then pinned ids, each in the program's order) and read time, each role's pick (`GET /api/models`), and a list's refresh (`POST /api/models/refresh`); no tiers or suggestions |
| `api_station`, `api_settings` | the web's Station and Settings: what the self board, builds, journal and digest show and the self actions, each the CLI's own function; reading, checking and writing `routing.json`/`providers.json` |
| `integration` | the repo eki lands into (`~/.eki/self/repo`): `main` is fast-forwarded, pushed to origin, and the source checkout follows only when clean |
| `queue` | proposed items in order: speculative rebase onto each one's predicted head, gate 2, and landing the front on integration `main` |
| `rebase` | the git steps of the queue: rebase a branch onto its predicted head, carry on after a resolve, a fresh worktree for gate 2 |
| `resolve` | the queue's side path: a run resolves a conflicted rebase, eki checks it, runs gate 1 again and puts the item at the back |
| `candidate` | the train's own checks before a swap (gate 3): the checkout's engine boots, it opens and migrates a copy of the db, and the running build opens the copy |
| `locks` | the hard lock list: files eki may never change on its own say; an item touching one waits for a person's yes |
| `train` | integration `main` goes live every few minutes as a build; `settle` marks what it carried live or rolled back |
| `attachments` | files sent with a request: made safe at intake (HEIC → JPEG, big pictures scaled down), and uploads from the window |
| `chores` | eki's own text jobs as runs on row `chore`, on whoever their role picks (the local model by default): the review, the digest's patch notes and triage, a fault's first brief, a PR comment's triage |
| `patchnotes` | the short digest page's rules: the areas and the path table, the rules-only page, the check of the model's notes; pure |
| `review`, `digestprose` | the second reader between gate 1 and the queue; the digest's patch notes and the long page's `## Triage` |
| `responses`, `responses_stream` | `POST /v1/responses`: OpenAI's Responses protocol in front of the local model's chat completions, for Codex |
| `gallery` | the pictures eki has drawn, newest first, from the store (`eki pictures`, the window's gallery) |
| `projectchats` | projects you work inside: a `projects` row or the built-in `eki` (`SELF`, no row); `folder_of`, `listing`, `adopt`, `create`, `set_folder`, `find`, `home_clone` |
| `projectqueue` | a local project's own queue (autonomy `apply`): its fit items rebased onto the predicted head of its branch, conflicts to a resolve run, gate 2 in batch with the project's check, then landed; one project per batch |
| `aftermerge` | the workflow card's `after_merge` after eki lands in the person's folder (with `npm install --include=dev` first when the landing changed `package.json` or a lockfile): a `command` run, one per project at a time, changes landing close together coalesced into one run after the last; a failure on the board and the goal and a needs-you notice, the landing kept |
| `projectland` | landing on a person's branch: `refs/heads/<branch>` moves forward only, the folder follows by `git merge --ff-only` when the branch is checked out there and no tracked file the change touches has local changes, else it waits naming the files; pure git, safe to call again |
| `github` | the only way to GitHub: one function per `gh` call (a subprocess, 60 s), plain `git push`/`git fetch` against origin, which path a project is on, the in-memory interval of the GitHub steps |
| `issues` | issues labelled `eki` in: a new one becomes a goal or a standing goal, a closed one drops the unfinished work |
| `prs` | pull requests out: push `eki/<id>`, open or reuse its PR, poll it — merged is applied, closed is dropped; close the PR of an item you drop |
| `prfollow` | your comments on an open PR: triage, the item back to `waiting` with the comment in its spec, push and answer in one line, up to the cap |
| `prmirror` | eki's own landed items as read-only PRs against `eki/landed` (off by default) |
| `notify` | pushes to your phone through ntfy: the notices table, the settings, the send and the housekeeping drain |
| `notifygoals` | which goals ended — self, standing and GitHub — as `goal done` notices |
| `threadkeep` | the chat list's housekeeping step: `backfill` marks old threads eki made for itself `by='eki'`, then purge, then settle and start title chores |
| `threadacts` | what you do to a chat: pin, unread, rename, move to a project (cwd follows, sessions dropped), group, archive, delete (cancel, hide; `purge` removes the rows once nothing is active) |
| `api_threads` | the chat list: eki's threads that need you, then your pinned chats, then the rest; `all`, `archived`; each entry's pin, unread, own, title_by, group, sessions; `groups` |
| `titles` | a `title` chore per new chat of yours with an answer, once; the cleaned title is written only while the title is still the prompt's |
| `apiroutes` | the window's newer API as a table: `ROUTES` is each page's `api_*.ROUTES` joined, entries `(method, regex, fn)` with `fn(conn, m, q, body)`; `server.py` asks `match(method, path)` before its 404 |
| `api_artifacts`, `api_schedule`, `api_customize` | the Artifacts, Scheduled and Customize pages over HTTP, each only through its own `ROUTES` (`api_projects.ROUTES` holds the projects page's newer routes) |
| `artifacts` | what your chats made that is still on disk — pictures, pages, documents — newest first, from the store's `tool` events with a path |
| `schedule` | prompts sent on a clock (table `schedules`): `next_fire` in the schedule's zone, and the housekeeping `tick` that fires a due one once — a Mac asleep through several firings runs one on wake |
| `skillsfind` | skills you could add, read from GitHub (`anthropics/skills`) through `gh`, cached a day |
| `connectors` | MCP servers you could add: a fixed catalog, each with the keys it needs, installed through `mcp.add` |
| `components/*` | pieces any agent can use ("Components" below): `find` (the folders reachable from a run's folder, pool vs project, a broken one reported and skipped), `kinds` (the registry and each kind's checks), `access` (queue, exclusive, memory, conflicts, idle stop, from the runs table), `service`, `servers` (a `serve` kept up on demand, one per card and served folder), `serve` (the serve kind: a link per folder, `for_run`), `servebuild` (a serve card's `build` before serving, when the folder changed since the last one), `static` (eki's own static file server), `ports` (one recorded port per served folder), `assets`, `agent` (and the bare mode), `skillcard` (a project's own skills), `journal` (a playtester session's notes, emotions, review, screenshots), `uses` (a component's calls, last use and waiting count, read from the runs table), `mcphold` (`eki mcp-hold`: a service card's MCP server held like a script call), `cardmerge` (`eki component merge`: cards folded into one) |
| `components/knowledge`, `components/mcpserver` | the passive kinds: a project's shared documents (`docs`, `files`, `brief`; `knowledgedocs` reads the one-doc and many-doc shapes) and its own MCP servers (`public`, `servers_of`, `run_config`, which `worker.build_turn` hands each run) |
| `api_cardknowledge`, `api_cardmcp` | a knowledge card's documents and an mcp card's (or a service card's) server and Test on the project page (with `web/cardknowledge.js`, `web/cardmcp.js`, registered in `window.projectCardKinds`) |
| `docs`, `api_doc` | a local text file in the side panel: the policy (under the person's home by real path, never eki's internals or a credential folder/file, edit only .md/.markdown/.txt, 2 MB), read with who last wrote it, an atomic save over the hash it was loaded from (else a conflict), and the `eki open` queue (`doc_open` events the window polls) |
| `docsearch`, `api_docsearch` | the window's "Open file…" search: a project's markdown files, or the projects' folders when no project is open |
| `providers/component` | a component call as a run: `eki use <name> [args…]` goes through the engine like any other run |
| `api_components` | the Settings page's read-only Components section: the pool and each project's, one line each, details on click |
| `api_cards` | the project page's Cards area, read side: a project's cards (its own components/ entries, state and last use, an instructions card's summary), the suggestion to add an instructions card, and the library (templates and the pool); the helpers the card leaves share |
| `api_cardadd`, `api_cardedit`, `api_cardrun`, `api_cardsessions` | a card added (template or pool card) and removed (to the Trash); its settings and prompt edited; run through the engine and its uses; an agent card's sessions |
| `api_cardinstructions` | an instructions card's file saved, CLAUDE.md linked to it (`@AGENTS.md`), and a "Read first" file read (inside the project folder only) |
| `cli/*` | one file per command |

**Scheduling.** The engine's spawn step holds each kind of run only by the
limit that already exists for it. Checks — gate 1, gate 2, the train's
check, the docs check, a project's own check — wait for a check slot
(`checkslots.is_judge`, `checkslots.limit()` = `self.check_slots`, default
2). Items being built are held by `self.parallel` in
`scheduler.start_ready` before a build run exists; the engine doesn't count
build runs, and a waiting item's `items.why` says so ("4 building
(self.parallel)"). The local model's own server queues its requests, so the
engine adds no limit for it. Everything else — chats, drafts, plans,
reviews, chores, other command runs — is a light agent turn and isn't held
by how many other runs are going. Always: one run at a time per thread, one
per folder, and `priority == "background"` runs wait for `machine.room`.
`EKI_MAX_PARALLEL` is an optional overall ceiling, off by default:
`engine.ceiling()` reads it on every call (unset, empty, not a number or
below 1 means no cap), and when set it caps all `starting`/`running` runs
of every kind, checks included; the held runs stay `queued` and start in
order as slots free up.

**Worktrees cost almost no disk.** A new worktree — an item's, a plan's or
draft's, gate 2's, the train's, eki's own or a project's — is made with
`git worktree add --no-checkout`, then `clonetree.fill` clones the source
checkout's tracked files into it with APFS `clonefile`: no data is copied
until a file changes. A file in the source checkout that differs from the
base is checked out normally; when clones aren't possible (not APFS,
`clonefile` fails) the whole tree is checked out the usual way. Either way
the result is byte-identical to a normal checkout and `git status` is
clean. `.venv` and `node_modules` are links to the source folder; agents
are told never to install inside a worktree, and one an item replaced with
a real folder is put back before gate 1 (`deplinks`, the board says
`relinked …`). A changed `package.json` or lockfile is fine: the
after-merge run installs (`npm install --include=dev`) in your folder
before the build. Housekeeping's `worktrees` step (`worktreegc`) removes,
5 per pass, the worktrees of finished, unreferenced work; a failed item's
stays 2 days for you to inspect (docs/self-build.md, "Worktrees"). Nothing
is held in memory, so a restart mid-cleanup loses nothing.

**The window's pages.** The sidebar opens with **New** (a new chat, ⌘K),
**Projects** (`#projects`, a card per project), **Artifacts** (`#artifacts`,
what your chats made; the old `#pictures` is Artifacts showing pictures),
**Scheduled** (`#scheduled`, standing goals and prompts on a clock) and
**Customize** (`#customize/<tab>`, skills and connectors). They share one
look (`eki/web/pages.css`), and each folds detail: a card or a row shows a
line or two, the rest opens on click. Below the chat list a small row holds
**Station · Settings** and a status dot (green when every provider is ok,
amber otherwise, and `n running`); a click opens the providers, quota
meters, engine line, PRs and digest in a popover (`sidestatus.js`).

## Runs

States: `queued → running → done | failed | cancelled | handed_off`, and
`running → interrupted → queued` when a worker dies without finishing.
A run carries its `attempt`; after 3 interruptions in a row it fails.
`priority` is `now` (you're waiting) or `background` (gated by the machine).
A `fresh` run starts a new program session and resumes only its own (after
a restart cut it off), never the thread's earlier one; resolve runs and
chores are fresh, so they get their context from the brief alone.

**Chores** (`eki/chores.py`, table `chores`) are text jobs eki gives
itself: the review between gate 1 and the queue, the digest's patch notes
and triage, the first draft of a fault item's spec (docs/self-build.md),
and whether a comment on a pull request asks for a change (`prcomment`,
subject `<item id>:<ISO time>`). Each is an ordinary run on row `chore`, in
a thread of its own (`chore: <kind> <subject>`, no folder), pinned to the
provider and model its role picks — `reviewer` for the review, `chores` for
the rest — with the pick's why on the run. No provider (the role is `"off"`,
or neither its target nor its default can run) means a `skipped` chore with
the why as its result, and the caller goes on without it. A chore reaches
Claude or Codex only when you set its role there, and then with no tools:
on row `chore` their `take` makes one call through `providers/oneshot` and
emits its text as one event — no tools, no session, no handoff. The local
model is given no `handoff` tool on this row, and a chore run that hands
off anyway fails with the reason and makes no follow-up. The chore's row
(`kind`, `subject`, `run_id`, `state` open → done | failed | skipped, the
parsed `result`) and its run are all the state there is: the caller finds
finished chores with `chores.finished` and moves each on once under
`db.tx`, so a restart mid-chore loses nothing.

**Roles** (`eki/roles.py`) say who does each of eki's own jobs: `router`
(the prompt check and narrow), `planner` (draft and plan runs), `reviewer`
(the review chore) and `chores` (digest prose, fault briefs, PR-comment
triage). The setting is `routing.json` `"roles": {"router": "claude:fast",
…}`, holding only the roles you set, each a target (see Routing). A role
not set reads the old settings, which are still read and never rewritten
or deleted: router the top-level `checker`, else `local`; planner
`self.planner` `{provider, model}` as `provider:model`, else the `code`
row's first target; reviewer and chores `self.local`, else the first
provider of kind `local`. So with no `roles` key nothing changes. `"off"`
is allowed for reviewer and chores. `eki roles` lists each role's target
and what it comes to (`router claude:haiku → claude · Haiku 4.5 · low`),
whether it's the default and any fallback why; `eki roles router claude
haiku`, `eki roles planner claude opus --effort high` or `eki roles router
claude:fast` checks and sets one (written in `fmt`'s shortest form), `eki
roles router default` removes it. A pick carries the effort too, and the
runs a role starts (chores, reviews, plans) record it.

`roles.pick` is recomputed from the settings at every use. A target can
run when its provider exists, isn't off and is available
(`capacity.status`); for router, reviewer and chores the provider must also
answer a one-shot call (`complete()`: kinds `local`, `claude_code`,
`codex`), for the planner it must have tools, and for a background pick it
must be within the background budget. A chore or review on a subscription
over that budget falls back at once rather than waiting. A target that
can't run falls back to the role's default, and the why says so ("router
claude:fast can't run (claude: limit) → local"); when the default can't
run either the pick has no provider — the router then uses `general`, a
chore is `skipped`. Each call made for the router is timed into
`role_calls`, and `eki route` shows the median per target, so a fast
subscription model and a sleeping local one can be compared.

## Threads

A thread stays with the provider that answered it. It moves only when it
must: the provider can't do what the next request needs (a picture for a
text-only model; tools, which the local model asks for with its one
`handoff` tool), you pick another, it hit a limit, or it isn't available.
A provider joining a thread mid-way gets the transcript so far as context.
Each provider keeps its own session id per thread, so returning to it
resumes its session.

The chat list shows your chats (`threads.by` NULL). Threads eki makes for
itself — chores, drafts and plans, gate 2, train checks, clones — carry
`by='eki'` and show only while they hold an open ask, or with `all`/`--all`;
`threadkeep` marks the ones from before the column. A pinned chat
(`pinned_at`) comes first and is kept beyond the limit. A chat is unread when
you marked it so (`unread=1`) or a run ended after `seen_at`; a NULL
`seen_at` (an older row) reads as seen. An archived chat leaves the list and
a new message in it unarchives it. Your groups (`chat_groups`) are sections
of their own. Delete sets `deleted_at`, which hides the chat everywhere at
once, and cancels its active runs; `threadacts.purge` removes its rows once
nothing in it is active. Files, worktrees, branches and attachments are
never touched, and eki's own threads can't be deleted — drop their work from
the Station. Moving a chat to a project sets its cwd to the project's folder;
when the folder changes its sessions are dropped, so the next turn starts
fresh with the whole transcript, as after a handoff. New chats are titled
from the prompt (`title_by='prompt'`) and then by a `title` chore, once; a
title you set (`'you'`) always wins, and chats from before (`'old'`) are
never retitled. A chat a scheduled prompt started carries the schedule's id in
`threads.schedule`.

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
   fires does a small model classify: the `router` role's pick (see
   Runs → Roles; by default the old `checker`, else `local`), asked with
   the model its target resolves to. A router target that can't run falls
   back to the default with its why in the route's why.
   `checker_wakes` in `routing.json` (default true) decides whether an off
   on-demand local router is started for that.
   A request that lands on `code` (by rule or check), while a `code-easy`
   row exists, is asked one narrower question with a menu of just `code`
   and `code-easy` ("a small, well-defined change: rename, a flag, a typo,
   one test"): "rule: has a folder → code; check: easy → code-easy →
   codex-local". If that check can't run the request stays on `code` — easy
   is never guessed — and a run whose row was set beforehand (self-work's
   builds) is never narrowed. `eki route` and `routing.explain` show the
   step.
4. **Preference.** The row's target order, bent by capacity: a target near
   its limit moves to the back.
5. **Failover** down the row.

When the check can't run (no router can run, an off one not woken, no row
given), the `general` row is used, cheapest first: local < subscriptions
(Claude Code, Codex) < API. The table is `~/.eki/routing.json`, written
with defaults on first run. Migration: an existing `routing.json` is left
as the person wrote it; new defaults (row `needs`, the `general` order)
only reach a fresh file.

**Targets** (`eki/targets.py`). A row's targets, a role and a pin all
name where work goes the same way: `provider`, `provider:model`, or an
object `{"provider", "model"?, "effort"?}` when it carries an effort.
`claude:opus` and `claude:claude-opus-4-8` both work; everything after the
first colon is the model (`local:qwen3:32b`), and a bare `provider` means
the provider's own default. `fmt` writes the shortest form: a string when
there is no effort (`"claude"`, `"claude:opus"`), an object with only the
keys that are set when there is (`{"provider": "claude", "model": "opus",
"effort": "high"}`). `key` (`claude:opus@high`) is only a dict key and the
`role_calls.target` column; it is never parsed back. Rows may mix targets
(`answer → ["local", "claude:haiku"]`, the same provider twice with
different models); needs, capacity and `excluded` go by the provider.
`comfyui:<workflow>` is a target like `claude:<model>`: `comfyui:hq` names
the workflow `hq` in ComfyUI's list.

`choose` turns a target into a model and an effort against the provider's
read list (`modellist.listed()`); a list never read is trusted as written.
Anything the program no longer offers falls back to its default, and the
note says so in one of three ways: "<m> isn't in <p>'s list any more; its
default", "<p> <model> doesn't offer effort <e> any more; its default", or
"<model> takes no effort" (a model whose `efforts` is NULL). The decision
carries both, the why names the target with `label` and appends the note
("→ code → claude · Opus 5.5 · high"); the run records them as
`runs.model` and `runs.effort` unless you pinned your own, which win. An
effort written by an earlier route is routing's to change. The run's
effort travels as `turn.extra["effort"]` and reaches each program its own
way: Claude Code `--effort <level>` (sessions and one-shot calls alike),
Codex `effort` on every `turn/start` (per turn, so a resumed thread gets it
each time), one-shot `codex exec` `-c model_reasoning_effort="<level>"`;
local and codex_local ignore it. The run page's route line shows the
label, amber when the why carries a fallback note. `eki route` shows each
row target's label, effort and note, and the roles block above the table;
`eki roles ROLE PROVIDER [MODEL] [--effort LEVEL]` sets a role and, when
the list was read, refuses a model or an effort the program doesn't offer.

**Capabilities.** Each provider declares `can` from {text, tools, web,
vision, image, image-edit}. Each kind has defaults (Claude Code: text,
tools, web, vision; Codex: text, tools, web, vision; local: text;
comfyui: image, image-edit; codex_local: text, tools; command: none);
an entry's own `can` in `providers.json` replaces them, and its optional
`about` is a sentence or two on what it's for. Rows declare `needs`
(default `text`; `code` needs tools, `web` needs web); a picture attached
adds `vision`. `eki providers`, `eki route` and the prompt check's menu
show the same tags and `about` the code enforces.

**Model lists, and the old tiers.** The choices come from what each
program itself reports (`eki/modellist.py`): Claude Code's `initialize`
control response (`models`, over `claude --input-format stream-json
--output-format stream-json`: `value`, `displayName`, `description`,
`resolvedModel`, `supportedEffortLevels` when `supportsEffort`), Codex's
`model/list` over `codex app-server` (`id`, `displayName`, `description`,
`supportedReasoningEfforts`, `defaultReasoningEffort`, `isDefault`), a
local server's `GET /v1/models` (ids only, no efforts). eki never reads a
token, calls a vendor API of its own, scrapes a page, or sends a model any
text to learn this. A list is cached in `provider_models` (one provider's
rows replaced in one transaction), read on demand (`eki providers models
--refresh [name]`, Settings' Refresh) and by housekeeping when it is over a
day old and the program is installed — on a thread with a 20 s timeout,
never blocking the engine. A model is `floating` when it is an alias whose
`resolved` model differs from it, or is `default`: it follows the newest.
`eki providers models` lists each model's label, description, efforts (the
default marked) and "follows the newest" for floating ones. Settings has
one picker (`eki/web/settingstarget.js`) for every row target and role:
provider, then model ("its default", floating names, pinned ids, each with
the program's description), then effort when the model offers any. ComfyUI's
models are its workflows, read from `workflows.json` rather than asked of
the program: ComfyUI → "FLUX.2 klein — draft" reads like Claude → Opus, with
the workflow's models, steps and size as its description and no effort. A
saved
choice no longer offered shows in amber with `choose`'s wording. It writes
`fmt`'s shortest form, saved through `/api/settings/routing`.

Tiers (`fast`, `default`, `top`) are still read, never set. A
`providers.json` entry's `"models": {"fast": "…", "default": "…", "top":
"…"}` (and the old `model`, read as `default`) still resolves a
`provider:tier` string, or an object whose `model` is a tier name; a tier
nobody picked resolves to the provider's default ("claude has no fast
model picked; its default"). Nothing writes tiers any more: the `eki
providers tier` command, the suggestions and the Settings tier editor are
gone.

**One-shot calls** (`eki/providers/oneshot.py`). Claude Code and Codex can
serve the text-only roles (router, reviewer, chores) with one call and no
tools, through their official CLIs. `ClaudeCode.complete` and
`Codex.complete` share `Local.complete`'s signature (`messages`, `model=`
…): the system message becomes the system prompt and the rest goes on
stdin. Claude runs `claude -p --output-format json --tools ""
--strict-mcp-config --no-session-persistence --system-prompt …` (plus
`--model` when one is named) and answers with the result JSON's `result`.
Codex runs `codex exec --json --sandbox read-only --skip-git-repo-check
--ephemeral -c developer_instructions=…` (plus `-m`) in a fresh empty temp
folder and answers with the last `agent_message`. `codex exec` has no
switch that removes its shell, so a read-only sandbox in an empty folder
plus the instruction to answer from the request alone is the closest it
comes to "no tools". A timeout kills the process, a non-zero exit raises,
and a limit message raises `Limited`, which the caller turns into a limit.

**Picture rows.** `image` (a quick draft), `image-hq`, `image-anime`
(need `image`), `image-edit` and `image-upscale` (need `image-edit`) each
pick a ComfyUI workflow through the same picker as any row: by default
`comfyui:draft`, `comfyui:hq`, `comfyui:anime`, `comfyui:edit` and
`comfyui:upscale`. A bare `comfyui` target (an older table) uses the
row's default workflow. `KIND_ORDER` counts ComfyUI as cheap as `local`. A
table without these rows gets them in memory from `table.rows()`, aimed at
whichever providers can do what they need; `routing.json` isn't rewritten.
A workflow no longer in the list falls back to the row's default, with
`choose`'s note. The why reads "rule: asks for a picture → image → ComfyUI
· FLUX.2 klein — draft".

**Rung 2: Codex on the local model** (`eki/providers/codex_local.py`, kind
`codex_local`, can text and tools). The same `codex app-server` as the Codex
provider, told by `-c` flags to use a model provider of eki's own —
`eki_local`, `wire_api="responses"`, base URL
`http://127.0.0.1:<server port>/v1`, `http_headers={"X-Eki"="1"}`, the
entry's `context_tokens` as `model_context_window`, and a raised
`stream_max_retries` so an engine restart mid-stream costs a retry — and
`-m <the local model id>`. Entry: `{"kind": "codex_local", "local": "<local
provider>", "context_tokens": 32768, "handoff_after": 20}`. When Codex is
installed and a local entry exists, a default `codex-local` joins
`providers.config()` in memory only (like comfyui); an entry you write wins.
It is the first try, not the last word: the model is told to end with
`HANDOFF: <why>` if the job is beyond it, and the run ends `handed_off` on
that line, on a failed turn, or after `handoff_after` minutes (the turn is
interrupted first). `worker.finish` makes the follow-up on row `code`
without this provider, so Claude takes it with the transcript; the files
are left as they are and the reason says so. `KIND_ORDER` counts it as cheap
as `local`; its finished runs count as local in the score; `models.in_use`
counts its running runs against its local model, so `idle_stop` never stops
the model mid-turn. The row `code-easy` (needs tools; targets `codex-local`,
`claude`, `codex`) is added by `table.rows()` in memory when a `codex_local`
provider exists; `routing.json` isn't rewritten.

**The Responses adapter** (`eki/responses.py`, `eki/responses_stream.py`).
Codex speaks only OpenAI's Responses API (it refuses `wire_api = "chat"`);
mlx_lm serves only chat completions. The engine's server mounts
`POST /v1/responses` — behind `X-Eki: 1` like every POST — which starts an
on-demand model first (`models.ensure`), turns the request (`instructions`,
`message`, `function_call` and `function_call_output` items, function
tools, `stream`) into one chat-completions request to the local entry's
`base_url`, and turns the chat stream back into the Responses events Codex
reads (`response.created`, `output_item.added`/`.done`,
`output_text.delta`, `function_call_arguments.delta`, `response.completed`
with usage), stripping `<think>…</think>` across chunk boundaries. It holds
no state — Codex sends the whole input, so `previous_response_id` is a 400
— and it translates the protocol only: the tools, the loop and the sandbox
are Codex's.

## Milestone 2: the window and the machine

- **Web UI served by the engine** (`eki/server.py`, `eki/api.py`,
  `eki/web/`) on `127.0.0.1:7788`. The files are read from disk on every
  request: a UI change is live on refresh. No build step, no framework. A
  POST needs the `X-Eki: 1` header, so only eki's own page can act.
  Nothing lives in the page that the engine doesn't have: refresh, or
  restart the engine mid-run, and the page picks up where it was.
  One page, views on hash addresses (`eki/web/nav.js` sends each to its
  view; a refresh reopens it): **Chat** (`#<thread>`, a reading column that grows with the window
  up to 860px, where a long code line scrolls inside its own block,
  replies with the provider's mark and the route's why, a floating
  composer with Send/Stop), **Pictures** (`#pictures`), **Station**
  (`#station`) and **Settings** (`#settings`, `#settings/<section>`).
  - *Station* (`eki/web/station.js`, `eki/api_station.py`) shows what
    `eki self`, `eki builds`, `eki route`, `eki observe` and the digest
    show, reloading every 5 s, and acts through the same functions the CLI
    calls: a wish (`selfwork.submit`), apply/drop/retry an item
    (`queue.apply`, a locked one only with `yes`; `selfwork.drop`,
    `selfwork.retry`), release (`train.release`), autonomy
    (`queue.set_autonomy`), undo a build judged worse (`score.undo`), write
    the digest now (`digest.write`). Open questions of self runs are
    answered in place. The board's words come from `eki/selfview.py`, so the
    page and `eki self` say the same thing.
  - *Settings* (`eki/web/settings.js`, `eki/api_settings.py`) edits
    `routing.json` and `providers.json` with forms or raw JSON, laid out
    like a Mac settings window: a list of sections down the left next to
    one body, each section at its own address — **Jobs**
    (`#settings/jobs`, the four role pickers and the on-demand checker
    switch; plain `#settings` opens it), **Rows** (`#settings/rows`),
    **Providers** (`#settings/providers`, the entries and the built-in
    list), **Notifications** (`#settings/notifications`) and **eki self**
    (`#settings/self`, autonomy and parallel); a refresh reopens the same
    section. `settings.js` is only the frame: each section is a file of
    its own (`settingsmodels.js`, `settingsrows.js`,
    `settingsproviders.js`, `settingsnotify.js`, `settingsself.js`) that
    registers with `settings.section(name, {title, file, html, edit,
    act})` and names the one file it edits; Form/Raw JSON and Reset act on
    that file. There is no Save button: a change saves itself as it is
    made (selects, checkboxes and buttons at once, text after a 600 ms
    pause or on blur, raw JSON only once it parses), through the same
    POST, one save per file at a time (a change made meanwhile goes when
    it returns, with the mtime it gave). A good save doesn't redraw, so
    focus and the open fold stay; a refusal is shown, "Not saved yet",
    and the draft is kept for the next change to try again; a 409 pauses
    saving that file and asks to reload it. A section file calls `settings.section` only when it
    is a function, else it pushes onto `window.settingsSections` (drained
    by `settings.js`), because the sidebar link `#settings` makes
    `window.settings` an element until `settings.js` loads. There is one draft per file, so Jobs and Rows
    edit the same routing.json, and changing section never drops a draft.
    Settings sit in rounded groups of one-line settings — the label (and a
    dim sentence) on the left, the control on the right
    (`settingsform.js`). Rows and providers fold to one summary line each
    (a row's key, title and target chain as labels joined by " → ", from
    `settingsTarget.label`, which is `targets.label`); clicking one opens
    its editor and closes any other, so one is open at a time, and which
    one is page state, not in the address. An open editor is the same
    one-line settings, in titled runs (`settingsForm.lines`), with long
    controls (examples, targets) under their label (`block`) and Delete in
    red at the bottom (`danger`). Rows and
    the ComfyUI entry that eki only adds in memory are shown as such and
    written only when edited. The server checks the data before writing
    (unique row keys, known targets, needs and `can` within the abilities,
    a `general` row, a known autonomy and kind, no `command` entry; every
    problem listed in a 400), keeps the old file as `<name>.json.bak`,
    writes atomically (tmp + `os.replace`) and refuses a file whose mtime
    changed since it was read with 409. Nothing reloads: the next decision
    reads the new file. Its Models section (`eki/web/settingsmodels.js`,
    `eki/api_models.py`) shows one line per provider ("N models, read
    <when> · Refresh") and each role's picker; every row target and role
    uses the same provider → model → effort picker
    (`eki/web/settingstarget.js`, see Routing), plus "default" and "off"
    where allowed, with any fallback in amber; it saves through the same
    settings writes.
  - Endpoints: GET `/api/station`, `/api/builds`,
    `/api/journal?since=&kind=`, `/api/digests`,
    `/api/settings/(routing|providers)`, `/api/models`; POST `/api/self`,
    `/api/self/items/<id>/(apply|drop|retry)`, `/api/self/release`,
    `/api/self/autonomy`, `/api/builds/<id>/undo`, `/api/digests/write`,
    `/api/settings/<name>`, `/api/models/refresh`. An unknown id is 404, a refused action 400 with
    the reason. `/api/route` serves the table and a request's explain.
  - The web's JS and CSS files are held to 400 lines too (the size test),
    so the page is split by job: `nav.js`, `split.js`, `side.js`, `app.js`,
    one file per view, `style.css` + `chat.css` + one stylesheet per view.
    The sidebar's edge is dragged to resize it (200–480px, double-click
    resets), and the width is kept in `localStorage` as `eki.side.w`. The
    sidebar's chat list is `sidelist.js` (eki's threads that need you,
    Pinned, your groups, projects, dates, an unread dot, Archived (n));
    `side.js` loads it, and `app.js` marks a chat seen when you open it or
    its run ends while the page is visible.
- **A thin Mac shell** (`mac/main.swift`, built by `bin/build-mac`): a
  window and a menu bar item around the web UI; it starts the engine if
  it isn't up. It rarely needs to change. View → Zoom In/Out/Actual Size
  (⌘+ ⌘− ⌘0, and ⌘= too) sets the web view's `pageZoom`, saved as
  `eki.zoom` in UserDefaults; the shell sets `--eki-zoom` on the page so
  the room for the window buttons stays the same on screen. The page
  reaches the shell through `eki/web/shell.js` (`window.ekiShell`):
  `pickFolder()` posts `{pickFolder: id}` and the shell answers with a
  folder panel through `ekiShell.picked(id, path)`; outside the shell
  `canPickFolder()` is false and the page works without it. `bin/build-mac`
  writes the commit the app was built from into Info.plist (`EkiCommit`).
  Once a healthy live build changes `mac/` or `bin/build-mac` relative to
  the installed app's `EkiCommit` (`eki/shellbuild.py`), eki rebuilds
  `Eki.app` as a background `provider='command'` run and swaps it in; an
  open window offers **Restart** and never restarts by itself
  (docs/self-build.md, "Replacing the running engine"). In dev mode (no
  build commit) you still run `bin/build-mac` by hand.
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
- **Pictures in** (`eki/attachments.py`). `eki ask --image a.png "…"`, or
  in the window paste, drop, or the 📎 picker; each picture is uploaded
  at once (`POST /api/attachments`, raw body plus `X-Filename`, saved as
  `~/.eki/attachments/<date>/<id><ext>`; not a picture extension or over
  20 MB → 400) and shown in a strip above the textarea until sent. At
  intake (`asking.submit`) a HEIC becomes a JPEG and a picture over 5 MB is
  scaled down (`sips -Z 2000`) into `~/.eki/attachments/<date>/`; the
  original is never changed. A file that isn't a picture passes through as
  a path in the prompt. The run's pictures are `Turn.images` and reach the
  program as it expects them: an image block in Claude Code's stream-json
  message, a `localImage` item in Codex's `turn/start`, an `image_url`
  data URL for a local model whose `can` has `vision` (none otherwise). A
  run that hands off passes its attachments on; history given to a
  provider joining mid-way marks a past picture as `[picture: <path>]`; a
  carry-on resume sends none, the session has them. `/api/file` serves a
  path any run was given, and the thread shows them as thumbnails.
- **Pictures** (`eki/providers/comfyui.py`, `comfyui_workflows.py`,
  `eki/gallery.py`): ComfyUI is a provider kind (`comfyui`, can `image` and
  `image-edit`). When `~/flux/ComfyUI` (or `EKI_COMFYUI_DIR`) holds a
  `main.py` and providers.json has no `comfyui` entry, the default one joins
  the file's entries in memory — the file is never written; an entry you
  write, even `{"off": true}`, wins. It is a managed server like the local
  model (`models.MANAGED_KINDS`): started on demand by the run, stopped
  `idle_stop` minutes after its last run, stepped out under memory pressure
  when nothing uses it, listed and started/stopped by `eki models` and the
  sidebar; a ComfyUI eki didn't start is never stopped. The entry keeps
  what it had: base URL, start script, on demand, idle stop, memory.
  Rules pick the row from the words (upscale, edit an attached picture,
  anime, quality words, "draw …"); a follow-up that isn't a question, in a
  thread whose last picture is known, edits it — the thread's last picture
  (`store.last_picture`, drawn or attached) is the source, uploaded to
  ComfyUI. A comfyui thread never stays by "thread": the rule's row picks
  the workflow, and a text question leaves it for a text model. Everything
  drawn stays in `~/.eki/images/<run>/` and is browsable: `eki pictures`,
  `GET /api/pictures?limit=&before=`, and the window's Pictures view (a
  grid, a picture opens in the side panel).
- **ComfyUI's workflows are its models** (`eki/providers/comfyui_workflows.py`,
  `comfyui_read.py`; it replaces `comfyui_graphs.py`). The list is
  `~/.eki/comfyui/workflows.json` — `{"workflows": [{"id", "label", "file",
  "inputs", "defaults"}]}` — and the graphs are plain files in
  `~/.eki/comfyui/workflows/`, ComfyUI's API format. The first time there is
  no list, eki seeds it once from its five shipped graphs: `draft` "FLUX.2
  klein — draft" (FLUX.2-klein 4B, 4 steps), `hq` "Qwen-Image 2.1 — high
  quality" (Q8 GGUF, 30 steps), `anime` "NoobAI-XL — anime", `edit` "klein
  — edit" (klein 4B shown the source) and `upscale` "klein — upscale" (2×,
  then a light klein pass). After that the list is the person's; eki never
  seeds again. A workflow's inputs are what an agent may set: each
  `{{name}}` placeholder in the JSON is one (`{{prompt}}`, `{{negative}}`,
  `{{seed}}`, `{{width}}`, `{{height}}`, `{{source}}` …), and an entry's
  `inputs` may also name a node id + field (`{"name": "steps", "node": "9",
  "field": "steps"}`) for a graph with no placeholders. `defaults` fills
  what a call leaves out. From the JSON eki reads, never writes: the models
  it loads (each checked against ComfyUI's model folders: there or
  missing), the sampler and its steps, and the size. `eki providers models
  comfyui` and `/api/models` list the workflows as ComfyUI's models, with
  those facts as the description. The old entry keys `"graphs"` and
  `"workflow"` are gone; a workflow is chosen by its id.
- **Settings → Providers → ComfyUI** lists its workflows the way a model
  provider lists its models (`eki/web/settingscomfyui.js`): each with its
  label, file, inputs and defaults, models (missing ones in amber), sampler,
  steps and size. Per workflow: **Open in ComfyUI** starts ComfyUI on
  demand, copies the graph to ComfyUI's `user/default/workflows/eki-<id>.json`
  and opens 127.0.0.1:8188 so it can be seen and edited there; **Reload**
  reads the file again; **Try** takes the inputs and shows the pictures;
  **Remove** moves the file to `workflows/removed/` and drops the entry.
  **Add** takes one exported file or imports every graph in a folder (a
  game's tools, say); only API format is taken — a UI-format file (with
  `nodes` and `links`) is refused with a line saying to export with "Save
  (API)". The endpoints live under `/api/comfyui/workflows`: the list,
  add or import, and per workflow reload, open, try and remove; POSTs are
  behind `X-Eki: 1` like every POST.
- **A drawing run** (`eki/providers/comfyui.py`): a ComfyUI run's model is
  the workflow (`runs.model` = its id). Its prompt is either the text,
  which fills `{{prompt}}`, or `{"inputs": {...}}` from a picture card;
  eki fills only the workflow's declared inputs, plus its defaults, posts
  it to `/prompt`, polls `/history/<id>` and saves each output through
  `/view`, one `tool` event named `image` per file, shown inline in the
  thread. The prompt id is the session, so a restarted run polls instead
  of drawing twice; a ComfyUI error fails the run with its message.

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

**The score's scope.** The score (gate 4) counts only what you asked for.
Every `run` row in the journal carries `data.scope`, written by
`observe.run_ended`: `self` when the provider is `command` or the thread is
a goal's, an item's or a chore's; `picture` when the provider is a `comfyui`
or the row is a picture row; `chat` otherwise. Rows written before the scope
existed are classified at read time by the same rule. `score.compute` counts
only `chat` runs in the share, the rates and the median, leaves out faults of
non-chat runs (faults with no run stay in), and returns `left_out` — the
long digest page prints "not counted: n self-work, m picture runs" under its score
table. Verdicts already frozen are never rewritten; a still-open build's
`before` is recomputed once under the rule and marked `"scoped": true`, so
before and after compare like with like.

## Standing goals and the budget

A **standing goal** is a goal in words tied to a git folder
(`eki goal add <folder> "…" [--check "<shell command>"] [--branch <name>]`).
The folder is recorded once as a project (`projects`: path, branch — the
folder's current one by default — and check; a folder on GitHub becomes
eki's own clone of it, below); the goal is a row in
`standing` (`on | paused | stuck | dropped`). eki works it in **rounds**, with
the same machinery it uses on itself: plan → items in worktrees → gate 1 →
proposed. A round is an ordinary goal owned by `eki` at background priority
(`goals.standing_id`, `goals.project`); it skips the draft, since the standing
text is the goal and the plan run reads the project. When a round has nothing
open, the next plan run is told what the last three rounds did and plans the
next one — or answers `ITEMS: []` with one line why, and the goal rests.

**GitHub is the front.** Label an issue `eki` in any repo you own and you
get a PR. There is nothing to register: issues are the way in, pull requests
the way out, and eki is the worker behind them. The only way eki reaches
GitHub is the official `gh` CLI as a subprocess, plus plain `git`
clone/fetch/push against origin: no token read, no HTTP of its own. Which
path a project is on (`github.path_of`) is decided fresh on every
housekeeping pass, never cached: its configured `remote.origin.url` must be
on github.com (else no `gh` is run at all), and `gh auth status` — never
with `--show-token` — must succeed; otherwise it's the local path below,
with the why ("origin isn't on GitHub", "gh isn't installed", "gh is not
logged in"). A project that drops to local mid-flight keeps its open PRs
untouched; they're looked at again when `gh` is back.

- **Only your words count.** An issue, comment or review counts only when its
  author is the account `gh` is logged in as (`gh api user`); anything else
  is ignored, and the log line or `items.why` says so. eki's own comments
  carry the marker `<!-- eki -->` (`github.MARK`) and are never read back.
- **Issues in, account-wide.** At most every `self.issues_minutes` (10) the
  `issues` step asks once, `gh search issues --label eki --state open
  --owner <login>` (`github.search_issues`, the login from `gh api user`
  once per pass). A repo in the answers with no project yet gets one on the
  spot (`projectsetup.ensure`); its issues wait, with the why
  (`<owner>/<repo>: issue #N waits — cloning`), until its setup is `ready`,
  and the pass after it turns ready takes them without waiting the 10
  minutes again (`github.soon`). A new issue becomes a project goal
  (`goals.issue`, planned without a draft), or a standing goal
  (`standing.issue`) when it's also labelled `standing`. Each issue makes
  one, once, whatever restarts or moves: it's looked for across every row of
  that repo, retired ones too; an edit after it's taken isn't followed. An
  issue that closes drops the unfinished work: waiting and building items
  are dropped, a goal still planning is `left` (`issue #N closed`), a
  standing goal is dropped; items already out as a PR are left to you.
  Issue work is owner `eki` at background priority, like standing rounds:
  it waits for `machine.room` and the budget.
- An issue on eki's own repo is self work: when its repo is
  `integration.own_repo()` (the integration clone's origin, if on
  github.com) it goes to eki's own queue as a self goal (`goals.project`
  NULL, owner `eki`, drafted like any wish), or a self standing goal when
  it's also labelled `standing`. No project row or clone is made for it,
  and it's taken once, whether by a self goal or by an older projects row
  of that repo. A closed issue drops the goal like any other. Self items
  land through the queue, not a PR, and carry no `Closes #N`, so you close
  the issue.
- **eki's own clone.** A GitHub project is worked in eki's clone at
  `~/.eki/projects/<owner>/<repo>` (`projects.clone_path`), never in your
  folder: eki doesn't look for it and doesn't touch it. The clone is
  blobless (`gh repo clone <repo> <dest>.part -- --filter=blob:none`, then
  moved into place), not shallow, since basing and "merged?" need ancestry
  and a shallow clone can refuse to push. It is a command run of its own
  (`projects.setup = cloning`, `projects.setup_run`), so the engine never
  waits on it and a restart just runs it again. When it lands, the
  project's branch is the clone's checked-out default branch, and it is
  fetched before every plan like any GitHub project. `eki goal add <folder>`
  on a folder whose origin is on GitHub reads only that folder's
  `remote.origin.url`, puts the goal on the `<owner>/<repo>` project and
  says `working in eki's own clone at <clone path>, not <folder>`.
  `--issues` is no longer needed and says so.
- **The check is guessed, and said.** When the clone lands, eki guesses the
  project's check once (`eki/checkguess.py`); the first rule that matches
  wins:
  1. an executable `bin/check`;
  2. `package.json` with a script `check`, else `test`, else `build`, run by
     the lockfile's manager (`pnpm`, `yarn`, `bun`, `npm` with
     `package-lock.json` → install `npm ci`, no lockfile → `npm install`;
     the others install `--frozen-lockfile`);
  3. `pyproject.toml`, `pytest.ini` or `tests/` → `.venv/bin/python -m
     pytest -q` after `uv sync` (with `uv.lock`), or after a `python3 -m
     venv .venv` with pip installing `-e .`, `pytest` and
     `-r requirements.txt` as present; with neither, bare `python3 -m pytest
     -q`. A `src/` layout gets `PYTHONPATH=src` so the worktree's code is
     tested, not the clone's editable install;
  4. a `Makefile` with a `check:` target, else a `test:` one;
  5. `Cargo.toml` → `cargo test`;
  6. `go.mod` → `go test ./...`;
  7. none: items are proposed "not judged: no check found; set one with
     `eki project <owner>/<repo> --check '…'`".

  It's recorded as `check_cmd`, `install_cmd` and `check_from` (`guessed:
  <why>`, `none: no check found`, or `set`) and never guessed again. The
  item keeps what judged it (`items.judged_by`), and every PR body names it:
  ``Check: `<cmd>` (guessed: …)`` and its tail, or the not-judged line.
  `eki project <owner>/<repo>` shows a project; `--check "…"` sets the
  check (`--check ''` means none), `--branch <name>` the base branch.
- **Install once.** When the guess has an install, it runs once, in the
  clone, as a second command run (`setup = installing`) before the first
  goal; later worktrees link `.venv`/`node_modules` from the clone. A clone
  or install that fails sets `setup = fault` with `fault = "<clone|install>
  failed: <first line>"`, shown on `eki goal`; the project's issues wait. A
  fault is never retried by itself, so a broken lockfile doesn't loop:
  `eki project <owner>/<repo> --retry` clears it and runs the failed step
  again.
- **Drop.** `eki goal drop <owner>/<repo>` drops the unfinished items of
  every goal on the project and its standing goals, cancels the setup run,
  removes the clone (only ever a path under `~/.eki/projects`), and records
  the repo's `eki` issues open at that moment (`projects.ignored`; if `gh`
  fails, the issues its goals came from, and it says so). Open PRs are left
  to you. Those issues stay ignored; a newer labelled issue brings the
  project back: a fresh clone and a fresh guess.
- **Folder projects move.** A project registered from your folder before
  this (`repo` unset, origin on GitHub) is `retired`: what it already has
  building, proposed or out as a PR finishes in that folder as before, and
  no new goal starts there. Its standing goals that are `on` move to the
  `<owner>/<repo>` project once its clone is ready, waiting till then with
  `moving to eki's own clone of <repo>`.
- **PRs out.** A proposed item is pushed as `eki/<id>` and gets a PR
  against the project's branch (one already there is reused): the item's
  title; its summary, the files touched, the check line above, then
  `Closes #N` on the last piece of an issue goal or `Part of #N` otherwise,
  and the marker. `items.pr` holds the URL, `items.pr_state` is `open |
  merged | closed`, `items.pushed` the sha last pushed. Merged on GitHub →
  `applied`, the worktree and branch removed; closed unmerged → `dropped`
  with your last comment as the reason, the branch kept. An item you drop in
  eki gets its PR closed. A gh or git failure is the item's `why` and is
  tried again next pass.
- **Follow-ups.** A comment of yours on an open PR newer than
  `items.pr_seen` is triaged: approval words (lgtm, thanks, 👍…) are not a
  change; anything else goes to a `prcomment` chore, and a skipped or failed
  chore counts as a change. A change puts the item back to `waiting` with
  the comment added to its spec; it builds on the same branch through gate 1
  and the review, is pushed (a fast-forward) and answered in one line naming
  the new sha. A follow-up that ends unfit or left says so on the PR, and
  the branch goes back to what was pushed. After `self.pr_followups` (3)
  (`items.followups`) eki says the next change is yours and waits.
- **Branches.** Merges happen on GitHub, so on this path eki fetches the
  project's branch into `refs/remotes/origin/<branch>` only and bases new
  work there. It pushes only `eki/*` branches, never forced. It still never
  checks out, commits to, resets or pushes the project's own branches.

**The local path — eki lands it.** For folders not on GitHub (origin
elsewhere or none; also a GitHub project while `gh` is missing or logged
out) eki works in your folder, and no `gh` is started for it. A project item
builds in `~/.eki/work/<item id>` on branch `eki/<item id>` of the project's
own repo (`.venv` and `node_modules` linked from the folder when
untracked). Its gate 1 is the project's `--check`, else its executable
`bin/check`, else none: the item is proposed "no check configured — not
judged". What happens next is the project's `autonomy`
(`eki project <name> --autonomy apply|propose`):

- **apply** (the default for local projects): a fit item joins the
  project's own queue (`eki/projectqueue.py`), the same queue eki's own
  items go through (docs/self-build.md): rebased onto the predicted head of
  the project's branch, a conflict resolved by a resolve run, checked in
  batch (gate 2 is the project's check, if it has one), then landed:
  `refs/heads/<branch>` moves forward (`eki/projectland.py`) and the item is
  `applied`; the goal's notice and the board say "landed on <branch>". Items
  of different projects never share a batch.
- **propose**: the branch waits for you, as before: the item stays
  `proposed` until its commit is in the project's branch
  (`git -C <path> merge eki/<id>`), then it is `applied`.

**Your folder moves only when it's safe.** When the branch is checked out
in your folder, landing runs `git merge --ff-only` there and nothing else.
If a tracked file the change touches has local changes in the folder,
landing waits and the board names it ("waits: chapter1 has local changes to
src/x.js"); it lands once the file is clean. Local changes to files the
change doesn't touch (logs, notes) don't block it. Untracked and ignored
files are never touched. Never a force, a reset, a checkout of another
branch or a rewrite of history already on the branch; a landing a restart
interrupted is finished by the next pass (a branch that already contains
the commit is done). Project items never enter eki's own train or swap.
`eki goal add` on a folder whose origin is on GitHub doesn't take this
path: it uses eki's own clone and says so, and its work keeps the PR path.

**When a round opens** (`standing.tick`, a housekeeping step): the goal is
`on`, not resting, has no open round, the Mac has room (`machine.room`), the
planner is available under the budget, and fewer than
`self.standing_waiting_max` (3) of the project's items wait for you as
proposed. Otherwise `standing.why` says which. An empty plan rests the goal
`self.standing_rest_hours` (24 h); a failed round rests it an hour, and three
failures in a row make it `stuck` until `eki goal resume`. `eki goal` lists
each goal with its why, rounds, open and proposed items with their merge
hint or PR, the path each project is on, and the budget right now; a
GitHub project is listed as `<owner>/<repo>` with its check and where it
came from, its clone, its setup (`ready | cloning | installing | fault:
<why>`, or `retired — finishing in <folder>`), its issue goals and its open
PRs; `eki goal pause|resume|drop|now <id>` —
`now` clears the rest but still waits for the Mac and the budget. A standing
goal whose folder is eki's own source has no project: its rounds are ordinary
self goals (integration repo, queue, `self_autonomy`).

**The budget** (`eki/budget.py`) is how much of each subscription window
background work may spend: `routing.json`
`"quota": {"background_day": 0.5, "background_night": 0.9, "night": "23:00-07:00"}`
(local time; an older `background_up_to` is read as `background_day`, the
file is never rewritten). Both ceilings are clamped to 0.9
(`budget.NEVER_ABOVE`), so background work never spends the last 10% of any
window. Windows a day or longer (week, month) are also held to pace: used no
more than the share of the window already gone. `capacity.status(...,
background=True)` asks for every window, not only the fullest, and says why
("kept for you: 5h at 52% (day budget 50%; night from 23:00 up to 90%)"). A
background run with no provider under budget stays queued with that why; a
run already going is never stopped. Work you wait for (`now`) ignores the
budget.

### Projects you work inside

**One project = a name + your folder (+ a GitHub repo), with chats inside
it** (`eki/projectchats.py`). Two folders, two meanings: `projects.folder`
is your real folder, where the project's chats run; `projects.path` keeps
its meaning above, where background work is based — the same folder for a
row with `repo` NULL, eki's own clone for a GitHub row. `folder_of` gives
`folder`, else `path` for a local row, else nothing (a GitHub row with no
folder yet); folders are always compared by realpath, no trailing slash.
A chat belongs to a project through `threads.project` (a `projects.id` or
`eki`); NULL is a chat outside any project and works exactly as before.
A new chat in a project (`eki ask -p <name|OWNER/REPO|eki>`, `body.project`,
the page's "New chat here") runs with the project's folder as its cwd, and
continuing it in another folder is refused. Runs in your folder take turns
as any other; nothing in `workspace` or the scheduler changes.

- **eki is built in.** `projectchats.SELF = "eki"`, named `eki`, its folder
  `builds.source()`, its background work the self goals (`goals.project IS
  NULL`). It has no row, so nothing that loops over `projects` sees it.
- **Old chats join by folder.** `adopt` (at the start of every listing,
  and after `create`/`set_folder`) sets `threads.project` on threads with
  none whose realpath(cwd) is a project's folder. It never moves a thread
  that has a project, and runs again harmlessly. Engine threads (clones,
  installs under `~/.eki/projects`, builds under `~/.eki/work`) never match,
  and no folder under `EKI_HOME` can be a project's.
- **Your clone is found, not made.** A GitHub row with no folder gets one
  from `home_clone`, only when asked (`listing(scan=True)`: the project
  page and `eki project`, never the sidebar poll): the first folder directly
  in your home (`paths.user_home()`, `EKI_USER_HOME` in tests) that is a git
  repo whose `remote.origin.url` is that repo. It reads only the config.
  Failing that, the page offers to pick a folder, or to clone to
  `~/<repo name>`: a `command` run of `gh repo clone <repo> <dest>` (a full
  clone), on a thread titled `clone <repo> to <dest>`, refused if `dest`
  exists. The folder is set at once and the page says `cloning…` while
  `dest/.git` is missing and the run is active, so a restart loses nothing.
- **New projects live in your home.** `eki project --new NAME [--folder
  PATH]` (or the `#projects` form) uses `~/<name>` by default, `git init`ing
  it when new and taking an existing folder as it is; the same folder again
  is the same project. `eki goal add <folder>` on a chat-only row fills its
  branch and starts background work on that same row; housekeeping never
  retires a row with no goals.
- **A project can be deleted** (`eki/projectdelete.py`; never `eki`, which
  is refused with the reason). The row turns `deleting` first, in one
  transaction, and every listing hides it; then `finish` cancels the active
  runs of its goals, items, setup and chats, leaves its goals, drops its
  items, standing goals and schedules, withdraws open asks, removes eki's
  worktrees of its items (branches kept) and, for a GitHub row, eki's own
  clone under `~/.eki/projects`, archives its chats (they keep
  `threads.project`), and last marks the row `deleted` with `path` suffixed
  `#deleted-<id>` so the same folder or repo can be added again as a fresh
  project — whose `adopt` takes the deleted row's chats back, still
  archived. Housekeeping finishes a delete a restart interrupted. The
  person's folder is never touched: only `git worktree remove` of eki's own
  worktrees runs there. A deleted GitHub row ignores the issues open when it
  went, as a drop does.
- **A chat row joins its GitHub row.** When `projectsetup.ensure` makes or
  brings back a GitHub row and a live row with `repo` NULL has a folder
  whose origin is that repo, the GitHub row takes the folder, the chats
  move to it, and the old row becomes `state='merged'` (or retires as above
  when it has goals of its own), said in the pass's lines. `listing` hides
  dropped and merged rows, and a retired row whose folder a live row has.
- **Work lands on its own.** A local project's fit items go through its
  own queue and land on its branch, fast-forward only, unless its autonomy
  is `propose` (the local path above: `projectqueue`, `projectland`; `eki
  project <name>` shows `autonomy: apply|propose`). The folder follows by
  `git merge --ff-only` only when no tracked file the change touches has
  local changes; untracked and ignored files are never touched. A GitHub
  project keeps its PR path.
- **The plan and build briefs** (`eki/projectbrief.py`). The plan brief
  lists the project's knowledge documents (name, about line, read mode,
  files) and asks for each item's `context` — the documents it needs, by
  name with a reason (Components → knowledge). The build brief carries the item's spec,
  its write-set, the knowledge brief narrowed to that context, and one
  sentence on how the work lands, from `projectbrief.landing`: the workflow
  card's `merge` when there is one, else `pr` for a GitHub project, `auto`
  for an `apply` project and `person` otherwise — "lands it on the
  project's branch itself" (auto), "opens a pull request from the branch …
  for the person to review and merge" (pr), or waits on the branch for the
  person to merge (person).
- **Instructions are the project's own file.** The offer is the Cards
  area's suggestion to add an `instructions` card (one click) whenever the
  project has no such card: "This project has AGENTS.md" (or CLAUDE.md)
  when one exists, else "No AGENTS.md yet". Adding it writes a starter
  `AGENTS.md` and a `CLAUDE.md` holding only `@AGENTS.md` when missing, so
  Claude Code and Codex read one copy. eki keeps no copy and never commits.

Not in scope: knowledge-file uploads, per-project memory, per-project
routing rows.

### Components

**Concrete pieces any agent can use, before any project framework**
(`eki/components/`). A component is a folder: `components/<name>/component.json`
plus the files its kind needs, inside a project folder, or in the
machine-wide pool `~/.eki/components/<name>/`. **A project assumes
nothing:** from a project folder (one with `components/`, or any listed
project's folder, eki's own included) only its own `components/` is
reachable — its cards. A pool component is reached through a card
`components/<name>/component.json` holding just `{"pool": "<name>"}` (named
like the pool component; a missing or broken one makes the card broken). A
run with no project (no folder, or a folder no project knows and with no
`components/`) sees the whole pool. eki finds the components of the folder a
run works in; a worktree reads its project's from the project folder, never
from the worktree. A broken component (bad JSON, unknown kind, a missing
file) is reported with its error and skipped; it never breaks the engine.
The project page shows a project's components as cards
(`eki/web/projectcards.js`, `api_cards`): add from the library, open to set
up, run, see its uses, remove. An open card is drawn in the side panel
(`panel.host`), the grid staying put with the open card marked.

**Everything a project's agents share is a card in the project.** All the
public knowledge, interfaces and assets shared between the agents of a
project live in that project as cards — instructions, knowledge, skills,
assets, services, servers, MCP interfaces, agents like the playtester.
eki's own global stores (`~/.eki/skills`, `~/.eki/mcp.json`, the pool) hold
only what every run everywhere needs. Kinds that are read or attached, never
called (`components.PASSIVE`: `knowledge`, `mcp`, `workflow`), stay out of the `eki use`
line of a run's brief.

**The common keys.** `kind` (`service`, `assets`, `agent`, `skills`,
`serve`, `knowledge`, `mcp`, `instructions`, `workflow` — see
"Workflows" below), `about` (one
line, shown wherever the component is listed), and how it is shared:

- `access`: `"queue"` — calls served in order, `slots` at a time (image
  generation); `"exclusive"` — one agent run holds it from its first use to
  the end of that run (a TTS model, an Ableton session) — whether it calls
  the card's script or its MCP server; `"shared"`.
- `memory_gb`: started only when `machine.room` has that much, else wait.
- `conflicts`: `[names]` never up or in use together (memory or file
  pressure); an idle conflicting one is stopped first, else wait.
- `idle_stop`: minutes a server stays up unused, on demand like the local
  model.

These rules are enforced machine-wide — across projects and eki's own runs —
by the engine's spawn step. Queues and holds are derived from the runs table
and the active runs, so they survive a restart. A wait shows on the
caller's run, with its why: "waits for voice: held by <run>".

A card that runs on a provider shares that provider, not a copy of it.
ComfyUI is one machine resource: every call of every card that targets it,
in any project, plus ComfyUI's own runs (routing's picture rows), counts
against the same slots, the same memory and the same conflicts (voice). Two
picture cards on different workflows still wait in one queue.

**Using one.** `eki use <name> [args…]`, from any run (an agent in a project
chat, a goal item, eki's own runs) or by hand. A call is a run with
provider `component`, its parent the caller's run, on a thread with no cwd,
so it goes through the engine like everything else. It prints the result
text or the output paths; a failure exits non-zero with the reason (a
missing component says so). `eki use` alone lists what is reachable from
here, each with its `about` line. Agent runs get one line in their brief:
which components are reachable and how to call them. eki runs no MCP server
of its own; the CLI is the integration.

**The kinds.**

- **`service`** — `"run"`: a command per call, the args appended, cwd the
  caller's folder, env `EKI_CALLER_DIR`. Or `"serve"` + `"health"` +
  `"call"`: a server kept up while used and stopped after `idle_stop`. Or
  `"target"`: a call handed to one of eki's providers at a target, as a
  row would name it — a picture card (below); the old `"provider"` key is
  still read. The pool template `voice` is exclusive, with a TTS command
  to fill in. Ableton is one service card (holo-cards'
  `ableton`): a script for unattended renders and an MCP server for writing
  MIDI in Live, one exclusive hold for both.
  **A service card can also offer an MCP server:** an optional `"mcp"`, the
  same shape as an mcp card's `"server"` (`{"command", "args", "env"}` or
  `{"url"}`). Project runs get that server as they get an mcp card's
  (`mcpserver.run_config`), and `run`/`serve` stays what `eki use <name>`
  calls. One card, one `access` rule and one `conflicts` list cover both
  uses. A command server is wrapped in `eki mcp-hold <card> -- <command…>`
  (`eki/components/mcphold.py`), a stdio proxy: the agent's first MCP tool
  call records a hold call run (provider `component`) parented to the
  agent's run, so the agent holds the card exactly like a script call —
  until the end of its run for an exclusive card — and a call that must
  wait waits with the same why ("waits for ableton: held by <run>"). A
  `{"url"}` server is not held: eki doesn't sit in its path. The card panel
  shows both uses: "script: <run>" and "MCP: <command or url> · Test".
- **A picture card** is a `service` with a ComfyUI target:
  `{"kind": "service", "about": "…", "target": "comfyui:<workflow>",
  "out": "assets/generated/<card>/"}`. Nothing else is written on the card:
  its inputs, defaults and models are read from the workflow's entry under
  the provider. A project may have several, each its own workflow —
  portrait, card art, backdrop, draft. (A ComfyUI workflow is a graph
  under the provider, not the `workflow` kind.) Agents call it like any card: `eki
  use <card> --prompt "…" [--seed N] [--size WxH] [--source <picture>]
  [--<input> value]`. `--size` sets width and height, `--source` uploads the
  picture for the workflow's `{{source}}`. Only the workflow's declared
  inputs are filled; an unknown one is refused with the list of known ones,
  and nothing runs. `eki use <card> --help` prints the workflow's label, its
  inputs with their defaults, and its models. The run is a ComfyUI run with
  the workflow as its model and `{"inputs": {...}}` as its prompt; its
  pictures are copied into `out` (relative to the project folder) and their
  paths printed, one per line. The card's panel shows the picker (ComfyUI →
  workflow, the same `settingstarget.js`), the workflow's inputs and models
  (missing ones in amber), the output folder, a Try box, and the last
  pictures, each with the prompt and the task that made it. Migration: the
  old form `{"provider": "comfyui"}` (the `images` template) is read as
  `"target": "<provider>:draft"` and written that way the next time the card
  is saved.
- **`assets`** — a folder eki manages outside git (`"path"`): managed, never
  versioned like code. Every worktree of the project sees it at its relative
  path by symlink (never a copy or a hardlink), so anything written there
  lands in the pool at once. `eki use <name> ls|info|rm <path>`; `rm` moves
  to the macOS Trash. eki says so when the path isn't git-ignored.
  **How worktrees get assets:** each new worktree of the project gets every
  assets card linked in as a symlink at the same relative path, before the
  build run starts; writes through it land in the one pool. Nothing is
  copied, never a hardlink. An assets path that isn't git-ignored as a whole,
  or that has tracked files inside, is reported on the card and not linked.
- **`serve`** — your own copy of the project in a browser. `"path"` is the
  URL prefix the folder is served under (starts and ends with `/`, e.g.
  `"/chapter1/"`, so the project's absolute paths keep their shape),
  `"headers"` extra response headers (default `{"Cache-Control":
  "no-store"}`), `"index"` the file for a folder (default `index.html`). The
  server is eki's own (`components/static.py`): threaded, 127.0.0.1 only,
  range requests so audio and video seek, the right MIME types (.mjs, .wasm,
  .glb, .ktx2, .ogg …), symlinks followed (an assets link is served
  through), no listing, nothing outside the prefix. One server and one port
  per worktree, and one for the person's own folder: the port is recorded
  under `~/.eki/component-state/ports/` so the same folder gets it again
  after a restart; the server starts on first use, stops after `idle_stop`
  (the template: 30), and when the worktree goes housekeeping stops its
  server and frees its port for the next folder. A run in a project
  worktree or folder whose project has a serve card gets `EKI_SERVE_URL`
  (e.g. `http://127.0.0.1:47213/chapter1/`) and one brief line — its own
  copy is served there, open that and not the person's server. `eki use
  <name>` starts it for the caller's checkout and prints the URL. The
  playtester called from such a run plays its caller's link; its own `url`
  is the default only when there is none. The card shows each link that is
  up (folder → URL). `"build"` is an optional shell command run in the
  served folder before its server is (re)started, and when `eki use <card>`
  or the playtester is called and the folder changed since the last build
  (`components/servebuild.py`). The check is cheap: the newest mtime of the
  folder's tracked files (and untracked ones that aren't ignored) against
  the last good build, recorded under `~/.eki/component-state/`. Built
  output should be git-ignored, so a build doesn't count as a change. A
  failed build fails the call with the tail of its output, so the
  playtester never plays a broken build.
- **`agent`** — a persona: `prompt.md`, optional `skills/`, `uses` (the
  components it may call), `mcp` (the MCP servers it gets) and a `target`
  (provider → model → effort). Each call is a session; it can be called many
  times.
- **`skills`** — a project's own skills (`eki/components/skillcard.py`): the
  card folder (`components/skills/` from the template) holds one sub-folder
  per skill, `<name>/SKILL.md` plus its files, versioned with the project.
  Claude Code runs in the project folder or its worktrees get it as an extra
  `--plugin-dir` (a wrapper under `~/.eki/project-plugins/`), Codex runs a
  per-run folder of links (`~/.eki/run-skills/<run>`); nothing is written
  into `~/.claude`, `~/.codex` or `~/.agents`. Runs elsewhere and bare agents
  get none. A broken SKILL.md (missing, no front matter or no description)
  is shown and skipped. `eki component skills add <project> <folder|git URL>`
  (never overwrites without `--replace`) and `eki component skills remove
  <project> <name>`; `eki use <card>` lists what it holds. The rule:
  project skills live in the project's card; ~/.eki/skills is only for skills every run should have.
- **`knowledge`** — the documents the project's agents read, one card
  holding many: `"docs"` is a list of documents, each `{"name", "files",
  "about", "read"}`. `"files"` are paths relative to the project root
  (globs and folders allowed, no absolute path, no `..`); `"read"` is
  `"always"` — every run in the project, planned or not, reads it first
  ("read first: <files> — <about>"); keep it for the few documents that
  apply to all work — or `"when-relevant"` — the planner picks per item,
  and a chat sees it as available ("look here when relevant"). E.g.
  `{"kind": "knowledge", "about": "…", "docs": [{"name": "decisions",
  "files": ["design/DECISIONS.md"], "about": "…", "read": "always"}, …]}`.
  The old shape — `"files"` and `"read"` at the top level — still loads, as
  a one-doc card whose document is named after the card, so nothing breaks.
  holo-cards keeps one `knowledge` card (decisions, design rules,
  contracts, owner memory, playtest notes …) in which only `decisions` is
  always. Everything works per **document**, not per card: the run brief
  (always-docs under "read first", the others "also available"), the
  planner's context, `eki component knowledge` and the board's item detail.
  `knowledge.files` lists each file with its size, last change and last
  author; a missing one shows on the card and is left out of the brief.
  The card panel (`web/cardknowledge.js`) lists the documents — name, a
  read-mode dropdown with its explanation, about, files, size, last change
  — each opening in the side panel's doc viewer / editor, with
  "+ document" and × per document.
  **The planner picks an item's context.** It already writes each item's
  spec (cause, files to touch), so it also names the knowledge documents
  the item needs, each with a short reason: the `"context"` key of an
  `ITEMS` entry (`[{"name", "why"}]`), stored as `items.context` (JSON) and
  shown on the board and in `eki self show` ("context: architecture,
  voices"). A name is a document's; an old card name still resolves and
  picks all of that card's documents. That keeps a one-line fix from being
  briefed on the story core and the owner's memory. For such an item
  `knowledge.brief(folder, context=…)` reads first only the always-docs,
  the chosen documents and the instructions card (`AGENTS.md`, which Claude
  Code reads anyway), and names the rest in one line: "Also available if
  you need them: <names> (see components/)". The item's fix-up rebuild,
  resolve run and review get the same context. An item with no context
  (old plans, `single`-plan and `--one` items, a planner that left it out)
  gets the full brief, and chats keep the full brief: there is no planner
  in the loop.
- **`mcp`** — an MCP server only this project's runs get: `"server"` is
  `{"command", "args", "env"}` or `{"url"}`. `mcpserver.run_config` gives a
  run its `--mcp-config` file (eki's registry merged with the project's
  servers, for that run) and Codex's `-c` flags; nothing is written into
  `~/.claude`, `~/.codex` or `~/.eki/mcp.json`. Env values show masked
  (`mcpserver.public`).
- **`instructions`** — the project's `AGENTS.md` as a card
  (`eki/components/instructions.py`, `{"file": "AGENTS.md"}`). The card shows
  the file's first heading and line count; opening it renders the file, and
  Edit turns it into a text editor (saved on blur / ⌘S). Backticked paths in
  it that are files inside the project are listed under "Read first", each
  opened read-only (never `..`, an absolute path or a symlink out). Adding
  the card to a project without the file writes a starter (title, "Read
  first", "How to check your work") and a `CLAUDE.md` holding only
  `@AGENTS.md`; an existing file is only pointed at, never overwritten. A
  `CLAUDE.md` without `@AGENTS.md` is said on the card, with "Link
  CLAUDE.md" appending the line. eki writes only those files, never commits.

**The bare-agent rule.** `"bare": true` means no built-in skills, only its
system prompt, strictly: Claude Code with `prompt.md` as its whole system
prompt (replacing the default), no setting sources, no CLAUDE.md or
AGENTS.md, no skills or slash commands, no file tools, no MCP servers but
the ones listed, and cwd an empty scratch folder outside any project.

**The playtester template** is a bare agent that knows nothing about the
game and can't see its code or assets. Its one tool is a browser it sees
through screenshots and drives with mouse and keys — Playwright MCP in
vision mode with `--snapshot-mode none` (no accessibility tree in any
response), and an `allow_tools` list (screenshot, the `_xy` mouse tools,
keys, type, wait, navigate, back, resize, close): a bare agent with one runs
Claude Code in `dontAsk` mode, so every other tool is refused — opened at the
component's `"url"`. A navigate anywhere outside that url (same origin, under
its path) marks the session `tainted: opened <url>` in its `session.json`
and on the printed result. It plays, takes notes as it
goes, records how it feels at each moment (emotion + why, timestamped), and
ends with an honest review. `eki use playtester ["what to play / focus
on"]`. Each session's notes, emotion log, review and screenshots are kept
under `~/.eki/components/sessions/<name>/<session>/`, one folder per call,
and the call prints the review and that path.

**CLI and window.** `eki component kinds | ls [folder] | add <kind|template>
[--name N] [--pool | -C folder] | show <name>`. `add` writes the folder from
the template and never overwrites; a template's `mcp_servers` are added to
eki's MCP registry switched off, so only the agent that lists them gets
them. eki never commits to a project's branches. The Settings page has one
folded, read-only "Components" section — the pool and each project's, one
line each, details on click. Nothing else in the window.
`eki component merge` (`eki/components/cardmerge.py`) makes fewer cards: it
folds a project's knowledge cards into one (each keeping its files, about
and read mode as a document) and an mcp card into a service card (its
`server` becoming the service's `mcp`), moves the old folders to the Trash
and rewrites every `conflicts` list that named them. holo-cards went from
18 cards to 10 this way.

Not in scope yet: a project page of components. A project-level router
switch is the `workflow` card's `router` stage (below); per-project routing
rows are still not in scope.

### Workflows

**Each project chooses its own workflow with one `workflow` card**
(`eki/components/workflow.py` checks it, `eki/workflow.py` reads it for the
engine). eki's own project runs the full pipeline; a game may need only
plan, agents in parallel, each checking its own work, merge; some projects
don't need the router at all. So the workflow is a card in the project, not
code eki applies to everyone. A project has at most one (a second is broken:
"a project has one workflow card: <name> is it"). `eki component add
workflow -C <folder>` writes the template. The workflow kind has no
`access` setting (`components/workflow.DROPS`): it isn't a shared resource.

**The workflow is shown as a graph on its own page**, `#project/<id>/workflow`
(`eki/web/workflowpage.js`, `workflowcanvas.js`, `workflowinspect.js`),
opened from the workflow card's "Open workflow →" and the project header's
Workflow link. The card's own panel is one line ("plan split · 4 parallel ·
checks: self · merge auto") and that link. The page is a dark canvas — pan by
drag or two-finger scroll, zoom by pinch or ⌘-scroll, fit to view on open —
with the stages left to right as boxes (a title, a one-line summary) joined
by arrows: Request in → Router (or "straight to <target>") → Planner / Single
item → Build ("4 in parallel") → each check in order → Merge → After-merge.
When one row would be too small to read, fit wraps the stages into rows like
text (an arrow from a row's end to the next row's start), picking the row
length that shows the graph largest; it fits again when the side panel opens
or closes, unless the person has panned or zoomed.
A stage that is off is drawn faded with a dashed border, still clickable; on
eki's own project the locked stages (train, batch) carry a lock. Cards that
feed a stage hang off it as small side nodes on dotted links: knowledge
("picked per item") and instructions → Planner and Build, skills and mcp →
Build, serve → Build and Playtester, the playtester card → Playtester, the
check command → Check command; a click opens that card in the side panel.
A click on a stage opens it in the side panel: what it does, its settings
(the card's dropdowns with their explanations, saved into the card as they
change; locked ones shown but fixed), and the goals and items at it now
(title, state, age), each opening its detail. The page reloads every 4 s
while shown: counts on the nodes, a count badge on an arrow when items are
moving into a stage, a red dot on a stage with a failure or an item left for
a person. Everything comes from `GET /api/projects/<id>/workflow`
(`eki/api_workflow.py`), built on each call from the card and the goals and
items tables; it holds nothing. Where a goal or item is drawn:

| state | stage |
|---|---|
| goal `drafting`, `planning`, `failed` (red dot) | Planner |
| item `waiting`, `building`, `left` (red dot) | Build |
| `judging` | Review if checked, else Check command if checked, else Self check |
| `reviewing` | Review if checked, else Self check |
| `unfit` (red dot) | Check command if checked, else Self check |
| `queued`, `resolving`, `rechecking` | Batch if checked, else Merge |
| `landed`, `rolled back` on eki | Train |
| `proposed`, `locked` (red dot), an open PR | Merge |
| `applied`, `live`, `dropped`, `landed` elsewhere | not drawn: over |

**The stages and their choices**, in the order they run:

- `router`: `on` — requests are routed (rules first, a small model for the
  rest) among the providers and the project's cards · `off` — everything
  goes to `target` (provider → model → effort, the target picker; required
  when off). Off really means off: chats and goals in the project never
  call the prompt check; their runs go to the target directly, and the why
  says so.
- `plan`: `split` — a planner turns a request into items with write-sets,
  built in parallel · `single` — the whole request is one item for one agent.
- `parallel`: how many items build at once (a whole number ≥ 1).
- `checks`: an ordered list, each optional but `self`:
  `self` (the building agent verifies its own work — screenshots, its tests —
  before it finishes; always on), `review` (a second reader agent,
  docs/self-build.md "The review"), `command` (the project's check command,
  the card's `command` field, as gate 1), `batch` (the queue checks queued
  changes together and bisects on red; needs `command`), `playtester` (the
  project's playtester card, `playtester` naming it, plays the change),
  `train` (build + drills + swap — only eki's own project has it).
- `merge`: `auto` — eki lands it on the branch · `pr` — a pull request on
  GitHub (needs a GitHub repo) · `person` — waits for the person.
- `after_merge`: an optional shell command (e.g. `npm run build`) eki runs
  in the person's folder, where the branch is checked out, right after it
  lands a change there under merge `auto`. It is a `provider="command"` run,
  so its output is kept and it can be resumed. It never blocks or undoes
  the landing. A failure shows on the item's board line and the goal
  ("landed; after-merge build failed: <tail>") and sends the needs-you
  notification. One runs at a time per project; changes that land close
  together get one run after the last. Merge `person` and `pr` never run
  it: eki didn't land there. `eki/aftermerge.py` does it; its state is on
  disk, so a restart loses nothing.

**A stage not chosen is skipped**, and said once on the board: the card's
line (`workflow.said`) is e.g. "checks: self only", or "router off →
claude:sonnet; plan single; checks: self, command". A project with
`checks: [self]` lands an item when its build turn ends, with no gate run.

**No card = today's behaviour for that kind of project**, so nothing changes
until a card exists. A broken card counts as none (its error is on the card):

| project | no card means |
|---|---|
| local | router on, split; checks self, review, and command + batch when it has a check; merge by its autonomy (`apply` → auto, `propose` → person) |
| GitHub | as local, merge `pr` |
| eki itself | split, parallel 4; checks self, review, batch, train; merge per `self.autonomy` |

**Adding a card migrates the old settings once** (`workflow.from_settings`):
the project's check command becomes `command` (with `command` and `batch`
checked), its autonomy becomes `merge`, `self.review` decides `review`, and
`self.parallel` is `parallel`. From then on those settings are read from the
card — one source of truth (`projects` reads the check command and the
autonomy through it).

**eki's own safety stages are fixed.** On eki's project the card
(`components/workflow/component.json` in eki's checkout) shows the full
pipeline, and the stages that protect eki itself — the train and its
drills, `batch` (`workflow.LOCKED`), and the locked files (docs/self-build.md
"What eki may not change alone") — are shown but can't be turned off: a card
there without them is broken with "<stage> protects eki itself: it can't be
turned off here", and the engine keeps them regardless. `train` on any other
project is refused.

## Notifications

eki pushes to your phone through [ntfy](https://ntfy.sh): a plain HTTP POST,
no account, no SDK. Only two kinds of event are sent, nothing else.

- **eki · goal done** (or **eki · goal ended, N left**) when a goal ends —
  every item live or landed, or stopped with items left. The body is the
  goal's title; a GitHub goal's notification opens its PR or issue.
- **eki · needs you** (high priority) when an item goes `locked`, an item is
  left for a person, or an agent's question or permission card opens. The
  body says what and the command to act (`eki self apply <item> --yes`).

**Settings.** `routing.json`
`"notify": {"server": "https://ntfy.sh", "topic": "", "events": ["goal_done", "needs_you"]}`.
An empty topic means off. The topic is a secret: it is shown masked and never
written to logs, digests or PR bodies.

**Delivery.** Each event has a stable key — `goal:<id>:done`,
`item:<id>:<state>:<tries>`, `ask:<id>` — recorded in the `notices` table at
the transition. The housekeeping drain sends each at most once, on a thread
(5 s timeout, 3 tries, never blocking the engine), so a restart neither
re-sends nor loses one. A goal counts only if it ended within the last day.

**CLI.** `eki notify` (on or off, the events, the topic masked),
`eki notify topic <name>`, `eki notify test`.

**On the phone and the Watch.** Install ntfy from the App Store and subscribe
to the same topic on the same server (ntfy.sh by default). Pick a long,
unguessable topic: anyone who knows it can read it. Allow ntfy's
notifications in iOS Settings. The Watch mirrors iPhone notifications while
the iPhone is locked or asleep and ntfy's are on: in the Watch app, under
Notifications, mirror ntfy. Then `eki notify test`.

## Not built yet

Learned preferences. Built on the pieces above, not beside them.
