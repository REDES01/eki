# A thin native shell, a web interface the engine serves — the plan

Design for the *Web interface in a native shell* stage of the
[roadmap](../ROADMAP.md). Nothing here is built yet; this says what moves,
what stays, and in what order.

## Why

Decided by the person on 2026-09-26. The Mac app is a Swift program of about
12,200 lines, and every change to what it shows needs a rebuild and a
relaunch. eki rebuilds the app itself after a swap (`eki/appbuild.py`), but
it waits to put the new app in place until you aren't using it — so on the
night this was decided, the app on screen was two days behind the engine it
was talking to.

A page the engine serves has no such lag. The Goals board (`eki/web/goals.html`)
already works this way: it's a page, the app gives it a place in the rail, and
it reloads itself when the file changes. So the rule from here on:

- **The app stays a Swift program only for what has to be native** — the
  window, the menu bar, the Dock, macOS permissions, trackpad gestures web
  can't match, the file panel, and starting the engine.
- **Everything you look at is a page the engine serves.** A UI change goes
  live when the engine swaps; the open window refreshes itself and keeps
  your place and your draft.

The app itself still updates, but rarely — only when the shell changes.

## 1. What's there today

Every view in `mac/*.swift`, what it does, how big it is (lines, roughly —
some files hold more than one view), and what it asks the engine.

The app talks to the engine only over HTTP on `127.0.0.1:8787`
(`mac/Client.swift`, 779 lines of request code and the data shapes the
engine returns) and streams a run's events as server-sent events
(`/api/runs/{id}/stream`). `mac/Model.swift` (873) is the state behind all
of it: polling, the open conversation, the watched run, errors. Nothing in
it does work itself — which is what makes the move possible: a page can ask
the same endpoints.

| view | file (lines) | what it does | engine endpoints |
|---|---|---|---|
| **Window and rail** — split view, chat list by age, search, pane switch, engine dot in the toolbar, rename, archive, delete | `Views.swift` ContentView, Sidebar, ChatRow, RailRow, EngineBadge, RenameSheet (~490) | the frame everything else sits in | `/api/conversations`, `/api/health`, `/api/runs` |
| **Chat thread** — the transcript, turn actions (copy, redo, diff), thinking, greeting and starter chips, cost strip, context meter, banners | `Views.swift` ChatPane, MessageView, TurnActions, DiffSheet, Thinking, CostStrip, ContextMeter, Banner, DiffView (~560) | reading and acting on a conversation | `/api/conversations/{id}`, `/api/runs/{id}/stream`, `/api/runs/{id}` (cancel, redo) |
| **Composer** — the text box, attachments strip, `@file` menu, `/` command menu, dropped and pasted pictures, repo field, backend picker | `Views.swift` Composer, AttachmentStrip, FileMenu, BackendPicker, RepoField (~450); `Live.swift` CommandMenu, MenuKeys (~60) | writing the next question | `/api/ask`, `/api/attachments`, `/api/files`, `/api/commands`, `/api/backends` |
| **Model picker** — which backend answers, with Claude Code's model and effort | `Views.swift` BackendPicker; `ClaudeCode.swift` ModelPanel (~160) | choosing who answers | `/api/backends`, `/api/agent/control` |
| **Markdown** — headings, lists, emphasis, code blocks with copy | `Markdown.swift` (276) | drawing answers | — |
| **Claude Code / Codex live cards** — the tool-call lines, ask cards, permission cards, elicitation and dialog cards, permission-need card | `Live.swift` ActivityLines, AskCard, PermissionCard (~210); `ClaudeCode.swift` ElicitationCard, DialogCard, ThinkingLines (~120); `Views.swift` PermissionNeedCard (~70) | answering the program mid-turn | `/api/runs/{id}/stream`, `/api/runs/{id}` (answers), `/api/agent/control`, `/api/access/request` |
| **Claude Code / Codex panels** — the terminal's `/mcp`, `/permissions`, `/usage`, `/context`, `/rewind`, `/tasks`, `/agents`, `/hooks`, `/status`, `/config`, `/memory`, `/skills`, `/plugins`, opened as sheets from the composer | `ClaudeCode.swift` (1,826) | the program's own screens, drawn by eki | `/api/agent/control`, `/api/mcp`, `/api/mcp/catalog`, `/api/registry`, `/api/skills`, `/api/skills/import` |
| **Artifacts panel and cards** — an html/svg/mermaid fence becomes a card; the card opens a panel beside the chat that runs it, source a click away | `Artifacts.swift` (425) | seeing what an answer made | — (drawn from the transcript); mermaid from the app bundle |
| **Gallery** — everything eki made, newest first, filters, thumbnails | `Gallery.swift` (602) | finding a page or picture again | `/api/artifacts` |
| **Inline pictures** — an image answer shown in the column; copy, save, show in Finder | `Images.swift` (262) | pictures in a thread | `/api/goals/file`-style file serving (today the app reads the file directly) |
| **Image viewer** — whole window, pinch/scroll zoom about the cursor, momentum pan, arrows | `ImageViewer.swift` (461) | one picture, properly | — (local files) |
| **Sorting mode** — in the viewer's edit mode, two-finger swipes: sideways browse, up to Trash, down to recover, with a prompt while the fingers are down | `Swipes.swift` (625) | going through pictures by hand | the engine's hide list (via the gallery) |
| **Usage** — every limit every provider reports, how old each number is | `Usage.swift` UsageRow, UsageCard, UsagePane (~300) | how much is left | `/api/usage`, `/api/usage/refresh`, `/api/usage/claude-probe`, `/api/usage/claude-bridge` |
| **Menu bar** — the meter image, and the panel under it | `MenuBarMeters.swift` (155), `Usage.swift` MenuPanel (~60) | usage at a glance | `/api/usage` |
| **Models & routing** — memory, loaded local models, routing rules | `Models.swift` (372) | what's loaded and who's picked | `/api/models`, `/api/policy`, `/api/settings` |
| **Providers and new models** — provider cards, add/replace key, test, add a model with a size slider and fit | `Providers.swift` (1,329) | adding what eki can use | `/api/providers*`, `/api/catalog*`, `/api/identify`, `/api/comfy/*`, `/api/engines`, `/api/deploy` |
| **Model scores** — measured vs prior per capability, Measure button | `Capability.swift` (283) | what each model has shown it can do | `/api/registry`, `/api/bench` |
| **Model downloads** — a setup run's card | `Downloads.swift` (101) | a model being set up | `/api/deploy`, `/api/runs/{id}/stream` |
| **Settings (⌘,)** — General (theme, accent, screen tools and the macOS permission buttons), Menu Bar (style, colour, which providers), Routing, engines list | `SettingsView.swift` (395), `Preferences.swift` (134) | how eki looks and behaves | `/api/settings`, `/api/engines`, `/api/policy`; the display choices are UserDefaults |
| **Goals / Self board** | `Goals.swift` (112) hosting `eki/web/goals.html` (889) | goals, self-work, the digest, the weekly note | `/api/goals*`, `/api/self*` — **already a web page** |
| **Onboarding** — keep running at login, add what's on this Mac, install `eki` on the PATH | `Onboarding.swift` (281) | first run | `/api/providers/discover`, `/api/registry/discover`; SMAppService and `launchctl` directly |
| **Notifications** | none in the app | — | the engine posts them itself (`Engine._notify`, `osascript`) |
| **Look and zoom** — palette, type scale, spacing, cards; ⌘+/⌘−/⌘0 scaling the layout | `Theme.swift` (511), `Scale.swift` (135) | one idea of each thing | — |
| **App entry** — the scenes, the menu commands (⌘N, ⌘R, zoom), keep running with no window | `EkiApp.swift` (101) | the app itself | — |

## 2. What stays native, and why

Native is what a page can't do, or can't do as well as the person is used to:

| stays native | why |
|---|---|
| **The window, its title bar and traffic lights; the Dock icon; the app menu** | these are the app. The page sets the title (`document.title`); the shell shows it quietly as today. |
| **The menu bar meter** (`MenuBarMeters.swift`) | a menu bar item is an `NSImage`, template-tinted by macOS. The panel *under* it becomes a small page (item 9). |
| **The engine-down screen and starting the engine** | the pages come from the engine, so when it isn't answering, the shell has to say so and offer to start it (`launchctl kickstart`, spawning it, as `Model.swift` does today). |
| **Keep running at login, `eki` on the PATH** | `SMAppService` and `launchctl` are app APIs. Onboarding's *page* is web; its buttons call the shell. |
| **macOS permission prompts** — Screen Recording, Accessibility, the System Settings deep links, showing the `eki-hid` helper | they must be asked as the app (or the engine: `/api/access/request` stays as it is). |
| **The image viewer and the sorting mode** (`ImageViewer.swift`, `Swipes.swift`) | web gets pinch as Safari gesture events and scroll as wheel events, but not the scroll *phases* (fingers down, fingers lifted, momentum) that sorting's "nothing happens until the fingers lift" is built on, nor `NSScrollView`'s magnify-about-the-cursor with rubber-banding. A page opens the viewer through the bridge with the list of pictures and where to start; the viewer is an overlay the shell draws over the page. If a later WebKit gives pages scroll phases, this can be revisited — not before. |
| **Folder and file panels** | `NSOpenPanel` as a sheet on the window (the board's *Choose…* already does this). |
| **Drag and drop, paste** | a picture dropped or pasted into a page arrives as a `File` in WebKit — the page uploads it (`/api/attachments`) with no native help. A **folder or file dropped as a path** (the repo field, `@file`) doesn't: WebKit hides paths from pages. The shell catches file-URL drops on the web view and hands the page the paths. Copying a *picture* to the pasteboard goes through the shell too (the web clipboard API wants a user gesture and can't write an `NSImage` with its file URL). |
| **Reveal in Finder, open in the default app, open a link in the browser** | `NSWorkspace`. Links that leave the engine already open in the browser (`EngineWeb`). |
| **Global keyboard shortcuts** — ⌘N, ⌘R, ⌘, and zoom | they live in the app menu so they show there and work before a page has focus. The shell forwards them to the page. Keys inside the page (Enter to send, ⇧Enter, arrows in a menu, Esc) are the page's. |
| **Context menus** | a right-click menu drawn by a page never feels like the Mac's. A page asks the shell to show an `NSMenu` at the pointer and gets the chosen item back. |
| **Display choices** — theme, accent, meter style and colour, zoom | per-viewer, in UserDefaults, as today (`Preferences.swift` says why). The page reads and sets them through the bridge; in a plain browser, `localStorage`. |
| **Notifications** | the engine already posts them; nothing moves. Clicking one could later open the right page — a shell job, noted, not planned here. |
| **Keychain** | nothing to move: API keys are put in the Keychain by the *engine* (`eki/secrets.py`); the app only shows the words. |

### The bridge

One message handler, `eki`, on the web view (`WKScriptMessageHandlerWithReply`,
as the board's `ekiPickFolder` is today), and one small module the pages
import:

```js
import { native } from "/ui/lib/native.js";
if (native.can("pickFolder")) folder = await native.call("pickFolder", { from: folder });
native.on("command", ({ name }) => { if (name === "newChat") newChat(); });
```

- **Page → shell:** `{op, args}` in, a reply or an error out. The first set:
  `pickFolder`, `pickFiles`, `reveal`, `openExternal`, `copyImage`,
  `showMenu`, `viewPictures` (the native viewer, with `sort` for the sorting
  mode), `prefs.get` / `prefs.set`, `openPrivacySettings`,
  `loginItem.state` / `loginItem.set`, `installCLI`, `relaunch`, `version`.
- **Shell → page:** events through `window.eki.receive(...)`, which the
  shell calls with `evaluateJavaScript`: `command` (menu shortcuts),
  `droppedPaths`, `appearance`, `prefs`, `viewerClosed` (what was trashed or
  recovered, so the gallery redraws).
- **Only the engine's page may ask.** The shell answers a message only from
  the main frame whose origin is the engine's (`127.0.0.1:<port>`). An
  artifact runs in a sandboxed `<iframe>` without same-origin, so it can't.
  Every op checks its arguments (a path to reveal is a real file; a URL to
  open is http, https or mailto).
- **A page never assumes the shell.** `native.can(op)` says whether the
  running shell knows an op — the shell lists its ops, with its version, at
  load. A page newer than the shell falls back (the engine's own folder
  dialog, `choose_folder`, already exists) or hides the button; in a plain
  browser there is no shell at all and every page still works. This is what
  lets the pages move every swap while the shell moves rarely.

## 3. How the pages are built and served

**Plain HTML, CSS and JavaScript modules, served as files by the engine. No
framework, no build step, no `node_modules`.** An agent changes a file, the
engine serves it, a browser shows it — nothing to install, nothing to
compile, nothing that can be out of date with the source. The Goals board
has shown this holds up for a page of real size.

What that gives up is a framework's components and reactivity; the pages
get the part that matters from the platform instead:

- **Custom elements** (`<eki-card>`, `<eki-pill>`, `<eki-markdown>`) for the
  pieces every page shares — Web Components are built into WebKit.
- **A small `html` tagged-template helper** in `lib/` that escapes by
  default, so building markup from strings is safe, plus a keyed list
  update for the long lists (the transcript, the rail) so they don't redraw
  from scratch on every event.

A framework was considered (Lit, Preact via a CDN or vendored). Vendoring
one is a dependency to keep current; loading one from a CDN puts the
window's working on someone else's server. Neither buys enough for a few
thousand lines of UI.

### Layout on disk

```
eki/web/
  ui/
    base.css           the design system: tokens and components
    lib/api.js         fetch the engine, stream a run (EventSource)
    lib/live.js        live reload, keeping your place (below)
    lib/native.js      the bridge, and its fallbacks in a browser
    lib/html.js        the tagged template, keyed lists
    lib/markdown.js    just enough Markdown (a port of Markdown.swift)
    components/*.js    <eki-card>, <eki-menu>, <eki-sheet>, …
    pages/*.js         one module per screen: chat, usage, models, …
    index.html         the window: the rail and the page it shows
    mermaid.min.js     moved from the app bundle, served the same way
  goals.html           moves under ui/ in item 1
```

The engine serves `eki/web/ui/` at `/ui/` with `Cache-Control: no-store`
(like `/goals` today) — the pages are on loopback, so caching gains
nothing and costs a stale page. Addresses are real paths
(`/ui/chat/<id>`, `/ui/usage`), so a link, the back button and a reload all
land where you were; the engine answers every `/ui/<page>…` with
`index.html`.

### One design system

`base.css` holds the tokens from `mac/Theme.swift` — the warm palette for
both appearances, the type scale (`Face`), the spacing steps (`Space`), the
radii, the shadow — as CSS custom properties, with
`@media (prefers-color-scheme: dark)` for the dark set. The board has its
own close-but-not-equal palette today; item 1 puts it on the shared one, so
there is one idea of a card, a row, a pill, a button. The accent the person
picked arrives from the shell (`prefs`) and sets `--accent`; the theme
override (always light, always dark) is the shell setting the web view's
appearance, which the page sees through `prefers-color-scheme` with no code
of its own.

A test holds it together: every colour in a page's CSS is a `var(--…)` from
`base.css` (no stray hex values), as `tests/test_mac_theme.py` does for the
Swift today.

### Live reload, keeping your place

1. The engine computes a **UI version** — a hash of the files under
   `eki/web/` (generalising `_board_version`, which does this for
   `goals.html` alone) — and puts it in `/api/health` and on a small
   server-sent event stream, `/api/ui/events`.
2. `lib/live.js` on every page holds that stream open. When the version it
   hears differs from the one the page loaded with, a new UI is live. That
   happens when a swap brings a new build (the stream drops as the engine
   restarts, reconnects, and reads the new version) or when a file is edited
   in a checkout the engine runs from.
3. The page **waits for a quiet moment** — not while a key was pressed in
   the last two seconds, an IME composition is open, a pointer is down, a
   drag is going, or a sheet has unsaved fields — then **saves its place**
   in `sessionStorage` and reloads: the address, each scrolling area's
   position (the transcript by the turn at the top of the view, not by
   pixels, since the new page may draw it at a different height), the
   composer's text and caret, attachments waiting to go, an open panel.
   After the load it puts them back before the first paint shows.
4. The composer's draft is also saved to `localStorage` as it's typed, per
   conversation, so even a crash or a quit loses nothing.
5. A run being streamed is picked up again by its id — the stream replays
   from the first event, as the app does today.

The window never flashes blank: the shell keeps the old page visible until
the new one has drawn (`WKWebView` does by default for a same-URL load), and
the engine-down screen shows only if the engine stays down for a few
seconds.

## 4. How eki checks a UI change

Tests of the Python can't see a page. A change under `eki/web/` gets its
own check in the candidate step (`eki/candidate.py`), next to the Swift
typecheck that runs when `mac/` changes.

**The browser is WebKit itself, driven by a small Swift helper** —
`mac/tools/webshot.swift`, built next to `eki-hid` by `build_app.sh`, and
compiled on demand with `swiftc` like the typecheck. It loads a page in an
offscreen `WKWebView` at a given size and appearance, runs a script in it,
collects console errors, uncaught exceptions and failed requests, and
writes a PNG. No Playwright, no Chrome, no new dependency — and it's the
same engine the window uses, so what it sees is what the person will see.
(`safaridriver` was the alternative; it needs an admin to enable it and
opens real Safari windows.)

The check, in `eki/webcheck.py`:

1. **Which pages.** `eki/web/ui/pages.json` lists each page's address and
   the fixture it needs (a conversation with a long answer, a code block, an
   artifact, a picture, a live permission card; a provider list; usage).
   The candidate engine already runs on a spare port against a copy of the
   database; the fixtures are written into that copy.
2. **Load cleanly.** Each page loads with no console error, no uncaught
   exception, no failed request to the engine, and something drawn (not a
   blank page).
3. **Before and after, light and dark.** Each page is shot against the
   running engine (before) and the candidate (after), at 1040×680, in both
   appearances: `~/.eki/shots/<change>/before-<page>-light.png` and
   `after-<page>-dark.png` — the names the board already shows with a
   change. A page whose pixels didn't change is dropped from the set, so
   the change page shows what moved.
4. **Smoke tests.** `eki/web/ui/tests/<page>.js`: short scripts run in the
   page — click the rail's second chat and see the transcript change; type
   in the composer and see *Send* enable; open the `/` menu and pick with
   the arrows; open a panel and close it with Esc; the live-reload
   round trip (type a draft, bump the version, see the draft back). They
   use the page's own elements, no test framework: a failed `assert` throws,
   and the helper reports it.
5. **Static checks in pytest,** which run everywhere, even without Xcode:
   every `import` in a module resolves to a file; no page loads anything
   from outside the engine; colours come from `base.css`; every page in
   `pages.json` has a smoke test.

A change fails the check when a page errors, draws nothing, or a smoke
test fails. A visual difference alone never fails it — that's what the
before/after pictures are for, for the person (or the reviewing agent) to
look at.

`eki/candidate.py` is on the hard-locked list, so the item that adds this
step is written by eki and applied by the person.

## 5. The order screens move

Smallest risk first. Each step is a separate roadmap item, lands on its
own, and leaves the app working: until the last steps, the native window
hosts each moved screen as a pane (`EngineWeb`, as Goals is today), so a
half-moved app is normal, not broken.

1. **The foundation.** `/ui/` served from `eki/web/ui/`; `base.css` from
   `Theme.swift`; `lib/api.js`, `lib/live.js`, `lib/native.js`,
   `lib/html.js`; the UI version and `/api/ui/events`; `EngineWeb`
   generalised with the one `eki` bridge handler (the board's folder picker
   moves onto it); the Goals board moved under `ui/` on the shared design
   system. *Done when* the board looks as it did, editing `base.css` in the
   running checkout refreshes the open board within two seconds keeping a
   half-typed goal, and the folder picker still opens as a sheet.
2. **The check.** `mac/tools/webshot.swift`, `eki/webcheck.py`, the `web`
   step in the candidate check, `pages.json` and the board's smoke test.
   *Done when* a change under `eki/web/` gets before/after shots in light
   and dark on its change page, and a planted JS error fails the check.
   (Applied by the person — it changes `candidate.py`.)
3. **Usage.** The pane becomes `/ui/usage`. Read-only, one endpoint family,
   no native needs. *Done when* it matches the native pane in both
   appearances, refresh and the Claude probe work, and `UsagePane` is gone
   from `Usage.swift`.
4. **Settings.** The ⌘, window hosts `/ui/settings`: General, Menu Bar and
   Routing, the engines list; display choices through `prefs.*`; the
   privacy buttons through `openPrivacySettings`. *Done when* every setting
   in the old window can be changed from the page and the menu bar meter
   redraws on a change, and `SettingsView.swift` is gone.
5. **Models, providers and scores.** `/ui/models`: memory, local models,
   routing, provider cards, the add-provider and add-model sheets, model
   scores, download cards. The largest forms, but no gestures and no live
   thread. *Done when* adding a provider (test, save), adding a model (fit,
   download, progress) and measuring one work from the page, and
   `Models.swift`, `Providers.swift`, `Capability.swift` and
   `Downloads.swift` are gone.
6. **The gallery.** `/ui/gallery`: the grid, filters, thumbnails (an
   artifact's thumbnail is drawn by the page in a sandboxed iframe, or by
   the engine later); a picture opens the native viewer with
   `viewPictures`, sorting mode included; what the viewer hid comes back as
   `viewerClosed`. *Done when* the gallery works as today, sorting from it
   still trashes and recovers, and `Gallery.swift` is gone.
7. **Claude Code and Codex panels.** `/ui/panel/<name>?conversation=…`,
   opened by the native composer as a sheet hosting the page: MCP, model
   and effort, permissions, usage, context, rewind, tasks, agents, hooks,
   status, config, memory, skills, plugins. *Done when* each panel does
   what it does today against a live Claude Code session and a Codex one,
   and the panels are gone from `ClaudeCode.swift` (the cards stay until
   step 8).
8. **The chat.** `/ui/chat/<id>`: the transcript with `lib/markdown.js`,
   turn actions, the diff sheet, thinking, the live cards (tool lines, ask,
   permission, elicitation, dialog, permission-need), inline pictures,
   artifact cards and the artifact panel (a sandboxed iframe, links out
   through `openExternal`), the composer with pictures by paste and drop,
   paths by `droppedPaths`, `@file` and `/` menus, the backend picker, the
   repo field, cost and context. The one step that's large; it can land as
   a page reachable at `/ui/chat` first and become the pane once it has
   done a week of real use beside the native one. *Done when* a whole day
   of chatting — Claude Code with permission prompts, Codex, a local model,
   a picture, an artifact — needs nothing from the native chat, and
   `Views.swift`'s chat, `Live.swift`, `Markdown.swift`, `Artifacts.swift`,
   `Images.swift`'s inline view and the cards in `ClaudeCode.swift` are gone.
9. **The window, onboarding and the menu-bar panel.** The rail moves into
   the page (`/ui/`), so the whole window is one web view: the page draws
   the rail over the shell's sidebar material (the web view is
   transparent there) and says which pane is showing. Onboarding becomes
   `/ui/welcome`, calling `loginItem.*` and `installCLI`. The panel under
   the menu bar meter becomes `/ui/menu`, kept loaded so it opens at once.
   *Done when* the native split view, the rail, `OnboardingSheet` and
   `MenuPanel` are gone and a fresh user can get from first launch to a
   first answer.
10. **Clean up the shell.** What's listed in section 6, deleted; the
    restart banner. *Done when* `mac/` is the shell alone and a change to
    the shell still reaches the person (section 6).

### The native feel to keep

Each step's *done* includes these, checked on the page against the native
pane it replaces:

- **Scrolling.** The page scrolls in the web view's own scroller, so
  momentum and rubber-banding are the system's; long lists scroll in
  `overflow: auto` areas, which WebKit gives the same. The transcript stays
  pinned to the bottom while an answer streams, and lets go the moment you
  scroll up — as it does now.
- **Keyboard.** ⌘N, ⌘R, ⌘, and zoom from the menu (forwarded by the
  shell); ⌘R reloads the page, keeping your place. In a page: Enter sends,
  ⇧Enter is a new line, Esc closes what's on top, the arrows move in a menu, Tab moves through
  controls in order. Focus rings where macOS draws them.
- **Text.** The system font (`-apple-system`), the sizes of `Theme.swift`'s
  type scale, system spell-check and text substitutions in the composer,
  selection that copies what it shows, monospace code in
  `ui-monospace`. Nothing drawn with a web font.
- **Dark mode.** Follows the system, or the theme picked in the app; flips
  live without a reload.
- **Zoom.** ⌘+ / ⌘− / ⌘0 set the web view's page zoom — the layout grows
  with the type, which is what `Scale.swift` does by hand today. Pinch on
  a page does nothing (only the viewer zooms).
- **Window chrome.** The native title bar and traffic lights, the quiet
  title the page sets, the sidebar's translucent material behind the rail.
  No web-drawn title bar. No WebKit context menu (*Reload*, *Inspect*),
  except in a debug build, where the Web Inspector is on.
- **Engine restarts** don't blank the window: the page keeps what it
  showed, says "reconnecting" in the engine dot, and reloads if the new
  build has a new UI.

## 6. What's left of mac/ at the end

Deleted, as their screens move: `Views.swift`, `Live.swift`,
`Markdown.swift`, `Artifacts.swift`, `Gallery.swift`, `ClaudeCode.swift`,
`Models.swift`, `Providers.swift`, `Capability.swift`, `Downloads.swift`,
`SettingsView.swift`, `Scale.swift` (zoom is the web view's), most of
`Usage.swift`, `Onboarding.swift` except `Engine` (the login item and CLI
install), most of `Client.swift` (the data shapes of every screen) and most
of `Model.swift` (the state of every screen). `Theme.swift` shrinks to the
few colours the viewer and the engine-down screen use.

What stays, about 2,000 lines of the 12,200:

| file | what it is |
|---|---|
| `EkiApp.swift` | the scenes, the app menu and its shortcuts, keep running with no window |
| `Shell.swift` (from `Goals.swift`) | the web view: loading, links out, engine-down screen, reload after a restart, appearance and zoom |
| `Bridge.swift` | the `eki` handler: its ops, the origin check, events to the page |
| `ImageViewer.swift`, `Swipes.swift`, `Images.swift` (the `Stage` and the actions) | the viewer and sorting mode |
| `MenuBarMeters.swift`, a small usage fetch | the menu bar image |
| `Preferences.swift` | display choices |
| `Engine` (from `Onboarding.swift`), engine start (from `Model.swift`) | login item, `launchctl`, spawning the engine, CLI install |
| `Theme.swift` (small) | the viewer's and the engine-down screen's look |

The bundled Python, the `eki-hid` helper, `webshot`, signing and packaging
stay as they are.

### How the shell still updates

Rarely, and the same way as today: after a healthy swap whose `mac/`
differs from what the installed app was built from, the engine rebuilds the
app (`eki/appbuild.py`) and puts it in place when you're not using it. What
changes is how you hear about it:

- `/api/health` says which shell version the engine has built
  (`appbuild.state()`), and the running shell tells the page its own
  (`native.call("version")`). When they differ, every page shows a quiet
  banner: *A new version of the app is ready — Restart*. The button calls
  `relaunch`, which starts the new app once this one has quit.
- The banner is for the shell only. A UI change never needs it — that's the
  point of the move — so seeing it is rare, and seeing it often means
  something that should be a page was put in the shell.
- A shell older than the pages keeps working: pages ask `native.can(op)`
  before using anything new (section 2), so the lag the person saw on
  2026-09-26 costs a button at worst, not a broken window.
