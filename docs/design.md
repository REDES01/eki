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
| `workspace` | git worktrees under `~/.eki/work/<key>` for work that must not collide; runs in one folder otherwise take turns |
| `routing/*` | the prompt check (which row) and the table (where each row goes), each checkable on its own |
| `targets` | one way to name where work goes, as a string (`provider`, `provider:model`, the old `provider:tier`) or an object `{provider, model?, effort?}`: `parse`, `fmt` (the shortest written form), `key` (a stable one-line dict key, `claude:opus@high`), `provider_of`, `label` (`claude · Opus 5.5 · high`), `choose` (model and effort against the read list, with a note when either falls back to the default), `resolve` (the old `(model, note)`), `problems`; pure |
| `roles` | eki's own jobs — `router`, `planner`, `reviewer`, `chores` — and the target each goes to: `get`, `default` (the old settings), `set_role`, `pick` (with the fallback and its why), `record`/`timings` (table `role_calls`) |
| `modellist` | each program's own model list (Claude Code's `initialize`, Codex's `model/list`, local's `/v1/models`) cached in `provider_models`: per model its label, `description`, `resolved` (the pinned model an alias stands for), `efforts`, `default_effort`, whether it is the default, and `floating` (an alias that follows the newest); no suggestions |
| `capacity` | can a provider take work right now: installed, not cooling down after a limit |
| `machine` | power, memory pressure, load — background work only runs when the Mac has room and is plugged in |
| `skills`, `mcp` | one store, handed to every program per run (Claude: `--plugin-dir`, `--mcp-config`; Codex: `~/.agents/skills`, `-c mcp_servers…`) |
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
the program's description), then effort when the model offers any; a saved
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
(need `image`), `image-edit` and `image-upscale` (need `image-edit`) all go
to `comfyui`, which `KIND_ORDER` counts as cheap as `local`. A table
without them gets them in memory from `table.rows()`, aimed at whichever
providers can do what they need; `routing.json` isn't rewritten. The why
reads "rule: asks for a picture → image → comfyui".

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
  up to 1100px, where a long code line scrolls inside its own block,
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
    act})` and names the one file it edits; Form/Raw JSON, Reset and Save
    act on that file. There is one draft per file, so edits in Jobs and
    Rows are saved together, and changing section never drops a draft.
    Settings sit in rounded groups of one-line settings — the label (and a
    dim sentence) on the left, the control on the right
    (`settingsform.js`). Rows and providers fold to one summary line each
    (a row's key, title and target chain as labels joined by " → ", from
    `settingsTarget.label`, which is `targets.label`); clicking one opens
    its editor and closes any other, so one is open at a time, and which
    one is page state, not in the address. Rows and
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
- **Pictures** (`eki/providers/comfyui.py`, `comfyui_graphs.py`,
  `eki/gallery.py`): ComfyUI is a provider kind (`comfyui`, can `image` and
  `image-edit`). When `~/flux/ComfyUI` (or `EKI_COMFYUI_DIR`) holds a
  `main.py` and providers.json has no `comfyui` entry, the default one joins
  the file's entries in memory — the file is never written; an entry you
  write, even `{"off": true}`, wins. It is a managed server like the local
  model (`models.MANAGED_KINDS`): started on demand by the run, stopped
  `idle_stop` minutes after its last run, stepped out under memory pressure
  when nothing uses it, listed and started/stopped by `eki models` and the
  sidebar; a ComfyUI eki didn't start is never stopped. Five rows, each with
  its own graph in `comfyui_graphs/`: `image` (draft.json, FLUX.2-klein 4B,
  4 steps), `image-hq` (hq.json, Qwen-Image 2.1 Q8 GGUF, 30 steps),
  `image-anime` (anime.json, NoobAI-XL), `image-edit` (edit.json, klein 4B
  shown the source) and `image-upscale` (upscale.json, 2× then a light klein
  pass). The worker puts the row in `turn.extra["row"]`; an entry's
  `"graphs"` can override any of them. Rules pick the row from the words
  (upscale, edit an attached picture, anime, quality words, "draw …"); a
  follow-up that isn't a question, in a thread whose last picture is known,
  edits it — the thread's last picture (`store.last_picture`, drawn or
  attached) is the source, uploaded to ComfyUI. A comfyui thread never stays
  by "thread": the rule's row picks the graph, and a text question leaves it
  for a text model. Everything drawn stays in `~/.eki/images/<run>/` and is
  browsable: `eki pictures`, `GET /api/pictures?limit=&before=`, and the
  window's Pictures view (a grid, a picture opens in the side panel).
- **A drawing run** (`eki/providers/comfyui.py`): eki fills the row's
  graph (the old single `"workflow"` key still means the `image` graph),
  posts it to `/prompt`, polls `/history/<id>` and saves each output
  through `/view`, one `tool` event named `image` per file, shown inline in
  the thread. The prompt id is the session, so a restarted run polls
  instead of drawing twice; a ComfyUI error fails the run with its message.

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

**The local path — landing is a branch you merge.** For folders not on
GitHub (origin elsewhere or none; also a GitHub project while `gh` is
missing or logged out) eki works in your folder, exactly as before, and no
`gh` is started for it. A project item builds in
`~/.eki/work/<item id>` on branch `eki/<item id>` of the project's own repo
(`.venv` and `node_modules` linked from the folder when untracked). Its gate 1
is the project's `--check`, else its executable `bin/check`, else none: the
item is proposed "no check configured — not judged". It stays `proposed`
until its commit is in the project's branch (`git -C <path> merge eki/<id>`),
then it is `applied`. eki only adds worktrees and `eki/*` branches: it never
checks out, commits to, fetches into, resets or pushes the project's
branches. Project items never enter eki's queue, train or swap. `eki goal
add` on a folder whose origin is on GitHub doesn't take this path: it uses
eki's own clone and says so.

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
- **A chat row joins its GitHub row.** When `projectsetup.ensure` makes or
  brings back a GitHub row and a live row with `repo` NULL has a folder
  whose origin is that repo, the GitHub row takes the folder, the chats
  move to it, and the old row becomes `state='merged'` (or retires as above
  when it has goals of its own), said in the pass's lines. `listing` hides
  dropped and merged rows, and a retired row whose folder a live row has.
- **Instructions are the project's own file.** With no `AGENTS.md` or
  `CLAUDE.md` in the folder, the page offers a chat that looks at it and
  writes `AGENTS.md`, plus a `CLAUDE.md` holding only `@AGENTS.md`, so Claude
  Code and Codex read one copy. eki keeps no copy.

Not in scope: knowledge-file uploads, per-project memory, per-project
routing rows.

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
