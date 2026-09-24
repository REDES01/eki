# A copy of the folder per thread

Two agents sent into the same folder at once overwrite each other. So a run
you give a git repo works in its **thread's own copy** of it (a `git
worktree` under `~/.eki/worktrees/`), and what it changed is brought back to
your folder when it ends (`eki/workspace.py`).

## What you see

Every folder run ends with one line saying where its changes went:

| Line | Meaning |
|---|---|
| *applied N files to `~/proj`* | in your folder, uncommitted — as if it had worked there. Commits the agent made stay commits when your folder hadn't moved meanwhile |
| *didn't apply cleanly … kept on `eki/kept/<run>`* | another run, or you, changed the same lines; your folder is untouched — `git merge eki/kept/<run>` |
| *the run didn't finish … on `eki/kept/<run>`* | failed or stopped: its partial work is on the branch, not in your folder |
| (nothing) | it changed nothing |

Pictures, pages, drawings and diagrams (`.png`, `.html`, `.svg`, `.mmd`…)
a run brought back to your folder also show in the app's gallery, beside the
chat that made them. A run worked in place counts what changed in the folder
while it ran (`eki/files.py`).

The copy is detached and adds no branch to your repo; `eki/kept/…` branches
are the only ones eki leaves, and only when it says so.

## How it works

- **One copy per thread.** A Claude Code session belongs to the folder it
  runs in, so the thread keeps its copy across turns. At the start of every
  run the copy is made to match your folder *as it is then*: your
  uncommitted edits and new files come along (new files over 200 MB in all
  are left out, and the run says so).
- **Dependencies are linked, not reinstalled.** `node_modules`, `.venv`,
  `venv` and `.env*` — ignored by git, needed to run — are symlinks to
  yours.
- **The agent is told** it is working in eki's copy of your folder, and
  paths you give inside your folder mean the same files there. Paths in its
  answer are rewritten to point at your folder.
- **Bringing back is one at a time per folder**, under a lock, so two runs
  never apply into your folder at once and a copy is never synced while
  another run is applying.
- **Folders that can't be copied** — not a git repo, no commit yet,
  submodules, or eki's own copies (`eki self`) — are worked in place, and
  runs there take turns.
- Copies unused for a week are removed; `eki/kept/…` branches never are.

Setting `worktrees` (on by default): off, runs work in the folder itself,
taking turns.

## Known edges

- Claude Code's `/rewind` restores files in the copy; what was already
  brought back to your folder isn't undone by it.
- A thread that had a Claude Code session in your folder before this starts
  a fresh session the first time it works in its copy.
