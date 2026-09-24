# Routing

Where a request goes is decided in two steps you can check on their own:

1. **The prompt check** — which *row* of the routing table the request is.
2. **The routing table** — where each row goes: first choice, then the
   fallbacks. The first choice that can take the request right now gets it
   (running, with quota, able to do this kind of work). Moving down the row
   is failover.

```
eki routing                      # the table, who set each row, room on each subscription
eki routing explain "fix the failing test"     # row, choices, where it would go — nothing runs
eki routing replay 40            # recent requests: where they went, where they'd go now
eki routing undo code            # take back a row you (or eki) set; "all" for everything
```

In a thread: `/routing`, and "why did that go to Codex?" — eki answers
itself, no model run.

## The table

| Kind of work | e.g. | default choices |
|---|---|---|
| Quick question | "what is 17×23", "hi" | local model → subscription's fast model |
| Writing, translation | "a haiku", "translate this" | local model → subscriptions |
| Explain, reason | "explain Python's GIL" | subscriptions → local |
| Code change | "fix the failing test" | subscriptions → local with hands (small changes only) |
| Big or hard code change | "refactor auth, update all callers", "create an RPG game" | subscriptions |
| Retry after a failure | (the answer before failed) | the default → the top model → the next subscription |
| Research on the web | "latest news on …" | subscriptions with web search |
| Picture | "a watercolor fox" | image models |

It fills itself:

- **Which model within a program** — the vendor's own guidance, read daily
  (`docs/watch.md`): Claude Code's default is Opus, top Fable, fast Haiku.
- **Which subscription first** — the one with the most room. eki learns what
  a request costs on each (the window's percentage moving between readings,
  shared across the requests that finished on it), so "50% used" on a $20
  plan and on a $200 one aren't the same any more: requests left, per hour
  until reset, decides. Until a few requests have been measured, the one
  being spent slowest against its window goes first. Credits and API keys
  come after subscriptions.

## Changing it — by doing, or by saying

- **Learned from what you do.** Pick a model by name where the table would
  have picked another, for the same kind of work, in three different threads
  within two weeks, and the row changes: *"Learned: Code change goes to Codex
  first (you picked it 3 times). Undo: eki routing undo code."* A rule you
  undo isn't learned again for a month; a row you set yourself never is.
- **Said in plain words**, in any thread — eki answers, and shows the row:
  - "use Claude for code", "use Claude for writing, not the local model"
  - "only use Codex if Claude runs out" (a backup, in every row)
  - "never use API keys" (not used automatically; still there by name)
  - "for this thread use Codex", "for today use Claude"
  - "things like this are writing" (the request before goes under that row)
  - "forget the rule about writing", "forget the rule for this thread"

  The plain forms are read without a model; anything freer is read by one
  (Claude Code when you have it).

Every row shows who set it — default, learned (and why), or you (in your
words). What eki recognises as a message *for it* is narrow on purpose:
short, not in a folder, an instruction or a question about routing naming a
model or a kind of work. "use Claude's API in this script" goes to a model.

## In a project

A project can say who works on it and who does what, in a file of its own —
`.eki/routing.yaml` at its top, kept in the repo so it reads the same for
everyone who works there:

```yaml
roster: [claude_code, codex]   # only these are chosen automatically here
prose: claude_code             # writing, translation
code: codex                    # code changes, big and small
```

It applies to requests whose folder is inside the project (a chat with a
folder, `eki ask -r`, a goal's folder). Names are the backends' keys from
`eki backends`, with a model role if you like (`claude_code@top`); besides
`prose` and `code`, any row works — `quick`, `explain`, `research`,
`picture`. The roster takes everyone else out of every row, except a row
it would leave empty (a roster of text models doesn't stop pictures); a
backend named for a kind of work goes first in that row either way. It sits
on top of your own table; "for this thread use …" still goes before it, and
a model picked by name is still used. `eki routing` run inside the project
shows its table and says which names this Mac doesn't have;
`eki routing explain -f <folder> "…"` checks one request.

## A thread stays with the model that answers

The table routes a thread's **first** message. After that the thread stays
with the model that answered — it has the context — and moves only when it
must:

- **the model hands it over.** A model with no tools of its own (the local
  one) is told what it can't do and given one thing it can: answer
  `[[handoff: claude_code | a brief]]`, and eki moves the thread to that
  harness with the brief (`eki/handoff.py`). Write a haiku: the local model.
  Make it about snow: the local model again, in two seconds, with the haiku
  in its context. How much disk space is free: handed to Claude Code.
- **you pick another** ("use Claude", or a model by name) — it stays there.
- **its answer failed** — the row's next choice (the "Retry after a
  failure" row).
- **it can't take this request** — out of quota, not running: the next in
  the row.

A program that keeps its own session (Claude Code, Codex) joining a thread
others have spoken in is given the conversation so far first, so "now make
an RPG around that haiku" knows the haiku. Setting `handoff` (on).

Checked live on 2026-09-23: haiku and rewrite on the local model (2 s each),
the disk question handed to Claude Code · Opus with a brief, the RPG idea
built on the haiku by Opus, which had been given the thread.

## Out of usage, halfway through

The table already skips a subscription whose window is spent. When one runs
out **during** a run — Claude Code's week ends halfway through building a
game — the run doesn't fail. eki (`eki/failover.py`):

- recognises the stop as a limit: Claude Code's own "rejected" rate-limit
  event (unless it's paying on past the window), or the words every
  provider uses — "usage limit reached", "hit your usage limit", 429;
- hands the same request to the row's next choice on **another**
  subscription, told what the first had written so far and to carry on —
  with the conversation so far, like any harness joining a thread;
- in the same copy of the folder: what the first changed is already there,
  and both sets of changes come back to your folder together;
- says so in the thread: what the first wrote, then
  *— Claude Code hit its usage limit (…); Codex carries on —*, then the
  rest;
- holds that subscription out for 15 minutes, so the next request doesn't
  try it first while the quota readings catch up.

Two hops at most. A model you picked by name reports its limit instead —
you chose it. Any other failure fails as before. Setting `failover` (on).

## Order

What can do it (running, quota, able) → your rules → the project's → the
vendor's ladder → room on each subscription. When nothing in a row can take a request, the
older cheapest-fit logic still finds something and says so.

## What a backend makes, and what it needs

"Able" starts with what the answer is. Each adapter declares what it makes —
`code`, `prose`, `image`, `mesh`, `audio` — and what a request must bring
before it can start (an image-to-3D model needs a picture). A request for
words skips a backend that only draws; a request for a mesh skips one that
only draws, and one that needs a picture when none is attached. Only then
do quota and cost decide. A provider's own row can say otherwise, for a
ComfyUI workflow that makes something other than pictures:

```yaml
capabilities: {text: false, produces: [mesh], needs: [image]}
```

`eki backends` and `eki_capabilities` show what each one makes.
