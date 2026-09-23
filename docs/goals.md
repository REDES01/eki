# Goals and the idle shift

A machine bought for AI mostly sits idle. eki keeps it working — but not by
guessing what to do. A project declares what should exist, and eki makes what's
missing whenever the machine has room (ROADMAP, Stage 3).

## Declaring a goal

`goals.yaml` in the project folder:

```yaml
bible: [world.md]                  # every piece reads this
goals:
  - name: npcs
    count: 20                      # or items: [innkeeper, smith, …]
    id: "npc-{n:02}"
    dir: "npcs/{id}"
    parts:
      bio:
        kind: text
        file: bio.md
        prompt: |
          Invent a new character for this world: name as a heading, then
          role, home, temperament and one secret. Already made: {others}
      portrait:
        kind: image
        file: portrait.png
        from: [bio]
        prompt: "Painterly fantasy portrait, head and shoulders. {bio:500}"
      lines:
        kind: text
        file: lines.md
        from: [bio]
        prompt: "Three short lines this character says to a traveller. {bio}"
```

Then `eki goals add ~/games/rpg`. The backlog is the difference between this
and the files that exist — like `make`. Delete a portrait file by hand and it
gets drawn again (on the board, *Redo* does that and *Delete* keeps it gone); raise `count` and the new ones get made.

In a prompt: `{bio}` is this item's bio (`{bio:500}` its first 500
characters), `{bible}` the bible, `{others}` the first line of this part in
every other item (so the twentieth character isn't the first one again),
`{id}` / `{n}` the item. Text pieces are written under a system prompt that
carries the bible; a model's thinking never reaches the file.

## When it runs

Whenever there's room — you can keep working:

- a piece starts only if its model fits in free memory, and other apps leave
  the CPU (60%) and GPU (35%) spare. The CPU and GPU are read *between*
  pieces, when eki's own model is idle, so its own work doesn't count;
- memory pressure mid-piece cancels the piece (it's made again later) and
  unloads the model the shift loaded;
- your own requests to eki come first: one arriving cancels the piece in
  progress;
- a piece that fails rests half an hour before it's tried again;
- the Mac is kept from idle-sleeping while there's work (the screen may
  sleep).

`eki goals mode away` makes it wait for nobody at the keyboard (5 minutes),
`eki goals mode resources` goes back.

## What it may spend

| mode | uses |
|---|---|
| `local` (default) | only models on this machine — no subscription at all |
| `spare` | also a subscription, for parts marked `line: frontier`, only while the week is being spent slower than it passes and never the last 30% of any window |
| `off` | nothing |

`eki goals mode spare`. A part is local unless it says `line: frontier`
(the main character's key scene, say); `backend: <provider>` names one outright.

## The board

`http://127.0.0.1:8787/goals`, and **Goals** in the Mac app — the same page.

- **All goals**, across projects: progress, parts, what each is doing
  (working, queued, paused, done), pause and resume. Projects are added and
  removed here too.
- **A goal**: its items as a picture grid (when it makes pictures) or a list,
  a filter, pause, and *Delete everything it made* — every file to the trash,
  and the goal paused so nothing is remade until you resume it.
- **An item**: every part in full. *Redo* a part (with an optional note for
  the next making); *Delete* a part or the whole item — it goes to the trash
  and **stays deleted** until you choose *Make again*. A part's dependents
  (the portrait drawn from a bio) go with it.

Nothing is ever deleted outright: files move to the project's `.eki/trash/`.
Paused goals and deleted pieces are kept in the project's `.eki/state.json`.

## Seeing it from the command line

```
eki goals              # each project's progress, and what the shift is doing now
eki goals report 12    # the last 12 hours: made, failed, stepped out, minutes, by model
```

Every piece is also a run (`eki runs`), and the log is
`~/.eki/goals/log.jsonl`.
