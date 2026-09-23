# Roadmap

Where eki is going, in the order it's meant to get there. The README says what
eki does today; this file says what it's for and what comes next. It is the one
plan — if something here is wrong, fix it here.

## The vision

**eki keeps the machine you own working for you around the clock, and rents
frontier models only for what it can't do.**

People buy serious machines for AI — a Mac Studio, a big MacBook Pro, a GPU
box — and the machine sits idle most of the day: running local models well is
fiddly, and nobody queues work for the hours they're away. Meanwhile the
subscriptions they rent run out. eki is the station on that machine: always
open, the local models are the local lines, Claude Code and Codex are the
express. It keeps the right models on the hardware, sends each request to the
cheapest line that can carry it, and — the part nothing else does — fills the
idle capacity with work worth doing, from goals you declare, stepping out
the moment you or another app need the room.

The first measurement, on the Mac eki is built on (2026-09-23): over three
days the local models did about 55 minutes of work — about 1% of the time —
while taking a third of the requests. That is the number to move.

**Who it's for.** People who own the hardware and make things with it:
technical solo creators and small teams — a game with hundreds of NPCs,
portraits and lines; a codebase with a backlog — and developers who pay for
more than one agent. The pitch is honest where the grey market isn't: your
subscription goes further because most work never needs it, your code never
leaves your machine, and the model is the one it says it is.

**The promise.** Your machine works while you don't, it never takes what you
need (your attention, your memory, your subscription), and your setup gets
better the more you use it.

**What makes it work:**

1. *The machine* — the right local models for the hardware, started and
   stopped as work needs, kept current (`eki lineup`).
2. *The lines* — routing to the cheapest line that can do the job; a model
   without tools hands over; a subscription that runs out carries on in the
   next (Stage 2).
3. *The idle shift* — declared goals turned into a backlog, worked through
   whenever the machine has room, giving way when something needs it, within
   a subscription budget that never touches the last of a window (Stage 3).
4. *The neighborhood* — skills, corrections, the tools you connected and a
   project's canon, held once and given to every model, so the unattended work
   agrees with you (Stages 1, 7).

**The first public release** is Stages 1–3: an engine and a command line that
run without the app, on macOS (Linux through Homebrew, untested).

**Later**, one piece of work split across providers — an RPG with a frontier
agent writing the code, a story model writing the lore, an image model making
the icons, a 3D backend producing Blender assets, and one memory under all of
them (Stages 4–8).

**Why no one else builds this.** Providers want your work on their servers,
not on your machine, and no provider makes your memory portable to a
competitor. Where a standard exists (`AGENTS.md`, MCP, Agent Skills) eki builds
on it rather than beside it. eki reaches subscriptions only through the
official programs — never a relay, never a shared login.

**What waits.** The Mac app is one front end, not the product. Redrawing more
of the CLIs' own screens and new kinds of output come after the first release
unless it needs them.

## What doesn't change

- **eki builds eki.** eki's whole job is handing work to agents that can
  change code — Claude Code, Codex, a local model with Codex's hands. Its own
  source is code like any other, so eki can change itself: take a request or
  notice a fault, dispatch an agent on its own repo, test the result, build
  it, replace the running engine and app with it, and go back if it's worse.
  This is not a feature beside the others; it is how the others get built.
  The self-build track makes the loop; every stage is work eki should be
  doing on itself, and carries an *evolve* line saying what it learns to
  keep up.
- **eki is a light router that makes full use of the harnesses that
  exist.** Models come from providers, harnesses from their makers (Claude
  Code, Codex), tools from MCP servers, instructions from skills. eki is
  the registry of all of them, the router between them, and the one
  interface — it doesn't build a harness of its own or a tool a provider or
  a server should supply. A capability a request needs (the web, the
  screen, a repo, a picture) is something a provider *has*, and routing
  finds one that has it; a gap is filled by adding a provider or a server.
  The tools eki serves are the ones that *are* integration: reaching
  another provider (`eki_ask`, `eki_image`, `eki_capabilities`), and — for
  a model with no tools of its own — the single one it needs: hand the
  thread to a harness. Its screen tools are a stopgap until a program's own
  computer use works under it, and go then.
- **The app is a front end.** Everything eki does is done by the engine and
  reachable from `eki`; the Mac app draws it. Nothing needs the app.
- **Your credentials stay yours.** Subscriptions are reached only by running
  the official CLIs as you. No token is read, copied or re-exposed. Ever.
- **Local first, nothing hidden.** Every answer says what produced it and why.
- **Work outlives the window.** Everything is a run, written down before it
  starts, executed by the engine.
- **Read what others know about models; don't work it out.** Which model
  a vendor recommends for what is on its own page; which local model
  people run and how its makers measured it is on Ollama. eki reads those
  daily and routes by them. Its own tests are the fallback, for what no
  one describes.
- **A thread stays with the model that answers.** It has the context. The
  first message is routed by the table; after that the thread moves only
  when it must — the model hands it over (a model without tools does, when
  a request needs files, commands, the web or a real build), you pick
  another, its answer failed, or it can't take the request (quota, not
  running). A program joining a thread is given what was said before.
- **The command line is the agents' interface.** Any agent with a shell can
  call `eki`. No second protocol until something without a shell needs one.
- **Files are the handoff.** A backend's output lands in the project folder
  and the path is what gets passed on.

## Where it stands (0.2.0 in progress)

Built: runs and the always-on engine; routing by label, capability scores,
live quota and pace; eki's own benchmark measurement beside the public boards;
Claude Code, Codex, MLX, GGUF/llama.cpp, OpenAI-compatible, Anthropic API and
ComfyUI adapters; local models with Codex's harness through the gateway; model
recommendation, download, profiling and context sizing; engines as fetched
extensions; schedules; usage in the app and menu bar; artifacts, image viewer
and gallery; the `eki` CLI (`ask`, `runs`, `watch`, `history`, `models`,
`policy`, `agent`, `serve`).

Claude Code under eki is the whole program now, not a chat with it: the
terminal's panels (`/mcp`, `/model`, `/permissions`, `/usage`, `/context`,
`/rewind`, `/agents`, `/hooks`) are drawn by eki from the program's own
control protocol, MCP servers are authenticated through it, an MCP server's
questions (elicitation) are cards, the model's thinking shows, and the
program gets eki's tools in-process — the other backends, pictures, the
screen (`docs/claude-code.md`).

Open right now:

- [ ] Commit the working tree: image follow-up edits, ComfyUI workflows as
      models (`eki/workflow.py`), gallery, picture sorting mode, app icon,
      Claude Code panels + eki's in-process tools + the MCP registry
- [ ] Confirm the hold-then-release swipe fix on a real trackpad
- [ ] Cut 0.2.0 with the icon in the release

## Stage 1 — One set of skills, context and tools

Claude Code and Codex each keep their own skills, standing instructions and
MCP config, and the local models have none. eki owns one copy of each and
gives every backend a view of it. Nothing is authored inside `~/.claude` or
`~/.codex` by hand. This is the heart of the first public release.

- [x] **No app needed.** `brew install REDES01/eki/eki` on macOS and
      Linux; `eki agent install` hands the engine to `brew services`
      (launchd or systemd). The formula is generated per release with every
      dependency pinned as a wheel (packaging/homebrew/). A Homebrew install
      doesn't change its own code. *Still open:* the screen helper on Linux,
      and `brew install eki` in homebrew/core once eki has the users for it
      (75 stars, or 225 if we submit it ourselves)
- [ ] **The harnesses people already use.** Claude Code and Codex now;
      Gemini CLI and other agent CLIs next, each through its official
      program and reading the same store

- [x] **One skill store.** `~/.eki/skills/`, under git (every change a
      commit), in the Agent Skills format both CLIs read (`eki/skills.py`,
      `docs/skills.md`). Codex 0.154 reads `~/.agents/skills` and follows
      links; Claude Code reads `~/.claude/skills` and follows links — both
      checked against the real programs
- [x] **Each CLI sees a view.** Enabled skills are symlinked into
      `~/.claude/skills/` and `~/.agents/skills/`; turning one off removes
      the link, not the skill. A folder eki didn't make is never touched
- [x] **eki's own metadata stays out of the skill.** A sidecar per skill:
      which backends it is on for, where it came from, version, usage. The
      `SKILL.md` stays portable
- [x] **Import what's already there.** Skills found on either side are taken
      into the store once, then linked back
- [x] **Skills for backends with no loader.** For MLX, GGUF and API
      routes the engine does the loading: descriptions in the system prompt,
      the body injected only when invoked (`/name`, `$name`) or when the
      model answers `[[skill:name]]` (`Engine._skilled`). Checked on Qwen3.8.
      Image routes don't take skills yet
- [ ] **One standing context.** `AGENTS.md` is canonical, per project and
      globally; `CLAUDE.md` is an `@AGENTS.md` import plus what is truly
      Claude-only. The global files for both CLIs are generated from one
      source. Stage 4's "how to use eki here" section lands in that source
- [x] **One tool registry.** MCP servers declared once in eki
      (`~/.eki/mcp.json`, the `/mcp` panel), rendered into Claude Code per
      session (`--mcp-config`) and a managed block of Codex's `config.toml`;
      servers Claude Code already has can be imported (`eki/mcpregistry.py`)
- [x] **eki's tools for both CLIs.** Claude Code gets them in-process
      (`eki/mcpbridge.py`, an SDK MCP server over the control channel);
      Codex runs `eki mcp`, the same handlers over stdio. Screen tools
      through `mac/tools/hid.swift`
- [x] **Both programs' panels, one drawing.** Claude Code's control channel
      and Codex's app-server answer the same ops (`Engine.agent_control`);
      the panels and slash commands show only for the picked program, and
      eki's own picker owns the model on every backend (no `/model`)
- [ ] **Commands shared under Auto.** `/skills`, `/context` and the like
      mean something for every provider; Auto should offer what is common
      to all of them — carefully, one command at a time
- [x] **"How to call eki" is a skill**, installed for both CLIs (the
      `eki` skill, refreshed at engine start until you edit it). Generating
      it from `eki capabilities` waits for that command (Stage 4)
- [ ] **Per-project layer** once Stage 5 exists: `.eki/skills/` and the
      project's `AGENTS.md` sit on top of the global set
- [x] A Skills pane: `/skills` in any thread — eki's store with
      per-backend toggles, an editor, import; then what Claude Code alone
      sees there (plugins, project, synced). `eki skills` on the command line
- *Evolve:* a skill is the safest thing eki can change about itself. After a
  run it drafts a new skill or an edit as a commit in the skills repo —
  readable, revertible, no engine swap. (Landed in 035ecd6, `eki/learn.py`:
  reviewed on a sign — asked, corrected, recovered — by the backend that did
  the work; see `docs/skills.md`. Checked live with Claude Code, Codex and
  Qwen3.8.)

## Stage 2 — Runs that survive and switch

Runs are fully parallel and more of them are unattended — schedules, agents
calling eki, eki working on itself. A run has to be safe to leave alone, and
a thread has to be able to move to another harness without losing its place.

- [x] **A run that touches a repo gets its own worktree.** Two agents sent
      to the same folder otherwise overwrite each other. Merge-back is an
      explicit step with a visible result: merged, conflicted, or left as a
      branch. Folders that aren't git get a per-folder lock instead
      (`eki/workspace.py`, `docs/worktrees.md`: one copy per thread, synced
      from your folder each run; checked live with Claude Code and Codex in
      one repo at once)
- [ ] **The top of the tree can do anything.** A run the person started — in
      a chat, from the command line, or by a schedule they set up — has every
      permission: any file, the network, push, install. It is their Mac and
      their request. Rendered as the backends' full-access modes, so it never
      stops on a prompt either
- [ ] **Permissions exist to narrow what's below it.** A run started by an
      agent through `eki` gets what its parent hands it, never more: the
      output path for `eki image`, read-only for a review, a worktree and a
      command list for a delegated coding task. Decided in eki, then rendered
      into Claude Code's permission settings, Codex's sandbox and approval
      modes, and the harness the local models borrow
- [ ] **A narrowed run is refused, not asked.** Nobody is watching a child
      run. What it isn't allowed is denied, it carries on or fails, and the
      thread says what it wanted, with *allow and rerun*
- [ ] **Roots eki starts on its own are not the person's.** Self-work opened
      by a fault follows the self-build track's autonomy setting, and the two things eki
      can't change alone stay that way at any level of the tree
- [ ] **Handoff between backends on long threads.** Replaying the store works
      until the thread outgrows the smaller model's context, and it ignores
      the CLIs' own session state. A rule for when to resume a native session
      and when to hand over a summary; who writes the summary; the summary is
      kept in the thread and can be read. Project memory (Stage 7) builds on it
- [x] **Failover on a limit.** When a subscription run hits its limit
      mid-run, the same request carries on in the row's next choice on
      another subscription — told what was written, in the same copy of the
      folder — and the thread says what moved and why (`eki/failover.py`,
      docs/routing.md). Moving *before* the wall isn't needed: credits
      carry a run past the plan, and a switch mid-run costs little
- *Evolve:* merge conflicts, refusals that stopped a child run and
  handoffs that lost something are recorded as outcomes, and the policy and
  the summary rule are adjusted from them, visibly.

## Stage 3 — Work while you're away

The machine works whenever it has room, on what you asked for, and gives way
the moment you or another app need it.

- [ ] **Goals.** A project declares what should exist (`goals.yaml`: 20 NPCs,
      each with a bio, a portrait and three lines; a world bible every piece
      reads). `eki goals` computes what's missing — the backlog is a diff, not
      a guess — in dependency order (a portrait is drawn from its bio)
- [ ] **The idle shift.** Work runs whenever the machine has room — you can
      be typing. A piece starts only if its model fits in free memory and
      other apps leave the CPU and GPU spare (read between pieces, when eki's
      own model is idle); memory pressure mid-piece cancels it and unloads the
      model. Your own requests always come first. `when: away` for anyone
      who'd rather it only ran with nobody at the keyboard. The Mac is kept
      awake while there's work (the screen may sleep)
- [ ] **Budget modes.** `local` (default: only models on this machine, zero
      subscription), `spare` (a subscription only while under pace for the
      week, never the last 30% of any window), `off`
- [ ] **The morning note.** What got made, what failed, what it cost — in
      pieces, hours and subscription share
- [ ] Measured: hours of useful local work a day, against the 1% baseline
- *Evolve:* pieces you redo or delete count against the model and template
  that made them; eki proposes the goals it sees you working toward.

## Stage 4 — The command line agents can use

So that the agent doing the work can ask eki for what it can't do itself.

- [ ] Capability commands: `eki image`, `eki write --model …`, and one per new
      kind of work as it arrives — non-interactive, `--json`, real exit codes,
      output written to a path and the path printed
- [ ] `eki capabilities`: what this Mac can do right now, for an agent to read
- [ ] `eki submit` / `eki wait <id>` for work too slow to block a shell on
- [ ] A depth guard (`EKI_DEPTH`) so an agent calling eki calling an agent
      stops somewhere, and nested calls count against quota like any other
- [ ] eki writes a short "how to use eki here" section into a project's
      `CLAUDE.md` / `AGENTS.md`, and allowlists `eki` for Claude Code
- *Evolve:* that section is generated from `eki capabilities`, so it changes
  when the Mac's backends do — nobody edits it by hand.

## Stage 5 — Projects

A project is a folder, a roster of backends, and a shared memory. Chats become
views into a project rather than the top of the tree.

- [ ] `.eki/` marker in a folder; calls made inside it belong to the project
- [ ] Project in the app: its chats, its artifacts, its files made by agents
      (today files Claude Code or Codex write into a repo don't reach the gallery)
- [ ] Per-project roster and policy: which backend does prose here, which does code
- *Evolve:* a project keeps notes on what worked — which backend was redone,
  which wasn't — and its policy is adjusted from them, visibly.

## Stage 6 — More kinds of work

Routing is already by capability; this widens what a capability can be.

- [ ] Adapters declare what they produce (code, prose, image, mesh, audio) and
      what they need; routing is capability first, then quota and cost
- [ ] A mesh backend that produces Blender assets into the project folder
- [ ] Prose models chosen by the person for the kind of writing, not by benchmark
- [ ] Text models asking for a picture mid-answer (through Stage 4's commands)
- *Evolve:* a new kind of work gets measurement items where answers can be
  checked, and outcome signals where they can't.

## Stage 7 — Project memory

- [ ] `eki remember` / `eki recall`, scoped to the project, backed by FERNme
- [ ] Canon, decisions and conventions in one graph every backend can query
- [ ] Memory shown in the app: what's there, where it came from, remove it
- *Evolve:* after a run, eki files what the run decided; stale entries decay.

## Stage 8 — Orchestration

Only what Stages 1–7 prove is missing. A driving agent (Claude Code, Codex) with
eki's commands is the first orchestrator; build a planner when it falls short.

- [ ] A task graph over runs: inputs and outputs as files, fan-out, resume
- [ ] A local model with its own tool loop, so it can call eki too
- [ ] An MCP wrapper over the same engine API, if a client without a shell needs it

## Alongside every stage — eki builds eki

The parts exist: `eki ask --repo ~/eki` already puts an agent to work on this
code, and schedules already fire runs unattended. What's missing is the closed
loop. An engine restart marks every live run *interrupted*, so the swap is
never done from inside a run: the self-work run ends, then a supervisor waits
for `running == 0` and swaps (see `docs/self-build.md`).

- [x] **eki is its own first project.** It knows where its source is, which
      checkout it is running from, and what version that is (78ed50a:
      `EKI_SOURCE`, `builds.running()`, `/api/health` → `build`)
- [ ] **"Change yourself" is a request.** (From the terminal it is: `eki self "…"`,
      dfccbdd on `self-build` — worktree, base check, routed run, commit, candidate
      check, propose only — on main since 70eb734. First real one: `self/4dab965b`,
      by Codex, fit. Still to do: saying it in a chat.) Say it in a chat — "eki, make the
      chat list show the project name" — and it is labelled as self-work and
      routed to a repo-capable agent on eki's own source, in a git worktree,
      never the checkout that's running
- [x] **Faults become requests too.** A run that failed inside eki's own code
      (a traceback, an adapter that stopped parsing a CLI's output after that
      CLI updated) opens a self-work run with the evidence attached
      (8df9b37, `eki/observe.py`, `docs/observe.md`: faults from runs, engine
      loops and request handlers; proposals only, never applied. Checked live:
      a planted KeyError in a request handler, hit twice, became a fit fix
      with a regression test, written by Claude Code)
- [x] **A candidate has to prove itself.** (`eki/candidate.py`, 57e7209,
      merged into main in 70eb734; design in `docs/self-build.md`.
      The schema check is forwards-and-readable-by-the-old-build; the Swift app
      build isn't checked yet.) Tests pass; the app builds; the
      candidate engine starts on a spare port against a copy of the database,
      answers health, completes a run on a local model, and migrates the
      schema both ways
- [x] **Swap without dropping work.** The old engine drains or hands over:
      runs either finish first or are resumable across a restart — which
      means fixing *interrupted* so a run can be picked up, not just declared
      dead. The self-work run itself is recorded as finished by the new engine
      (78ed50a, 11da108, 8a301ee: the supervisor waits for no runs; a run cut
      off anyway is carried on in its session and folder copy — checked live
      with Claude Code and Codex)
- [x] **Going back is automatic.** The previous build is kept. A supervisor
      small enough not to need changing watches the new engine; if it isn't
      healthy within minutes, the old one comes back and the thread says why
      (78ed50a: `eki/supervisor.sh`, `eki swap`; a broken build rolled back
      live in 72s. It says so in a notification — no thread line yet)
- [ ] **How far it goes alone is a setting.** *Propose* (a branch and a diff
      to read), *apply here* (swap this Mac's install after the checks), and
      per-area overrides. Default is propose (78ed50a: `self_autonomy` and
      `eki self --apply` — checked live end to end, f906934; per-area
      overrides not built)
- [x] **Two things it can't change on its own at any setting:** the supervisor
      and rollback path, because a bad change there can't be undone by them;
      and the credentials rule. Those need a person (78ed50a: `builds.py`,
      `supervisor.sh`, `agent.py`, secrets and quota are protected — never
      applied; the supervisor is only installed by `eki agent install`)
- [ ] **This Mac and the public repo are different.** What eki changes here is
      a local branch on top of the last release, rebased when a release
      lands. Offering a change upstream is a pull request; merging to `main`
      and cutting a release stays with a person, because other people
      install that
- [ ] **Everything it did to itself is visible.** A Self pane: each change,
      who asked, which agent, the diff, the checks, and *Undo*
- *Evolve:* this is the stage that lets the rest evolve. It is also the
  first thing eki should improve once it works.

## What eki keeps current about itself

The self-build track is eki changing its code. This is the other half — what it knows:

- [x] eki measures models itself, on its own, when windows are idle
- [ ] Outcomes from real use — redos, overrides, retries — adjust the same scores
- [ ] A public model-choice table in this repo, regenerated by eki from its
      measurements, committed by a schedule
- [x] Which models exist and which to use, read daily from the vendors'
      model pages (a ladder: default · top · fast) and from Ollama's popular
      models and their makers' benchmark charts — instead of working it out
      from benchmark snapshots (`eki/watch.py`, `docs/watch.md`, `eki lineup`)
- [ ] Engine manifests refreshed by eki, as a reviewable change
- [ ] The app updates itself from GitHub releases, through the self-build swap and
      rollback, keeping local changes on top
- [x] After a run, eki writes down what it learned about using a backend
      (a skill, an instruction) and uses it next time; every such note is
      listed and can be deleted — as commits in Stage 1's skill store
      (035ecd6: `eki skills learned`, `eki skills rm`; the /skills panel
      lists them as skills, without a "learned" mark yet)
- [ ] eki takes the next unchecked item in this file as self-work (the self-build track),
      at whatever autonomy is set
- [ ] eki keeps this file current: ticks what landed, with the commit
- [x] A journal of what eki notices about itself — faults, provider
      failures, friction, gaps, history — kept without a model (8df9b37,
      `eki observe`)
- [ ] A weekly note from the journal and this file: what eki noticed, and
      two or three suggestions with their evidence. It suggests; you decide,
      and what you pick becomes an item here or an `eki self` request.
      Suggestions lean to adding a provider or server, not building a part

## Not planned

- Asking two models the same thing to compare answers. Not the use case.
- Reading or reusing a CLI's login, in any form.
- Driving Claude Code with a model that isn't Anthropic's.
- Contract tests or version pinning for the CLI adapters. Claude Code and
  Codex are treated as black boxes, trusted to do the work given clear
  instructions; a break is handled as a fault (the self-build track).
- A separate spending cap. Budgets live in each provider's settings.

## Keeping this file

Anyone — a person, an agent, eki — who finishes an item ticks it and names the
commit. New ideas go under the stage they belong to, or under a new stage if
they change the order. Don't delete history; move what's abandoned to
*Not planned* with a line saying why.
