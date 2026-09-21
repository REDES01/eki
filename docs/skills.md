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
```

## Rules

- Only links into the store are ever made or removed. A folder in
  `~/.claude/skills` or `~/.agents/skills` that eki didn't make is left
  alone; if it has the same name as one of eki's, the panel says so.
- Import moves a real folder into the store and links it back, so the
  program sees no difference.
- `eki` — eki's own skill ("how to call eki") — is written at engine start
  for Claude Code and Codex and kept current until you edit it; then it is
  yours.
