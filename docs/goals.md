# Goals

A goal is something you'd ask in a chat, left running:

> Every morning, look at my X and propose 3 posts in my voice.
>
> Make 20 NPCs for Ashfall — a bio, a portrait and three lines each.
>
> Keep the tests in ~/eki passing; fix what breaks on a branch.

That, and optionally: **when** (once until it's done, every day, weekdays,
every week, every few hours), a **folder** to work in, whether it **may use
your subscriptions**, and whether it **may use the screen**. Nothing else to fill in — eki doesn't plan the work. The
agent that gets each turn does, the way it would in a chat.

## How it runs

- **In its own thread.** Each turn is an ordinary request in the goal's
  thread, so it remembers what it did and what you said. It's in your chat
  list like any conversation, and you answer it there — or in the reply box
  on the board.
- **Routed like anything you type.** Writing goes to the local model; work
  that needs tools (files, commands, drawing through eki) goes to a harness —
  Qwen with Codex's hands when it stays on this machine. A thread stays with
  the model that's been answering it.
- **Within its budget.** By default a goal uses only the models on this
  machine. Tick *May use my subscriptions* and it may use Claude Code or Codex —
  only while that subscription is under pace for the week, and never the last
  30% of a window.
- **When the machine has room.** A turn starts only if its model fits in free
  memory and other apps leave the CPU and GPU spare; your own requests come
  first (a goal's turn on a local model steps aside for them); only on power;
  the Mac is kept from idle-sleeping while there's work. *Only when I'm away*
  waits for nobody at the keyboard.
- **The screen only while you're away.** A goal that may use the screen
  (looking at your X timeline in the browser, say) gets eki's screen tools —
  a goal without it never does, under Claude Code or Codex. Its turns start
  only when nobody has touched the keyboard or mouse for 5 minutes and the
  Mac isn't locked; the display is kept on while it works, and it steps out
  the moment you touch anything (its own clicks and keys are told from
  yours). The screen needs a model that can see and act, so such a goal may
  use your subscriptions' spare room, and is routed to one that can see.
- **On time, or when there's room.** A repeating goal normally waits for the
  machine to have room, like any goal. Tick *On time* and its turn starts at
  the time instead (a report at 8:00) — not waiting for room or for your
  request, and a goal already working steps aside for it — still within its
  budget and only on power. A time the Mac slept through by more than six
  hours is skipped, not run stale.
- **One thread, or fresh each time.** A goal keeps one thread, so it
  remembers what it did last time. Tick *Fresh each time* and each time
  starts a new thread named after the goal and the time; your answer to one
  still carries on in that thread.
- **Until it says where it stands.** A turn ends with one line: `GOAL: done`
  (a one-off goal stops; a repeating one waits for its next time), `GOAL:
  continue` (another turn when there's room), or `GOAL: waiting` (it asked you
  something — it carries on once you reply). A one-off goal that hasn't said
  it's done after 30 turns, or fails three turns in a row, stops as *stuck*.
- **Never on its own behalf.** A goal proposes; it doesn't post, send or buy.
  Anything that acts in the world is yours to do from its thread.

Goals are also eki's timetable: what used to be a *schedule* — a request at
set times — is a repeating goal, on time and fresh each time. Schedules you
had become goals like that when the engine starts (paused ones stay paused).

eki sends a notification when a goal needs you (`GOAL: waiting`), when one is
done, when a repeating goal's run for this time ends, and when one gets
stuck — not for every turn of a goal that's carrying on (Settings:
`notify_goals`).

## The board

`http://127.0.0.1:8787/goals`, and **Goals** in the Mac app — the same page.
Write a goal at the top (the *when* is guessed from your words — "every
morning at 8" — and stays yours to change; *Choose…* picks its folder in
macOS's own dialog); each goal shows its status, its
last answer, *Run now*, *Pause*, *Delete*. Open one for its thread, *Edit*, and
a reply box.

## From the command line

```
eki goals add "Every morning, look at my X and propose 3 posts" --every day --at 08:00 --screen
eki goals add "Make 20 NPCs for Ashfall" -f ~/games/ashfall
eki goals add "Summarise yesterday's commits in ~/eki" --every weekdays --at 08:00 --on-time --fresh
eki goals                    # each goal, its status, and what the shift is doing
eki goals run|pause|resume|rm <id>
eki goals mode on|off|resources|away
eki goals report 12          # the last 12 hours: turns, outcomes, minutes, on which models
```
