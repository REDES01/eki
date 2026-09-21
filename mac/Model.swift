// SPDX-License-Identifier: Apache-2.0
// App state.
//
// The window is a viewer. Sending a message asks the engine to start a run
// and then watches it; switching conversations or closing the window stops
// the *watching* and nothing else. Coming back to a conversation reattaches
// to whatever is still running in it, replayed from the first word.
import AppKit
import Combine
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    enum EngineState: Equatable {
        case checking, up, starting, down(String)
    }

    @Published var engine: EngineState = .checking
    @Published var backends: [Backend] = []
    @Published var conversations: [ConversationRow] = []
    @Published var runs: [Run] = []
    @Published var localModels: [LocalModelRow] = []
    @Published var memory: MemoryReport?
    @Published var policy = PolicyDTO()
    @Published var busyModel: String = ""
    @Published var modelMessage: String = ""
    @Published var search: String = ""
    @Published var usage: UsageReport?
    @Published var providers: [ProviderDTO] = []
    @Published var showArchived = false          // the chat list shows archived threads
    /// set to move the window to a pane (e.g. Models after starting a download)
    @Published var paneRequest: Pane?
    /// downloads in flight, shown on the Models pane while they run
    @Published var deploys: [String] = []

    // the conversation on screen
    @Published var conversationID: String = ""
    @Published var turns: [Turn] = []
    @Published var liveRun: String = ""          // the run being watched, if any
    @Published var streaming: String = ""        // its words so far
    @Published var routedTo: String = ""
    /// what the program is doing right now, a line per tool call
    @Published var activity: [String] = []
    /// a question or permission prompt the run is waiting on
    @Published var prompt: PendingPrompt? = nil
    /// slash commands for the composer, by thread (or folder, before a thread)
    @Published var commands: [SlashCommand] = []
    private var commandsKey = "\u{0}"
    @Published var chatError: String = ""
    @Published var cost: CostReport?

    // choices the user makes
    @AppStorage("preferredBackend") var preferredBackend: String = ""   // "" = let it route
    /// a model behind that provider, "" = routed per message ("claude:opus" is sent)
    @AppStorage("preferredModel") var preferredModel: String = ""
    /// the models the registry lists behind each provider, for the picker
    @Published var registry: [RegistryModel] = []
    @AppStorage("lastRepo") var lastRepo: String = ""

    let client = EngineClient()
    private var watcher: Task<Void, Never>?
    private var poll: Task<Void, Never>?

    var sending: Bool { !liveRun.isEmpty }
    var liveCount: Int { runs.filter(\.isLive).count }

    init() {
        // at launch, not when a window first appears: the menu bar item is
        // visible immediately and would otherwise sit empty until clicked
        Task { @MainActor in self.start() }
    }

    // ---- lifecycle ------------------------------------------------------

    func start() {
        guard poll == nil else { return }       // the menu bar and the window both call this
        Pref.importTokenbarIfNeeded()
        Task { await ensureEngine() }
        poll = Task { [weak self] in
            var tick = 0
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(3))
                await self?.refreshLive()
                tick += 1
                if tick % 5 == 0 { await self?.refreshUsage(force: false) }   // ~15s
            }
        }
    }

    // ---- usage ------------------------------------------------------------

    func refreshUsage(force: Bool) async {
        if let fresh = try? await client.usage(refresh: force) { usage = fresh }
    }

    /// Ask Claude Code for a reading now. Returns what to tell the user, if
    /// anything — the first run needs them to trust the probe folder.
    func probeClaude() async -> String {
        do {
            usage = try await client.probeClaude()
            return ""
        } catch ClientError.http(_, let body) {
            // FastAPI wraps the reason as {"detail": "…"}
            if let data = body.data(using: .utf8),
               let wrapped = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = wrapped["detail"] as? String {
                return detail
            }
            return body.isEmpty ? "Claude Code didn't report any limits." : body
        } catch ClientError.offline {
            return "The refresh didn't finish in time. It takes about a minute; "
                 + "if it keeps failing, ~/.eki/engine.log says why."
        } catch {
            return error.localizedDescription
        }
    }

    func setClaudeBridge(_ enabled: Bool) async {
        do {
            usage = try await client.setClaudeBridge(enabled)
        } catch {
            chatError = error.localizedDescription
        }
    }

    /// The menu bar item, drawn from the latest usage and your settings.
    func menuBarImage(style: MeterStyle? = nil, colour: MeterColour? = nil) -> NSImage {
        let d = UserDefaults.standard
        let style = style ?? MeterStyle(rawValue: d.string(forKey: Pref.meterStyle) ?? "") ?? .stacked
        let colour = colour ?? MeterColour(rawValue: d.string(forKey: Pref.meterColour) ?? "") ?? .mono
        let all = usage?.providers ?? []
        let keys = Pref.shown(from: all.map(\.provider))
        let labels = Dictionary(uniqueKeysWithValues: all.map { ($0.provider, $0.label) })
        let initials = MenuBarMeters.initials(for: keys, labels: labels)
        let inputs: [MeterInput] = keys.compactMap { key in
            guard let p = all.first(where: { $0.provider == key }) else { return nil }
            return MeterInput(key: key, initial: initials[key] ?? "?",
                              short: p.short?.used, long: p.long?.used,
                              colour: NSColor(Palette.backend(key)))
        }
        return MenuBarMeters.image(inputs, style: style, colour: colour)
    }

    /// Normally the login agent already has the engine up and this is one
    /// health check. If it isn't, nudge launchd; failing that, start one.
    func ensureEngine() async {
        if await client.health() {
            engine = .up
            await refreshAll()
            return
        }
        engine = .starting
        if Engine.bundled != nil {
            // a packaged build: launchd owns the engine, so ask it to (re)start
            if let why = Engine.enableLogin() {
                engine = .down(why)
                return
            }
            kickLaunchAgent(force: true)
            return await waitForEngine()
        }
        if !kickLaunchAgent() {
            guard let error = spawnEngine() else { return await waitForEngine() }
            engine = .down(error)
            return
        }
        await waitForEngine()
    }

    private func waitForEngine() async {
        for _ in 0..<50 {                       // ~10s, uvicorn usually takes one
            try? await Task.sleep(for: .milliseconds(200))
            if await client.health() {
                engine = .up
                await refreshAll()
                return
            }
        }
        engine = .down("The engine didn't answer on 127.0.0.1:8787 — "
                       + "`eki agent status` will say why.")
    }

    @discardableResult
    private func kickLaunchAgent(force: Bool = false) -> Bool {
        let plist = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/local.eki.engine.plist")
        guard force || FileManager.default.fileExists(atPath: plist.path) else { return false }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        task.arguments = ["kickstart", "-k", "gui/\(getuid())/local.eki.engine"]
        try? task.run()
        task.waitUntilExit()
        return task.terminationStatus == 0
    }

    /// Only when there is no login agent. Returns an error, or nil on success.
    private func spawnEngine() -> String? {
        guard let root = Self.projectRoot() else {
            return "Couldn't find the eki project next to the app."
        }
        let python = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            return "No virtualenv at \(python.path) — run ./eki.sh once."
        }
        let task = Process()
        task.executableURL = python
        task.arguments = ["-m", "eki.cli", "serve"]
        task.currentDirectoryURL = root
        // not held onto: the engine outlives the app, and so does its work
        do { try task.run() } catch { return error.localizedDescription }
        return nil
    }

    /// The project directory: the app is built into it, so it is one level up.
    static func projectRoot() -> URL? {
        let candidates = [
            Bundle.main.bundleURL.deletingLastPathComponent(),
            FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("eki"),
        ]
        return candidates.first {
            FileManager.default.fileExists(atPath: $0.appendingPathComponent("eki/cli.py").path)
        }
    }

    // ---- refreshing -----------------------------------------------------

    func refreshAll() async {
        async let b = try? await client.backends()
        async let c = try? await client.conversations(matching: search, archived: showArchived)
        async let r = try? await client.runs()
        async let m = try? await client.models()
        async let p = try? await client.policy()
        async let reg = try? await client.registry()
        backends = await b ?? []
        registry = await reg ?? registry
        conversations = await c ?? []
        runs = await r ?? []
        for run in runs where run.isLive && run.kind == "deploy" && !deploys.contains(run.id) {
            deploys.append(run.id)
        }
        if let report = await m {
            localModels = report.models
            memory = report.memory
        }
        policy = await p ?? policy
        await refreshUsage(force: false)
    }

    /// Cheap enough to do every few seconds: what's running, where.
    func refreshLive() async {
        let healthy = await client.health()
        if !healthy {
            if engine == .up { engine = .down("The engine stopped answering.") }
            return
        }
        if engine != .up {
            engine = .up
            await refreshAll()
            return
        }
        if let fresh = try? await client.runs() {
            runs = fresh
            for run in runs where run.isLive && run.kind == "deploy" && !deploys.contains(run.id) {
                deploys.append(run.id)          // a download started from the CLI, say
            }
        }
        if let fresh = try? await client.conversations(matching: search,
                                                       archived: showArchived) {
            conversations = fresh
        }
    }

    func refreshConversations() async {
        conversations = (try? await client.conversations(matching: search,
                                                          archived: showArchived)) ?? []
    }

    // ---- the chat list's menu -------------------------------------------

    func pin(_ id: String, _ on: Bool) async {
        try? await client.set(conversation: id, pinned: on)
        await refreshConversations()
    }

    func rename(_ id: String, to title: String) async {
        try? await client.set(conversation: id, title: title)
        await refreshConversations()
    }

    func archive(_ id: String, _ on: Bool) async {
        try? await client.set(conversation: id, archived: on)
        if on && conversationID == id { newConversation() }
        await refreshConversations()
    }

    /// Returns what went wrong, or nil.
    func delete(_ id: String) async -> String? {
        do {
            try await client.delete(conversation: id)
        } catch ClientError.http(_, let body) {
            if let data = body.data(using: .utf8),
               let w = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = w["detail"] as? String { return detail }
            return body
        } catch {
            return error.localizedDescription
        }
        if conversationID == id { newConversation() }
        await refreshConversations()
        runs = (try? await client.runs()) ?? runs
        return nil
    }

    func refreshModels() async {
        guard let report = try? await client.models() else { return }
        localModels = report.models
        memory = report.memory
    }

    func refreshProviders() async {
        if let fresh = try? await client.providers() { providers = fresh }
        backends = (try? await client.backends()) ?? backends
        policy = (try? await client.policy()) ?? policy
        await refreshModels()
        await refreshUsage(force: false)
    }

    func removeProvider(_ key: String) async {
        do {
            try await client.removeProvider(key)
            modelMessage = ""
        } catch {
            modelMessage = error.localizedDescription
        }
        await refreshProviders()
    }

    /// A download just started: show it where models live.
    func show(deploy id: String) {
        if !deploys.contains(id) { deploys.append(id) }
        paneRequest = .models
    }

    // ---- the conversation on screen ------------------------------------

    func newConversation() {
        detach()
        conversationID = ""
        turns = []
        chatError = ""
        cost = nil
    }

    func open(_ id: String) {
        detach()
        conversationID = id
        chatError = ""
        cost = nil
        Task { await reload(reattach: true) }
    }

    /// Re-read the thread from the engine, and pick up a run still going in it.
    private func reload(reattach: Bool) async {
        let id = conversationID
        guard !id.isEmpty else { return }
        do {
            // say so rather than showing an empty thread: a decode that quietly
            // returns [] looks exactly like a conversation with nothing in it
            let view = try await client.conversation(id)
            guard id == conversationID else { return }   // moved on meanwhile
            turns = view.turns
            if reattach, let run = view.active_run, liveRun != run {
                attach(run)
            }
        } catch {
            chatError = "couldn't load this conversation: \(error.localizedDescription)"
        }
        await refreshCost()
    }

    func refreshCost() async {
        guard !conversationID.isEmpty else { cost = nil; return }
        cost = try? await client.cost(conversationID)
    }

    func send(_ text: String, repo: String) {
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !sending else { return }
        chatError = ""
        Task {
            do {
                let asked = preferredBackend.isEmpty || preferredModel.isEmpty
                    ? preferredBackend : preferredBackend + ":" + preferredModel
                let started = try await client.ask(prompt: prompt,
                                                   conversation: conversationID,
                                                   backend: asked, repo: repo)
                conversationID = started.conversation
                // the engine has written the question; show the stored thread
                await reload(reattach: false)
                attach(started.run)
                await refreshConversations()
            } catch {
                chatError = error.localizedDescription
            }
        }
    }

    func resume(_ run: String) {
        Task {
            do {
                let started = try await client.resume(run: run)
                if started.conversation == conversationID {
                    await reload(reattach: false)
                    attach(started.run)
                }
                await refreshLive()
            } catch {
                chatError = error.localizedDescription
            }
        }
    }

    func retry(_ run: String) {
        Task {
            do {
                let started = try await client.retry(run: run)
                if started.conversation == conversationID { attach(started.run) }
                await refreshLive()
            } catch {
                chatError = error.localizedDescription
            }
        }
    }

    /// Watch a run: replayed from the first word, then live, until it ends.
    private func attach(_ run: String) {
        watcher?.cancel()
        liveRun = run
        streaming = ""
        routedTo = ""
        activity = []
        prompt = nil
        watcher = Task { [weak self] in
            guard let self else { return }
            let stream = await self.client.watch(run: run)
            do {
                for try await event in stream {
                    guard self.liveRun == run else { return }
                    switch event.event {
                    case "route":
                        self.routedTo = event.reason ?? ""
                    case "output":
                        self.streaming += event.text ?? ""
                    case "error":
                        self.chatError = event.message ?? "something went wrong"
                    case "activity":
                        if let line = event.text { self.activity.append(line) }
                    case "ask", "permission":
                        self.prompt = PendingPrompt(run: run, event: event)
                    case "cancel", "answered":
                        if self.prompt?.requestID == event.request_id { self.prompt = nil }
                    default:
                        break
                    }
                }
            } catch {
                // the engine went away mid-answer; the run's own state says how
            }
            guard self.liveRun == run else { return }
            // The engine has written the answer into the thread by now. Swap
            // the streamed text for the stored turn in one step — an await
            // between the two would show the answer twice for a frame.
            let fresh = try? await self.client.conversation(self.conversationID)
            guard self.liveRun == run else { return }
            if let fresh { self.turns = fresh.turns }
            self.streaming = ""
            self.activity = []
            self.prompt = nil
            self.liveRun = ""
            await self.refreshCost()
            await self.refreshLive()
        }
    }

    /// Stop watching. The run carries on; coming back reattaches to it.
    private func detach() {
        watcher?.cancel()
        watcher = nil
        liveRun = ""
        streaming = ""
        routedTo = ""
        activity = []
        prompt = nil
    }

    /// The named models behind a provider, enabled, for the picker.
    func modelsBehind(_ key: String) -> [RegistryModel] {
        registry.filter { $0.provider == key && $0.enabled && !$0.isDefault }
            .sorted { $0.label < $1.label }
    }

    // ---- answering the program ---------------------------------------------

    func answer(_ p: PendingPrompt, with response: [String: Any]) {
        prompt = nil
        Task {
            do {
                try await client.answer(run: p.run, request: p.requestID, response: response)
            } catch {
                chatError = "couldn't send the answer: \(error.localizedDescription)"
            }
        }
    }

    /// The commands the composer can offer: the thread's, or the folder's
    /// for a thread that hasn't started. Fetched once per thread/folder.
    @Published var commandsLoading = false

    func loadCommands(cwd: String) {
        let key = conversationID + "|" + cwd + "|" + preferredBackend
        guard key != commandsKey, !commandsLoading else { return }
        commandsLoading = true
        Task {
            defer { commandsLoading = false }
            let got = (try? await client.commands(conversation: conversationID, cwd: cwd,
                                                  backend: preferredBackend)) ?? []
            // an empty answer (engine still starting the session, or down)
            // is not remembered, so the next "/" asks again
            if !got.isEmpty { commandsKey = key }
            commands = got
        }
    }

    /// The Stop button: this one does mean the work.
    func stopRun() {
        let run = liveRun
        guard !run.isEmpty else { return }
        Task { await client.cancel(run: run) }
    }

    func cancel(_ run: String) async {
        await client.cancel(run: run)
        await refreshLive()
    }

    // ---- local models ---------------------------------------------------

    func setModel(_ key: String, running: Bool, force: Bool = false) async {
        busyModel = key
        modelMessage = ""
        defer { busyModel = "" }
        do {
            // starting 14.5GB of weights is not instant; the engine waits for
            // the port and tells us what actually happened
            modelMessage = try await client.setModel(key, running: running, force: force)
        } catch {
            modelMessage = error.localizedDescription
        }
        await refreshModels()
        backends = (try? await client.backends()) ?? backends
    }

    /// How long a local server stays loaded unused; 0 keeps it loaded.
    func setIdle(_ key: String, minutes: Double) async {
        do {
            modelMessage = try await client.setIdle(key, minutes: minutes)
        } catch {
            modelMessage = error.localizedDescription
        }
        await refreshModels()
    }

    // ---- policy ---------------------------------------------------------

    func update(policy change: PolicyDTO) async {
        do {
            policy = try await client.save(policy: change)
        } catch {
            chatError = error.localizedDescription
        }
    }

    func toggle(backend key: String, enabled: Bool) async {
        var next = policy
        next.disabled = enabled ? next.disabled.filter { $0 != key }
                                : Array(Set(next.disabled + [key])).sorted()
        await update(policy: next)
    }

    func prefer(_ key: String) async {
        var next = policy
        next.order = [key] + next.order.filter { $0 != key }
        await update(policy: next)
    }

    func setTier(_ key: String, to tier: Int?) async {
        var next = policy
        if let tier { next.tiers[key] = tier } else { next.tiers.removeValue(forKey: key) }
        await update(policy: next)
    }

    /// A folder chooser, because typing a path into a text field is a chore.
    func chooseRepo() -> String? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use folder"
        return panel.runModal() == .OK ? panel.url?.path : nil
    }
}


/// A question Claude Code asked, or a permission it wants, waiting on you.
struct PendingPrompt: Identifiable, Equatable {
    let run: String
    let requestID: String
    let kind: String                  // "ask" | "permission"
    let questions: [AskQuestion]
    let input: JSONValue
    let tool: String
    let title: String
    let description: String
    let suggestions: [JSONValue]

    var id: String { requestID }

    init(run: String, event: RunEvent) {
        self.run = run
        requestID = event.request_id ?? ""
        kind = event.event ?? "ask"
        questions = event.questions ?? []
        input = event.input ?? .object([:])
        tool = event.tool ?? ""
        title = event.title ?? ""
        description = event.description ?? ""
        suggestions = event.suggestions ?? []
    }
}
