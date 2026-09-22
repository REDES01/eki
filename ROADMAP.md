# Roadmap

Where eki is going, in the order it's meant to get there. The README says what
eki does today; this file says what it's for and what comes next. It is the one
plan — if something here is wrong, fix it here.

## What eki is for

First, one interface for managing and routing between models: the local ones,
the CLIs you pay for, the APIs you hold keys for, the image graphs — each used
for what it's good at, picked for you or by you.

In the end, a multi-agent framework. A real piece of work — say, an RPG — needs
a frontier agent writing the code, a story model writing the lore, an image
model making the icons, a 3D backend producing Blender assets, and a memory
that all of them share. eki is where those meet: it knows what each backend can
do, what it costs right now, and how to hand work from one to the next.

## What doesn't change

- **eki builds eki.** eki's whole job is handing work to agents that can
  change code — Claude Code, Codex, a local model with Codex's hands. Its own
  source is code like any other, so eki can change itself: take a request or
  notice a fault, dispatch an agent on its own repo, test the result, build
  it, replace the running engine and app with it, and go back if it's worse.
  This is not a feature beside the others; it is how the others get built.
  Stage 1 makes the loop; every stage after it is work eki should be doing
  on itself, and carries an *evolve* line saying what it learns to keep up.
- **eki integrates; it doesn't build the parts.** Models come from
  providers, tools come from MCP servers, instructions come from skills.
  eki is the registry of all three, the router between them, and the one
  interface — never a harness of its own and never the maker of a tool a
  provider or a server should supply. A capability a request needs (the
  web, the screen, a repo, a picture) is something a provider *has*, and
  routing finds one that has it; a gap is filled by adding a provider or
  a server, not by code in eki. The only tools eki serves are the ones
  that *are* integration: reaching another provider through eki
  (`eki_ask`, `eki_image`, `eki_capabilities`). Its screen tools are a
  stopgap until a program's own computer use works under it, and go then.
- **Your credentials stay yours.** Subscriptions are reached only by running
  the official CLIs as you. No token is read, copied or re-exposed. Ever.
- **Local first, nothing hidden.** Every answer says what produced it and why.
- **Work outlives the window.** Everything is a run, written down before it
  starts, executed by the engine.
- **Nothing raw under Auto.** A request that isn't for a picture goes to a
  harness — Claude Code, Codex, a local model with Codex's hands — never
  to a bare model, which would describe what it can't do. A bare model
  answers only when picked by name, or when no harness can take the work.
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

## Stage 1 — eki builds eki

The parts exist: `eki ask --repo ~/eki` already puts an agent to work on this
code, and schedules already fire runs unattended. What's missing is the closed
loop. An engine restart marks every live run *interrupted*, so the swap is
never done from inside a run: the self-work run ends, then a supervisor waits
for `running == 0` and swaps (see `docs/self-build.md`).

- [ ] **eki is its own first project.** It knows where its source is, which
      checkout it is running from, and what version that is
- [ ] **"Change yourself" is a request.** (From the terminal it is: `eki self "…"`,
      dfccbdd on `self-build` — worktree, base check, routed run, commit, candidate
      check, propose only. First real one: `self/4dab965b`, by Codex, fit. Still to
      do: saying it in a chat.) Say it in a chat — "eki, make the
      chat list show the project name" — and it is labelled as self-work and
      routed to a repo-capable agent on eki's own source, in a git worktree,
      never the checkout that's running
- [ ] **Faults become requests too.** A run that failed inside eki's own code
      (a traceback, an adapter that stopped parsing a CLI's output after that
      CLI updated) opens a self-work run with the evidence attached
- [x] **A candidate has to prove itself.** (`eki/candidate.py`, 57e7209 on
      branch `self-build` in `~/eki-self`; design in `docs/self-build.md` there.
      The schema check is forwards-and-readable-by-the-old-build; the Swift app
      build isn't checked yet.) Tests pass; the app builds; the
      candidate engine starts on a spare port against a copy of the database,
      answers health, completes a run on a local model, and migrates the
      schema both ways
- [ ] **Swap without dropping work.** The old engine drains or hands over:
      runs either finish first or are resumable across a restart — which
      means fixing *interrupted* so a run can be picked up, not just declared
      dead. The self-work run itself is recorded as finished by the new engine
- [ ] **Going back is automatic.** The previous build is kept. A supervisor
      small enough not to need changing watches the new engine; if it isn't
      healthy within minutes, the old one comes back and the thread says why
- [ ] **How far it goes alone is a setting.** *Propose* (a branch and a diff
      to read), *apply here* (swap this Mac's install after the checks), and
      per-area overrides. Default is propose
- [ ] **Two things it can't change on its own at any setting:** the supervisor
      and rollback path, because a bad change there can't be undone by them;
      and the credentials rule. Those need a person
- [ ] **This Mac and the public repo are different.** What eki changes here is
      a local branch on top of the last release, rebased when a release
      lands. Offering a change upstream is a pull request; merging to `main`
      and cutting a release stays with a person, because other people
      install that
- [ ] **Everything it did to itself is visible.** A Self pane: each change,
      who asked, which agent, the diff, the checks, and *Undo*
- *Evolve:* this is the stage that lets the rest evolve. It is also the
  first thing eki should improve once it works.

## Stage 2 — Runs that can be left alone

Runs are fully parallel and more of them are unattended — schedules, agents
calling eki, eki working on itself. Three things have to hold before that is
safe to widen.

- [ ] **A run that touches a repo gets its own worktree.** Today only Stage
      1's self-work does. Two agents sent to the same folder otherwise
      overwrite each other. Merge-back is an explicit step with a visible
      result: merged, conflicted, or left as a branch. Folders that aren't
      git get a per-folder lock instead
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
      by a fault follows Stage 1's autonomy setting, and the two things eki
      can't change alone stay that way at any level of the tree
- [ ] **Handoff between backends on long threads.** Replaying the store works
      until the thread outgrows the smaller model's context, and it ignores
      the CLIs' own session state. A rule for when to resume a native session
      and when to hand over a summary; who writes the summary; the summary is
      kept in the thread and can be read. Project memory (Stage 7) builds on it
- *Evolve:* merge conflicts, refusals that stopped a child run and
  handoffs that lost something are recorded as outcomes, and the policy and
  the summary rule are adjusted from them, visibly.

## Stage 3 — The command line agents can use

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

## Stage 4 — One set of skills, context and tools

Claude Code and Codex each keep their own skills, standing instructions and
MCP config, and the local models have none. eki owns one copy of each and
gives every backend a view of it. Nothing is authored inside `~/.claude` or
`~/.codex` by hand.

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
      source. Stage 3's "how to use eki here" section lands in that source
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
      it from `eki capabilities` waits for that command (Stage 3)
- [ ] **Per-project layer** once Stage 5 exists: `.eki/skills/` and the
      project's `AGENTS.md` sit on top of the global set
- [x] A Skills pane: `/skills` in any thread — eki's store with
      per-backend toggles, an editor, import; then what Claude Code alone
      sees there (plugins, project, synced). `eki skills` on the command line
- *Evolve:* a skill is the safest thing eki can change about itself. After a
  run it drafts a new skill or an edit as a commit in the skills repo —
  readable, revertible, no engine swap. This is where the "writes down what
  it learned" item below lands, and it can ship before Stage 1 is finished.

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
- [ ] Text models asking for a picture mid-answer (through Stage 3's commands)
- *Evolve:* a new kind of work gets measurement items where answers can be
  checked, and outcome signals where they can't.

## Stage 7 — Project memory

- [ ] `eki remember` / `eki recall`, scoped to the project, backed by FERNme
- [ ] Canon, decisions and conventions in one graph every backend can query
- [ ] Memory shown in the app: what's there, where it came from, remove it
- *Evolve:* after a run, eki files what the run decided; stale entries decay.

## Stage 8 — Orchestration

Only what Stages 2–7 prove is missing. A driving agent (Claude Code, Codex) with
eki's commands is the first orchestrator; build a planner when it falls short.

- [ ] A task graph over runs: inputs and outputs as files, fan-out, resume
- [ ] A local model with its own tool loop, so it can call eki too
- [ ] An MCP wrapper over the same engine API, if a client without a shell needs it

## What eki keeps current about itself

Stage 1 is eki changing its code. This is the other half — what it knows:

- [x] eki measures models itself, on its own, when windows are idle
- [ ] Outcomes from real use — redos, overrides, retries — adjust the same scores
- [ ] A public model-choice table in this repo, regenerated by eki from its
      measurements, committed by a schedule
- [ ] Board snapshots, model catalogue and engine manifests refreshed by eki,
      each as a reviewable change
- [ ] The app updates itself from GitHub releases, through Stage 1's swap and
      rollback, keeping local changes on top
- [ ] After a run, eki writes down what it learned about using a backend
      (a skill, an instruction) and uses it next time; every such note is
      listed in the app and can be deleted — as commits in
      Stage 4's skill store
- [ ] eki takes the next unchecked item in this file as self-work (Stage 1),
      at whatever autonomy is set
- [ ] eki keeps this file current: ticks what landed, with the commit

## Not planned

- Asking two models the same thing to compare answers. Not the use case.
- Reading or reusing a CLI's login, in any form.
- Driving Claude Code with a model that isn't Anthropic's.
- Contract tests or version pinning for the CLI adapters. Claude Code and
  Codex are treated as black boxes, trusted to do the work given clear
  instructions; a break is handled as a fault (Stage 1).
- A separate spending cap. Budgets live in each provider's settings.

## Keeping this file

Anyone — a person, an agent, eki — who finishes an item ticks it and names the
commit. New ideas go under the stage they belong to, or under a new stage if
they change the order. Don't delete history; move what's abandoned to
*Not planned* with a line saying why.
