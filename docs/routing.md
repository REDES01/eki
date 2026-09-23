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
| Retry after a correction or failure | "no, standard library only" | the top model → the default → the next subscription |
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

## Tools decide the harness

The line between a model directly and a harness (Claude Code, Codex, a local
model with Codex's hands) is whether the request needs tools — files,
commands, the screen, the web, a look at this Mac ("what is taking up space
in my downloads folder"). A greeting, a haiku, an explanation need none, so
the local model answers them directly: ~4 s for "hello" against ~37 s for
the same model under Codex's instructions. A code change needs tools, so
the local model is offered there only with Codex's hands, and only for small
changes. A request that needs tools when nothing with tools can take it is
told so, not handed to a model that can't act.

`eki routing` shows each choice's typical time (the median of its recent
requests).

## Order

What can do it (running, quota, able) → your rules → the vendor's ladder →
room on each subscription. When nothing in a row can take a request, the
older cheapest-fit logic still finds something and says so.
