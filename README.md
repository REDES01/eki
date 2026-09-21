<p align="center"><img src="docs/logo.svg" width="128" height="128" alt="eki logo"></p>

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

Where it's going: [ROADMAP.md](ROADMAP.md).

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

**Pictures and artifacts.** Say *generate an image of…* and the request goes
to the image backend — eki starts ComfyUI if it's asleep — and the picture
appears in the chat; click it for a viewer with zoom, copy, save and *Show in
Finder*. An answer that contains a whole HTML page, an SVG or a Mermaid diagram
shows a card instead of a wall of source; the card opens it live in a panel
beside the chat, with the code one click away. The panel has no storage and no
file access, and links open in your browser.

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
how demanding) and a score per model per kind of work, from three sources in
order of how much they know:

1. **eki's own measurement.** The same public test items — thirty each from
   GSM8K, MATH (level 5), AIME 2025, MBPP, HumanEval, TriviaQA and MMLU-Pro,
   plus a short hand battery — put to every provider through the same
   adapters. A number, a boxed expression, a letter or a test suite says
   whether the answer is right; only the open-ended items need a local model
   as judge. Because every model sees the same items on the same harness, the
   4-bit build you actually run and a frontier model land on one scale, which
   no board offers. It runs on its own when nothing else is (*Settings →
   Routing → Measure on its own*): local models by default, Claude, Codex and
   API providers too if you say so, and only while their windows are nearly
   idle — a full run is a real bite of a 5-hour window. The items are fetched
   once from the Hugging Face datasets server into `~/.eki/bench/`.
2. **The public boards.** A snapshot of Epoch AI's benchmark hub (the closed
   frontier models) merged with Hugging Face's official leaderboard data (the
   open-weight models down to 0.8B) ships with eki, reduced to one number per
   model per kind of work, relative to the best model on each benchmark. A
   measurement of a few dozen items on this Mac outranks it; until then it
   is what separates Fable from Opus from Sonnet.
3. **The model's class**, for a model neither of the above has seen.

Each model also carries a relative cost, from its public API price where one
is listed (OpenRouter's model list; Opus sits at 5, Fable at 10, Sonnet at 2)
and from its class otherwise. The router picks, behind a provider, the
cheapest model that clears the bar — the default when it does — and names it
in the reason: "claude (opus): cheapest fit for math/hard".

A subscription's cost moves with its pace. Each window is compared with an
even burn: 90% of a 5-hour window used with four hours to go is far ahead of
pace and makes that provider ×3 dearer, so medium work goes local and the
rest of the window is there for the hard problem later; a week barely
touched by Thursday is ×0.5, cheaper than its list price, and gets spent.
Fable's own week prices Fable alone. Usage credits are what pays once a
window is gone, so they never price a provider on their own; a spent window
with credits left keeps the provider available at the dearest pace (×4,
"5H spent, on credits") and only a spent window with no credits is a wall.
The Usage pane marks where an even burn would be on each bar, and the reason
says when pace decided: "codex: cheapest fit … 5H behind pace ×0.5; claude 5H
ahead of pace ×3.1".

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

## A local model with Codex's hands

A model on this Mac has no harness of its own — no tools, no file editing,
no agent loop. Codex has one, and takes any provider that speaks OpenAI's
Responses API. eki speaks it, on its own port, for every local model it
manages (`eki/gateway.py`): Codex calls `/v1/responses` with the model's
provider key, eki starts the model if it's asleep, translates the request
into the chat call the local server understands, and turns the stream back
into the items Codex expects. So each local text model of a useful size
appears as a second provider, **Codex on <model>** — free, offline, edits
files — in the running for repo work like any other, measured like any
other, and gone when the model is removed. Nothing to register by hand.

Which model to download is answered the same way, in the Add Model sheet
under **Recommended for this Mac** (`eki/suggest.py`). Nothing else does
this: Hugging Face and the model apps say whether a build *fits*, no one
says whether it's *good*. eki joins the public boards (how each base model
scores against the best there is, medium and hard tasks only), the
mlx-community catalogue (which builds of it exist, linked to their base),
and this Mac's memory (which builds fit, with how much context) — and
sorts. For each base it picks the highest-precision build that still
leaves the Mac room and a useful window, and shows the board scores so the
choice is visible. Boards are priors: once a build is measured here, that
number is what routing uses.

**Engines are fetched, not installed.** A model is a file; something has
to run it — mlx_lm for MLX builds, llama.cpp for GGUF. eki treats those as
extensions (`eki/engines/`): each is a manifest (what it serves, a pinned
version, where it comes from, how big it is) and an installer that puts
exactly that version under `~/.eki/engines`, owned by eki — no sudo,
nothing on your PATH, nothing in your Homebrew or Python. The first model
that needs an engine fetches it as part of its own setup, with progress in
the thread; a checksum is verified before anything runs, and a failed check
leaves nothing behind. Settings → Engines shows what's here. An MLX Python
you already have (Settings → Local models) is used in preference to
fetching one.

**GGUF too.** Add Model has an MLX / GGUF switch. A GGUF repo shows its
quantisations (Q4_K_M is the default; fewer bits is smaller and faster,
Q8_0 is closest to the original); eki sizes the chosen one from the base
model's config, downloads just those files, checks each against the Hub's
sha256, and serves it with `llama-server --jinja` so tool calls work and
Codex can drive it. On Apple Silicon an MLX build of the same model is
usually faster; GGUF is for the models that only exist that way.

**Images: the workflow is the model.** ComfyUI stays wherever you
installed it; eki connects to it (Add provider → ComfyUI) and asks what it
has. A workflow is either built by eki for a model file that ComfyUI can
see — the standard checkpoint graph for SD 1.5 / SDXL, the FLUX.2-klein
graph — or your own, exported from ComfyUI as *Workflow → Export (API)*.
Either way eki reads the graph to find where the prompt, the negative
prompt, the size, the seed, the steps and a source picture go
(`eki/workflow.py`), checks it against that ComfyUI's node classes and
model files before you add it, and at request time fills the slots and
posts it. Results come back through ComfyUI's own API into `~/.eki/images`,
so its output folder is nobody's business. A workflow with a LoadImage
node can edit pictures as well as draw them.

How big and how many are said in the request, in words (`eki/imagespec.py`):
*draw 4 images of a red fox at 1536x1024*, *…, 16:9*, *…in portrait
orientation*, *a robot, x4*, *画4张…*. Those words are taken out before the
model reads the rest — handed "four images of a fox" it draws four foxes —
and go into the graph as numbers: a size is brought onto the 32-pixel grid
and under the provider's pixel limit keeping its shape, a shape alone is cut
from as many pixels as the model usually draws, and a count fills the graph's
`batch_size` (a workflow without one is queued that many times, seeds apart).
After a picture, *4 more*, *try again at 1280x720* or *same but 16:9* repeat
what made it on the new paper. Only plain statements count: *4k* is a
quality tag, *a portrait of a sailor* is a subject. `POST /api/ask` takes
`width`, `height` and `batch` outright, which win over the words, and a
provider's defaults and limits (`width`, `height`, `batch`, `max_batch` — 8 —
and `max_pixels` — 2048² —) are set with `PATCH /api/providers/<key>`
`{"options": {…}}`.

Image models are measured too (`eki/measure_images.py`): twelve fixed
prompts whose success is a list of plain facts — is there a cat, is it
left of the dog, are there exactly three apples, does the sign say OPEN —
drawn by the model and checked one picture at a time by the cheapest
model here that can see (Claude Code on a subscription before a metered
API). The share of facts that hold is the score, in the same registry as
everything else. Pictures are free; the judge isn't, so eki runs this on
its own only when that window is nearly idle.

What that model *is* is read from its own files, not typed in
(`eki/profile.py`): the build's bits and group size, its weights on disk,
its layers and how many keep a growing cache, whether its chat template
does tool calling and has a thinking switch, the author's sampling. The
profile is rebuilt every time the providers load, so a model added by hand
is described like one eki set up, and a build swapped under the same name
is noticed. A template without tool calling gets no Codex companion. What
a profile never claims is quality: a 4-bit and an 8-bit build of the same
base share a public prior and are measured separately.

How much context that model gets is worked out the same way: its own
config says what it supports, the memory beside its weights says what fits
(hybrid models like Qwen3.5/3.8 keep a growing cache in only some of their
layers, which is counted as such), and a 128k cap says where a bigger
window stops being worth the wait (`eki/context.py`). The Models pane shows
the result and the reason. Codex's own prompt is about 15k tokens, so a
model that can't be given 48k gets no companion — inside 32k Codex would
compact after a few commands, which from outside looks like a model that
stops for no reason. In a thread, a meter above the composer shows how full
the window is, and a compaction is said out loud when it happens.

Claude Code is not driven this way: it speaks only Anthropic's API, and
pointing it at another model would mean running Anthropic's program outside
what it supports.

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
