# hub

One place to ask, whatever ends up answering.

hub is a small Mac app and a local engine. You type a question; it works out
what the question *is* — a quick lookup, a translation, a change to your code,
a picture — and sends it to the cheapest thing on hand that can actually do it:
a model running on your own Mac, the Claude Code or Codex CLI you already pay
for, or an API you brought a key for.

Everything runs as a **run**: written down before it starts, executed by an
engine outside the window, streamed to whoever is watching. Close the app
mid-answer and the answer still finishes. Open it again and you are looking at
the same run, replayed from the first word.

- **Local first.** A model on your Mac costs nothing and leaves nothing
  behind. hub reaches for it whenever it's good enough, starts it on demand,
  and unloads it when you haven't used it for a while.
- **Nothing is hidden.** Every answer says which backend produced it and why
  that one. Usage against your Claude and Codex limits lives in the app and,
  if you want, in the menu bar.
- **Your credentials stay yours.** hub runs *your* installed CLIs as you. It
  never reads, copies or reuses their logins, and it stores no login of its
  own for them. API keys you paste go into the macOS Keychain, never into
  hub's database or config.

macOS on Apple silicon, for now.

## Install

Download `Hub-<version>.zip` from Releases, unzip, drag `Hub.app` to
Applications, open it. The app carries its own Python and engine — there is
nothing else to install.

Builds that aren't signed with an Apple Developer ID (including anything you
build yourself) need one extra step the first time: **System Settings →
Privacy & Security → Open Anyway**.

On first launch hub offers to

1. keep the engine running at login (a launch agent — the work outlives the
   window),
2. add what it finds on this Mac: Claude Code, Codex, an MLX server, Ollama,
   LM Studio, ComfyUI,
3. install a `hub` command for the terminal.

None of the three is required.

## Using it

Type. hub picks. If you want a particular backend, pick it by name from the row
of chips. Give a request a **folder** and only backends that can edit files are
considered — that's how a request becomes a code change rather than advice
about one.

**Activity** shows everything running and everything that has run, with the
output and, for a run with a folder, the diff it left behind. **Usage** shows
what's left of your limits. **Models & routing** is where providers are added,
switched off, preferred, or downloaded.

From a terminal, the same engine:

```
hub ask "summarise this file" --repo ~/code/thing
hub runs                # what's happening
hub watch <id>          # follow one
hub history -q kyoto    # search past conversations
hub models              # what's loaded, what it costs in memory
hub agent status
```

## Adding things

**A provider** is anything that can answer: Claude Code, Codex, the Anthropic
or OpenAI API, xAI, OpenRouter, an OpenAI-compatible URL (vLLM, llama.cpp,
Ollama, LM Studio), ComfyUI for images. *Models & routing → Add provider*
finds what's already on the Mac, tests before saving, and puts any key in the
Keychain.

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

"Good enough" comes from the label hub puts on the request (what kind of work,
how demanding) and a table of starting beliefs about classes of model in
`hub/priors.py` — coarse on purpose, and honest about being priors rather than
measurements.

Labelling itself is free by default: rules that read the request. A small local
model can do it instead if you set one in *Settings → Routing*; it gets about a
quarter of a second to answer and the rules take over if it doesn't. Whichever
you use, the router model is never chosen to *answer* anything.

```
python -m hub.evals.label_eval --url http://127.0.0.1:8090
```
runs both labellers over 200 seed prompts and prints accuracy and latency. The
rules were tuned against that same set, so read their score as optimistic.

## Claude and Codex usage

hub can show what's left of your five-hour and weekly Claude limits. The only
sanctioned way to know that is Claude Code's own status line, so hub offers to
add one (chaining any status line you already have); Claude Code then reports
its limits to hub as you use it. Turn it off and nothing is read. Codex
reports its own limits through its `app-server` interface.

hub does not read, copy or reuse either CLI's credentials, and does not send
your subscription anywhere it wasn't already going.

## Building it

```
./hub.sh                       # a venv and the CLI, for development
mac/build_app.sh               # the app, engine from this checkout
mac/build_app.sh --full        # self-contained: bundled Python + engine
mac/package.sh                 # dist/Hub-<version>.zip
```

Signing is one variable: without `DEVELOPER_ID` the app is signed ad-hoc and
opens after the Privacy & Security prompt; with it (and `NOTARY_PROFILE`) the
same build is signed, notarised and stapled. An Apple Developer account is only
needed for that last step, not to build or run anything here.

Tests: `.venv/bin/python -m pytest -q`.

## Layout

```
hub/            the engine: runs, router, providers, quota, adapters
hub/adapters/   one file per kind of backend
hub/quota/      reading what's left of a subscription, the sanctioned way
hub/evals/      the labelling seed set and its harness
mac/            the SwiftUI app and the build/sign/package scripts
```

MIT licensed — see `LICENSE`, and `NOTICE.md` for what hub bundles and what it
merely drives.
