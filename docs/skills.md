# Skills

One set of skills for every backend. A skill is a folder with a `SKILL.md`
(name and description in the frontmatter, instructions below, optional
scripts and files beside it) — the Agent Skills format Claude Code and Codex
both read. eki keeps the only copy; each backend gets a view.

| Backend | How it sees a skill |
|---|---|
| Claude Code | a link in `~/.claude/skills/<name>` into the store |
| Codex | a link in `~/.agents/skills/<name>` into the store (Codex's user folder) |
| Local and API models | eki is the loader: names and descriptions in the system prompt; the body only when used |

## Where things are

- `~/.eki/skills/` — the store, a git repo. Every change is a commit, so
  `eki skills log` shows what changed and `git -C ~/.eki/skills revert <c>`
  undoes it.
- `~/.eki/skills.json` — eki's notes about each skill (which backends it is
  on for, where it came from, how often a local model used it). Kept out of
  `SKILL.md` so a skill stays portable.

## Using one

- **Claude Code** — `/name`, or it picks one itself from the description.
- **Codex** — `$name` (eki also turns `/name` into `$name`), or it picks one.
- **Local / API models** — `/name` or `$name` hands the skill over outright.
  Otherwise the model sees the list and answers `[[skill:name]]` when one
  fits; eki holds that answer back and asks again with the skill's
  instructions. A reasoning model's `<think>` passes through untouched.
  Settings key `skills_local` turns this off.

`/skills` in any thread opens the panel: eki's skills with a checkbox per
backend, an editor, import, and below them what Claude Code alone sees in
that folder (plugins, project skills, synced ones).

## Command line

```
eki skills                          # list, and any skill eki doesn't hold yet
eki skills new NAME -d "when to use it" < body.md
eki skills new NAME -f path/to/SKILL.md
eki skills edit NAME                # $EDITOR, then commit and relink
eki skills off NAME [--for codex]   # everywhere, or one backend
eki skills on NAME [--for local]
eki skills import [NAME]            # take skills from the CLIs' folders in
eki skills rm NAME                  # out of the store (still in git history)
eki skills log [NAME]
eki skills learned                  # what eki learned, and its latest reviews
eki skills learn CONVERSATION       # review a thread for a skill now
```

## Skills eki learns

After a run that taught something, eki writes the lesson down as a skill —
or improves one it wrote before — and commits it to the store
(`eki/learn.py`). It only looks when there's a sign:

| Signal | What it looks like |
|---|---|
| asked | "remember this as a skill", "from now on…", "next time…", 记住, 次から |
| corrected | the message opens by correcting the answer before it: "no, use pnpm", "don't…" |
| recovered | the answer before failed or was stopped, and this one went through |

The backend that did the work reviews it in a fresh one-shot (Claude Code
and Codex from `~/.eki/learn`, never a repo); a local model reviews only
while it is up. The review usually answers *none*: a lesson has to be
reusable, concrete and earned in that run, with no secrets and nothing
one-off. What passes is a commit — `learn NAME: why` or `improve NAME: why`,
with the run and conversation in the message — and a notification.

- **What eki may change:** skills it learned itself and nobody has edited
  since. Edit one (`eki skills edit`, the panel) and it's yours; eki reads
  it but never rewrites it. `eki` (the built-in) is off limits.
- **Edits made outside eki** — an agent that changed a skill through its
  link in `~/.claude/skills`, you in an editor — are committed as their
  own change (`edit NAME by claude_code, outside eki`) when the run ends,
  so nothing is swept into eki's next commit.
- **One lesson, one place.** Claude Code and Codex each have a memory of
  their own, which would keep a second copy only that program sees. While
  eki is learning, both are told to leave remembering to eki (an added
  system prompt / developer instruction), and Codex runs with its
  `memories` feature off. If Claude Code saves a note to its auto-memory
  anyway, that is a signal of its own: the review is shown the note, and a
  note whose lesson is now a skill (new, or one that already said it) is
  moved out of Claude's memory to `~/.eki/learn/absorbed/` and dropped from
  its `MEMORY.md`. Notes that are facts about one project stay. A skill
  folder an agent writes straight into `~/.claude/skills` or
  `~/.agents/skills` is taken into the store (`take in NAME, written by
  codex`). In `propose` mode nothing is moved out of Claude's memory, since
  the skill is still off; with `skills_learn` off, both programs' own
  memory is theirs again.
- **Seeing and undoing:** `eki skills learned` lists what it learned and its
  latest reviews; `eki skills rm NAME` or `git -C ~/.eki/skills revert <c>`
  takes one back. `eki skills learn <conversation>` asks for a review of a
  thread now.

Settings: `skills_learn` — `apply` (default: on at once), `propose` (arrives
off, and a skill that is on isn't changed under you), `off`;
`skills_learn_daily` — reviews eki starts on its own per day (8; the ones you
ask for don't count); `skills_learn_backend` — who reviews ("" = whoever did
the work); `notify_learned`.

## Rules

- Only links into the store are ever made or removed. A folder in
  `~/.claude/skills` or `~/.agents/skills` that eki didn't make is left
  alone; if it has the same name as one of eki's, the panel says so.
- Import moves a real folder into the store and links it back, so the
  program sees no difference.
- `eki` — eki's own skill ("how to call eki") — is written at engine start
  for Claude Code and Codex and kept current until you edit it; then it is
  yours.
