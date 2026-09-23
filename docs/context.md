# Standing context

One set of standing instructions for every CLI. `AGENTS.md` is the standard,
so it is the one eki keeps; Claude Code's `CLAUDE.md` is an `@AGENTS.md`
import plus whatever is truly Claude's (`eki/standing.py`).

| File | What it is |
|---|---|
| `~/.eki/context/AGENTS.md` | canonical: yours, plus eki's "how to use eki here" section between `<!-- eki: … -->` markers, kept current at engine start |
| `~/.eki/context/CLAUDE.md` | `@AGENTS.md`, then Claude-only text |
| `~/.codex/AGENTS.md` | a link to the canonical file (`$CODEX_HOME` if set) |
| `~/.claude/CLAUDE.md` | a link to the Claude file |
| `~/.claude/AGENTS.md` | a link to the canonical file, so the import resolves either way |

Edit the source, never the CLIs' files: `eki context edit` (add `--claude`
for the Claude-only part). Text outside eki's markers is never rewritten.

## Files that were already there

A `~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md` eki didn't make is left
alone and reported (`eki context` says *conflict*) until you take it in:
`eki context import` puts Codex's text into `AGENTS.md` and Claude's below
the import in `CLAUDE.md` — only you can say which of it is shared, so move
what is to `AGENTS.md` — keeps the originals in `~/.eki/context/imported/`,
and links the CLIs' files back. Text already in the source isn't added
twice. A link someone else made (dotfiles) stays theirs.

## A project

`eki context project [DIR]` gives a folder the same shape: a `CLAUDE.md`
alone becomes the `AGENTS.md`, and `CLAUDE.md` starts with `@AGENTS.md`,
anything else in it kept below as Claude-only. eki only does this when
asked; it never writes into a project on its own.

## Command line

```
eki context                 # each CLI's file: linked, missing or conflict
eki context show [--claude]
eki context edit [--claude] # $EDITOR on the source, then relink
eki context import          # take the CLIs' own files in
eki context project [DIR]   # AGENTS.md canonical, CLAUDE.md an import
```
