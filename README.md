# eki

One place to ask, whatever ends up answering.

eki is a small Mac app and a local engine. You type a question; it works out
what the question *is* — a quick lookup, a translation, a change to your code,
a picture — and sends it to the cheapest thing on hand that can actually do it:
a model running on your own Mac, the Claude Code or Codex CLI you already pay
for, or an API you brought a key for.

Everything runs as a **run**: written down before it starts, executed by an
engine outside the window, streamed to whoever is watching. Close the app
mid-answer and the answer still finishes. Open it again and you are looking at
the same run, replayed from the first word.

- **Local first.** A model on your Mac costs nothing and leaves nothing
  behind. eki reaches for it whenever it's good enough, starts it on demand,
  and unloads it when you haven't used it for a while.
- **Nothing is hidden.** Every answer says which backend produced it and why
  that one. Usage against your Claude and Codex limits lives in the app and,
  if you want, in the menu bar.
- **Your credentials stay yours.** eki runs *your* installed CLIs as you. It
  never reads, copies or reuses their logins, and it stores no login of its
  own for them. API keys you paste go into the macOS Keychain, never into
  eki's database or config.

macOS on Apple silicon, for now.

## Install

Download `Eki-<version>.zip` from Releases, unzip, drag `Eki.app` to
Applications, open it. The app carries its own Python and engine — there is
nothing else to install.

Builds that aren't signed with an Apple Developer ID (including anything you
build yourself) need one extra step the first time: **System Settings →
Privacy & Security → Open Anyway**.

On first launch eki offers to

1. keep the engine running at login (a launch agent — the work outlives the
   window),
2. add what it finds on this Mac: Claude Code, Codex, an MLX server, Ollama,
   LM Studio, ComfyUI,
3. install a `eki` command for the terminal.

None of the three is required.

## Using it

Type. eki picks. If you want a particular backend, pick it by name from the row
of chips. Give a request a **folder** and only backends that can edit files are
considered — that's how a request becomes a code change rather than advice
about one.

Chats are named by a local model once they have an answer in them — "Why
Cats Knead Blankets" rather than the first line you typed — and a title you
set yourself is never replaced. Right-click a chat, or use the dots beside
it, to pin, rename, archive or delete it, or copy its id.

A thread being answered moves to the top of the list with a pulse beside it,
and drops back when the answer lands. **Usage** shows what's left of your
limits. **Models & routing** is where providers are added,
switched off, preferred, or downloaded.

From a terminal, the same engine:

```
eki ask "summarise this file" --repo ~/code/thing
eki runs                # every run, including ones that failed
eki watch <id>          # follow one
eki history -q kyoto    # search past conversations
eki models              # what's loaded, what it costs in memory
eki agent status
```

## Adding things

**A provider** is anything that can answer: Claude Code, Codex, the Anthropic
or OpenAI API, xAI, OpenRouter, an OpenAI-compatible URL (vLLM, llama.cpp,
Ollama, LM Studio), ComfyUI for images. *Models & routing → Add provider*
finds what's already on the Mac, tests before saving, and puts any key in the
Keychain.

A note on Codex: it edits files through a separate helper,
`codex-code-mode-host`, that the standalone install doesn't always ship —
without it Codex answers questions and declines every edit. eki says so on
the provider card and offers *Fix editing*, which fetches the helper matching
your Codex version from Codex's own GitHub release, checks Apple's signature
says it's OpenAI's, and puts it beside the `codex` binary.

**A local model**: *Add local model* searches Hugging Face's `mlx-community`,
says how much memory it will want against what's free right now, then
downloads it as a run — start script, provider record, smoke test and a
measured tokens/second at the end. It starts on demand and unloads after 30
idle minutes.

## How it picks

In order: what you asked for by name, your policy (off / preferred / retiered),
what's actually up, hard requirements (a folder needs file editing; a picture
needs an image model), live quota, then *good enough for this kind of work* —
and only then cost.

"Good enough" comes from the label eki puts on the request (what kind of work,
how demanding) and a score per model per kind of work. Scores start as
priors — starting beliefs about classes of model in `eki/priors.py`, coarse on
purpose — and are replaced by measurement: *Models…* on any provider lists
the models behind it (Claude Code's fable/opus/sonnet, Codex's list, whatever
a local server loaded) and *Measure* runs each through a short battery with
checkable answers, a local model grading the open-ended ones. A measurement
only speaks for the difficulty it was taken at; five perfect easy answers say
nothing about hard work. The router then picks the cheapest model behind a
provider that clears the bar, and names it in the reason.

Memory is read from the OS, not just from what eki loaded. A local model is
started only if it fits in what the Mac really has free (keeping 4 GB back);
otherwise the request goes to the next backend that clears its bar, with the
reason stated. To make room eki unloads only servers it started itself and
nobody is using, sooner when the Mac is under pressure, and never anything
else — Docker's memory is yours.

Labelling itself is free by default: rules that read the request. A small local
model can do it instead if you set one in *Settings → Routing*; it gets about a
quarter of a second to answer and the rules take over if it doesn't. Whichever
you use, the router model is never chosen to *answer* anything.

```
python -m eki.evals.label_eval --url http://127.0.0.1:8090
```
runs both labellers over 200 seed prompts and prints accuracy and latency. The
rules were tuned against that same set, so read their score as optimistic.

## Claude and Codex usage

eki shows what's left of your Claude plan the way Claude Code's own `/usage`
shows it: the five-hour session, the week, any per-model weekly allowance
(Fable, for example) and the usage credits you've spent past the plan.

It gets there two ways, neither of which touches your login:

- **`/usage`, on request.** *Refresh now* — or every ten minutes while the
  app is open, if you turn that on — opens Claude Code in a scratch folder,
  in plan mode with no tools, shows `/usage`, reads the panel and quits.
  `/usage` asks Anthropic about your account without asking a model
  anything, so it costs nothing. The first time, Claude Code asks whether to
  trust that folder; eki leaves that answer to you.
- **The status line, passively.** An opt-in status line records the session
  and weekly limits Claude Code reports whenever you use it yourself, so
  those two stay current between refreshes.

Codex reports its own limits through its `app-server` interface.

eki does not read, copy or reuse either CLI's credentials, and does not send
your subscription anywhere it wasn't already going.

## Building it

```
./eki.sh                       # a venv and the CLI, for development
mac/build_app.sh               # the app, engine from this checkout
mac/build_app.sh --full        # self-contained: bundled Python + engine
mac/package.sh                 # dist/Eki-<version>.zip
```

Signing is one variable: without `DEVELOPER_ID` the app is signed ad-hoc and
opens after the Privacy & Security prompt; with it (and `NOTARY_PROFILE`) the
same build is signed, notarised and stapled. An Apple Developer account is only
needed for that last step, not to build or run anything here.

Tests: `.venv/bin/python -m pytest -q`.

## Layout

```
eki/            the engine: runs, router, providers, quota, adapters
eki/adapters/   one file per kind of backend
eki/quota/      reading what's left of a subscription, the sanctioned way
eki/evals/      the labelling seed set and its harness
mac/            the SwiftUI app and the build/sign/package scripts
```

Licensed under the Apache License 2.0 — see `LICENSE` and `NOTICE`, and
`THIRD_PARTY.md` for what eki bundles and what it merely drives.
