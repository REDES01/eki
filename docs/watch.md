# Which models are out there

eki doesn't work out which model is best. It reads it, once a day, from the
people who make and use the models (`eki/watch.py`), and routes by what it
read. `eki lineup` shows the current reading; `eki lineup refresh` reads now.

## Frontier models: the vendor's own guidance

Anthropic's and OpenAI's model pages say which model to start with, which is
the most capable, and which is the fast one. A reader model (Claude Code,
which can also read a chart) turns each page into a **ladder** over what the
program on this Mac can actually run:

```
claude_code — Anthropic: default: opus · top: fable · fast: sonnet
  “If you're unsure which model to use, start with Claude Opus 5.5 for most
   workloads. Use Claude Fable 5.1 for demanding reasoning … or when your
   evals on Claude Opus 5.5 at higher effort still fall short.”
```

Routing follows it:

| The request | Model |
|---|---|
| easy | fast |
| anything else, hard work included | default |
| the answer before was corrected, or failed and this is the retry | top |

**Is the program here up to date?** The vendor's page names versions;
the program on this Mac runs what *it* knows. eki asks Claude Code which
model each of its names resolves to (from its handshake — no model is asked)
and checks the newest published version of Claude Code and Codex. When the
vendor's pick is newer than what the program runs, the ladder still maps
onto the same family ("opus" is still the right *kind* of model) and the
gap is said: "its default is claude-opus-5-5, but 'opus' here runs
claude-opus-5 — 2.1.280 is out". `eki lineup update` runs the program's own
update (`claude update`; Codex by however it was installed).

The top model is also rested when its own window (Fable's week) is being
spent much faster than it lasts; the default takes its work. A model the
vendor lists that the program can't run yet is reported, not routed to
(on 2026-09-23: Codex 0.154 offers GPT-5.6 while OpenAI's page lists
GPT-6, and 0.156 is out). A program
with no ladder yet is routed as before.

## Local models: what people run, and what the makers measured

Ollama's library, sorted by popularity, is the shortlist. For each of the top
models that isn't cloud-only, eki opens its page, takes the build that fits
this Mac (MLX first, a size near the one you run), and reads the benchmark
chart in its readme — an image, so the reader looks at it. The candidate is
compared with your local model on the benchmarks both appear in; the chart
usually includes its predecessor.

- beats yours on most shared benchmarks (at least three) → **suggested**
- a newer version of what you run, but its chart doesn't compare them →
  suggested, to be **tested by eki after download** (the fallback)
- otherwise, or an older version of what you run → skipped, with why

A suggestion names its MLX build on Hugging Face; `eki lineup take NAME`
downloads and sets it up (the usual Add Model run). Nothing is downloaded on
its own. A model already read isn't read again until its page says it was
updated.

## When

At engine start if the last reading is a day old, then every day. What
changed — a new default, a model a program can't run yet, a new suggestion —
is a notification and a line in eki's journal (`eki observe --all`). A page
that can't be read is recorded, and yesterday's reading stays in use.

Setting `watch_reader`: who reads (default: Claude Code).
