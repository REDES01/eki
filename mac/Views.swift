// SPDX-License-Identifier: Apache-2.0
// The window.
//
// Shaped like the chat apps it sits beside: a quiet rail on the left, one
// centred reading column, and a composer that is the only bright thing on the
// page. The assistant's words sit directly on the canvas rather than in a
// bubble — a bubble around every answer turns a long reply into a slab.
import SwiftUI
import UniformTypeIdentifiers

enum Pane: Hashable {
    case chat(String)      // "" = a new one
    case usage
    case models
    case artifacts
    case goals
}

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var stage = Stage.shared
    @State private var pane: Pane = .chat("")
    @AppStorage(Pref.onboarded) private var onboarded: Bool = false
    @State private var showWelcome = false
    @Environment(\.zoom) private var zoom

    var body: some View {
        NavigationSplitView {
            Sidebar(pane: $pane)
                .navigationSplitViewColumnWidth(min: (Metric.rail - 40) * zoom,
                                                ideal: Metric.rail * zoom,
                                                max: (Metric.rail + 80) * zoom)
        } detail: {
            Group {
                switch pane {
                case .chat: ChatPane()
                case .usage: UsagePane()
                case .models: ModelsPane()
                case .artifacts: GalleryPane()
                case .goals: GoalsPane()
                }
            }
            .frame(minWidth: 520, minHeight: 400)
            .background(Palette.canvas)
        }
        // The top bar says where you are and nothing else: the chat's title,
        // quiet, and the engine only when it needs you.
        .modifier(QuietTitle(title: title))
        .toolbar {
            // pushes the engine's dot to the far right, away from the title
            ToolbarItem(placement: .principal) { Spacer() }
            ToolbarItem(placement: .primaryAction) { EngineBadge() }
        }
        .toolbarBackground(Palette.canvas, for: .windowToolbar)
        .overlay {
            if let picture = stage.picture { PictureViewer(picture: picture) }
        }
        .overlay(alignment: .bottom) { NoticeView() }
        .onChange(of: model.paneRequest) { _, wanted in
            if let wanted { pane = wanted; model.paneRequest = nil }
        }
        .sheet(isPresented: $showWelcome) { OnboardingSheet() }
        .task {
            model.start()
            // Only greet someone who has nothing set up yet: an upgrade from an
            // earlier build already has providers and an engine.
            await model.refreshProviders()
            if UserDefaults.standard.bool(forKey: "eki.showWelcome") {
                showWelcome = true
            } else if !onboarded {
                if model.providers.isEmpty || model.engine != .up { showWelcome = true }
                else { onboarded = true }
            }
        }
    }

    private var title: String {
        switch pane {
        case .chat: return model.chatTitle
        case .usage: return "Usage"
        case .models: return "Models & routing"
        case .artifacts: return "Artifacts"
        case .goals: return "Goals"
        }
    }
}

/// The window's title in the top bar, in the muted row type rather than the
/// system's bold. Swapping the system title out takes macOS 15; before that
/// it stays, which is still the right words.
private struct QuietTitle: ViewModifier {
    let title: String
    @Environment(\.zoom) private var zoom

    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content
                .navigationTitle(title)
                .toolbar(removing: .title)
                .toolbar {
                    ToolbarItem(placement: .navigation) {
                        Text(title)
                            .font(.hubRow.weighted(.medium))
                            .foregroundStyle(Palette.inkMuted)
                            .lineLimit(1)
                            .truncationMode(.tail)
                            .frame(maxWidth: Metric.column * zoom, alignment: .leading)
                    }
                }
        } else {
            content.navigationTitle(title)
        }
    }
}

extension AppModel {
    /// The open chat's name, for the top bar: its title as the rail has it,
    /// or its first question until the rail catches up.
    var chatTitle: String {
        if conversationID.isEmpty && turns.isEmpty { return "New chat" }
        if let row = conversations.first(where: { $0.id == conversationID }),
           let t = row.title, !t.isEmpty {
            return t.replacingOccurrences(of: "\n", with: " ")
        }
        let first = turns.first { $0.role == "user" }?.content ?? ""
        return first.isEmpty ? "eki" : String(first.prefix(80)).replacingOccurrences(of: "\n", with: " ")
    }
}

// MARK: - the rail

/// How long ago a chat was last touched, in the words the rail groups by.
enum ChatAge: String, CaseIterable {
    case today = "Today"
    case yesterday = "Yesterday"
    case week = "Previous 7 days"
    case older = "Older"

    /// Calendar days, not 24-hour spans: a chat from 11 pm is "Yesterday"
    /// at 9 the next morning, as you'd say it.
    static func of(_ date: Date?, now: Date = Date(), calendar: Calendar = .current) -> ChatAge {
        guard let date else { return .older }
        let today = calendar.startOfDay(for: now)
        if date >= today { return .today }
        guard let yesterday = calendar.date(byAdding: .day, value: -1, to: today),
              let weekAgo = calendar.date(byAdding: .day, value: -7, to: today) else { return .older }
        if date >= yesterday { return .yesterday }
        if date >= weekAgo { return .week }
        return .older
    }
}

struct Sidebar: View {
    @EnvironmentObject var model: AppModel
    @Binding var pane: Pane
    @State private var renaming: ConversationRow?

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: Space.s) {
                RailRow(icon: "square.and.pencil", title: "New chat",
                        selected: false) { choose(.chat("")) }
                SearchField(text: $model.search)
            }
            .padding(.horizontal, Space.s)
            .padding(.bottom, Space.m)

            ScrollView {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    RailRow(icon: "gauge.with.dots.needle.33percent", title: "Usage",
                            trailing: usageSummary,
                            selected: pane == .usage) { choose(.usage) }
                    RailRow(icon: "slider.horizontal.3", title: "Models & routing",
                            selected: pane == .models) { choose(.models) }
                    RailRow(icon: "square.on.square", title: "Artifacts",
                            selected: pane == .artifacts) { choose(.artifacts) }
                    RailRow(icon: "square.grid.2x2", title: "Goals",
                            selected: pane == .goals) { choose(.goals) }

                    if !model.search.isEmpty {
                        RailHeader(text: "Results")
                        rows(model.conversations)
                    } else if model.showArchived {
                        RailHeader(text: "Archived") {
                            Button("Back") { toggleArchived() }
                                .buttonStyle(.plain)
                                .font(.hubCaption.weighted(.medium))
                                .foregroundStyle(Palette.inkFaint)
                        }
                        rows(model.conversations)
                    } else {
                        // what you're waiting on comes first, then what you
                        // keep, then the rest by when you last touched it
                        let working = model.conversations.filter { $0.live == true }
                        let pinned = model.conversations.filter { $0.isPinned && $0.live != true }
                        let rest = model.conversations.filter { !$0.isPinned && $0.live != true }
                        let byAge = Dictionary(grouping: rest) { ChatAge.of($0.updated) }
                        if !working.isEmpty {
                            RailHeader(text: "Working")
                            rows(working)
                        }
                        if !pinned.isEmpty {
                            RailHeader(text: "Pinned")
                            rows(pinned)
                        }
                        ForEach(ChatAge.allCases, id: \.self) { age in
                            if let group = byAge[age], !group.isEmpty {
                                RailHeader(text: age.rawValue)
                                rows(group)
                            }
                        }
                    }
                    if model.conversations.isEmpty {
                        Text(!model.search.isEmpty ? "No matches"
                             : model.showArchived ? "Nothing archived" : "Nothing yet")
                            .font(.hubCallout)
                            .foregroundStyle(Palette.inkFaint)
                            .padding(.horizontal, Space.s)
                            .padding(.top, Space.xs)
                    }
                    if model.search.isEmpty && !model.showArchived {
                        RailRow(icon: "archivebox", title: "Archived", selected: false,
                                quiet: true) { toggleArchived() }
                            .padding(.top, Space.l)
                    }
                }
                .padding(.horizontal, Space.s)
                .padding(.bottom, Space.l)
            }
        }
        .background(Palette.rail)
        .onChange(of: model.search) {
            Task { await model.refreshConversations() }
        }
        .sheet(item: $renaming) { row in RenameSheet(row: row) }
    }

    private func rows(_ list: [ConversationRow]) -> some View {
        ForEach(list) { row in
            ChatRow(row: row, selected: pane == .chat(row.id),
                    rename: { renaming = row }) { choose(.chat(row.id)) }
        }
    }

    private func toggleArchived() {
        model.showArchived.toggle()
        Task { await model.refreshConversations() }
    }

    private func choose(_ value: Pane) {
        pane = value
        if case .chat(let id) = value {
            if id.isEmpty { model.newConversation() } else { model.open(id) }
        }
    }

    /// The fullest window across providers, as a hint beside "Usage".
    private var usageSummary: String? {
        let windows = (model.usage?.providers ?? []).flatMap(\.windows)
            .filter { $0.kind == "window" }
        guard let top = windows.max(by: { $0.used < $1.used }) else { return nil }
        return UsageFormat.percent(top.used)
    }
}

/// A heading in the rail, with room for one quiet control at its end.
struct RailHeader<Trailing: View>: View {
    let text: String
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack {
            SectionLabel(text: text)
            Spacer()
            trailing()
        }
        .padding(.horizontal, Space.s)
        .padding(.top, Space.xl)
        .padding(.bottom, Space.xs)
    }
}

extension RailHeader where Trailing == EmptyView {
    init(text: String) { self.init(text: text) { EmptyView() } }
}

struct SearchField: View {
    @Binding var text: String

    var body: some View {
        HStack(spacing: Space.s) {
            Image(systemName: "magnifyingglass")
                .font(.hubIcon)
                .foregroundStyle(Palette.inkFaint)
                .frame(width: 16)
            TextField("Search", text: $text)
                .textFieldStyle(.plain)
                .font(.hubRow)
            if !text.isEmpty {
                Button { text = "" } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.hubIcon)
                        .foregroundStyle(Palette.inkFaint)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, Space.s)
        .frame(height: Metric.control)
        .background(Palette.fill, in: RoundedRectangle(cornerRadius: Radius.small))
    }
}

struct RailRow: View {
    let icon: String
    let title: String
    var trailing: String? = nil
    var iconColor: Color? = nil
    let selected: Bool
    /// muted, for a row that's a way somewhere rather than a place
    var quiet: Bool = false
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.s) {
                Image(systemName: icon)
                    .font(icon == "circle.fill" ? .zoomed(size: 7) : .hubIcon)
                    .foregroundStyle(iconColor ?? Palette.inkMuted)
                    .frame(width: 16)
                Text(title).font(.hubRow)
                    .foregroundStyle(quiet ? Palette.inkMuted : Palette.ink)
                    .lineLimit(1)
                Spacer()
                if let trailing {
                    Text(trailing)
                        .font(.hubCaption)
                        .foregroundStyle(Palette.inkMuted)
                }
            }
            .listRow(selected: selected, hovering: hovering)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Palette.ink)
        .onHover { hovering = $0 }
    }
}

struct ChatRow: View {
    @EnvironmentObject var model: AppModel
    let row: ConversationRow
    let selected: Bool
    var rename: () -> Void = {}
    let action: () -> Void
    @State private var hovering = false
    @Environment(\.zoom) private var zoom
    @State private var confirmDelete = false
    @State private var problem = ""

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.s) {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(title)
                        .font(.hubRow)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    if let hit {
                        Text(hit)
                            .font(.hubCaption)
                            .foregroundStyle(Palette.inkFaint)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
                if row.live == true {
                    // being answered right now
                    Dot(color: Palette.ok, size: 6, pulsing: true)
                        .help("Working…")
                }
                if hovering || selected {
                    Menu { menuItems } label: {
                        IconChip(systemName: "ellipsis", fill: .clear, size: 20)
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .tint(Palette.inkMuted)
                    .fixedSize()
                } else if row.isPinned {
                    Image(systemName: "pin.fill")
                        .font(.hubGlyph)
                        .foregroundStyle(Palette.inkFaint)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(minHeight: 20 * zoom)      // as tall as the menu that shows on hover
            .listRow(selected: selected, hovering: hovering)
        }
        .help(title)
        .buttonStyle(.plain)
        .foregroundStyle(Palette.ink)
        .onHover { hovering = $0 }
        .contextMenu { menuItems }
        .confirmationDialog("Delete “\(title)”?", isPresented: $confirmDelete) {
            Button("Delete", role: .destructive) {
                Task { if let why = await model.delete(row.id) { problem = why } }
            }
        } message: {
            Text("Its \(row.n) turns go with it. Archive keeps them out of the way instead.")
        }
        .alert("Couldn't delete", isPresented: Binding(get: { !problem.isEmpty },
                                                       set: { if !$0 { problem = "" } })) {
            Button("OK") { problem = "" }
        } message: { Text(problem) }
    }

    /// One menu, two ways in: right-click anywhere, or the dots on hover.
    @ViewBuilder private var menuItems: some View {
        Button(row.isPinned ? "Unpin" : "Pin") {
            Task { await model.pin(row.id, !row.isPinned) }
        }
        Button("Rename…", action: rename)
        Button("Copy conversation ID") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(row.id, forType: .string)
        }
        Divider()
        Button(row.isArchived ? "Unarchive" : "Archive") {
            Task { await model.archive(row.id, !row.isArchived) }
        }
        Button("Delete…", role: .destructive) { confirmDelete = true }
    }

    private var title: String {
        let t = (row.title ?? "").replacingOccurrences(of: "\n", with: " ")
        return t.isEmpty ? row.id : t
    }

    /// While searching, the row says what matched under its title; otherwise
    /// a row is its title and nothing more.
    private var hit: String? {
        guard !model.search.isEmpty, let hit = row.hit else { return nil }
        let line = hit.replacingOccurrences(of: "\n", with: " ")
        return line.hasPrefix(title) ? nil : line
    }
}

struct EngineBadge: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        // a running engine is the normal case and says nothing but a dot;
        // the word appears when it's starting or gone
        HStack(spacing: Space.s) {
            Dot(color: color, size: 6, pulsing: model.engine == .starting)
            if model.engine != .up {
                Text(label)
                    .font(.hubCaption)
                    .foregroundStyle(Palette.inkMuted)
            }
        }
        .padding(.horizontal, Space.s)
        .padding(.vertical, Space.xs)
        .help(model.engine == .up ? "engine running\n" + detail : detail)
    }

    private var color: Color {
        switch model.engine {
        case .up: return Palette.ok
        case .checking, .starting: return Palette.warn
        case .down: return Palette.danger
        }
    }

    private var label: String {
        switch model.engine {
        case .up: return "engine"
        case .checking: return "checking"
        case .starting: return "starting"
        case .down: return "offline"
        }
    }

    private var detail: String {
        if case .down(let why) = model.engine { return why }
        return model.backends.map { "\($0.key) — \($0.detail)" }.joined(separator: "\n")
    }
}

// MARK: - chat

struct ChatPane: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var stage = Stage.shared
    @AppStorage("eki.artifactWidth") private var panelWidth: Double = 480
    @State private var draft: String = ""
    @State private var repo: String = ""
    @AppStorage("eki.permissionsNoticed") private var permissionsNoticed = false

    /// The chat keeps this much however wide the panel is dragged.
    private static let chatMinimum: Double = 340

    var body: some View {
        GeometryReader { geo in
            let most = max(300, Double(geo.size.width) - Self.chatMinimum)
            HStack(spacing: 0) {
                conversation
                if let artifact = stage.artifact {
                    PanelHandle(width: $panelWidth, limit: 300...most)
                    ArtifactPanel(artifact: artifact)
                        .frame(width: min(panelWidth, most))
                        .transition(.move(edge: .trailing))
                }
            }
        }
        // what's open beside one thread has no business beside the next
        .onChange(of: model.conversationID) { stage.artifact = nil }
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            if case .down(let why) = model.engine {
                Banner(text: why, tone: Palette.danger) {
                    Button("Retry") { Task { await model.ensureEngine() } }
                        .buttonStyle(GhostButton())
                }
            }
            if !permissionsNoticed {
                Banner(text: "Claude Code and Codex now run commands and edit files without "
                           + "asking. Change it in Settings → Routing → Permissions.",
                       tone: Palette.warn) {
                    Button("OK") { permissionsNoticed = true }.buttonStyle(GhostButton())
                }
            }
            if fresh {
                // a new chat: the greeting, the composer right under it, and
                // a few ways to begin — the whole page is the invitation
                VStack(spacing: Space.xl) {
                    Spacer(minLength: 0)
                    Greeting()
                    footer
                    StarterChips { model.draftRequest = $0 }
                    Spacer(minLength: 0)
                    Spacer(minLength: 0)
                }
            } else {
                transcript
                footer
            }
        }
        .background(Palette.canvas)
        .onAppear { repo = model.lastRepo }
        // the terminal's panels, drawn here (see ClaudeCode.swift)
        .sheet(item: $model.claudePanel) { panel in
            ClaudePanelSheet(panel: panel).environmentObject(model)
        }
    }

    /// Nothing said yet and nothing on its way.
    private var fresh: Bool {
        model.turns.isEmpty && model.streaming.isEmpty && !model.sending
            && model.prompt == nil && model.chatError.isEmpty
    }

    @ViewBuilder private var footer: some View {
        if let need = model.permissionNeed {
            PermissionNeedCard(need: need)
        }
        if !model.notice.isEmpty {
            Text(model.notice)
                .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                .padding(.horizontal, Space.m).padding(.vertical, Space.xs)
                .background(Palette.fill, in: Capsule())
                .transition(.opacity)
        }
        Composer(draft: $draft, repo: $repo)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Metric.turn) {
                    ForEach(model.turns) { turn in
                        MessageView(turn: turn)
                    }
                    if !model.streaming.isEmpty {
                        MessageView(turn: Turn(id: 0, role: "assistant",
                                               content: model.streaming,
                                               backend: routedBackend,
                                               reason: model.routedTo),
                                    streaming: true)
                    }
                    if !model.thinking.isEmpty && model.sending && model.streaming.isEmpty {
                        ThinkingLines(text: model.thinking)
                    }
                    if !model.activity.isEmpty && model.sending {
                        ActivityLines(lines: model.activity)
                    }
                    if let prompt = model.prompt {
                        switch prompt.kind {
                        case "ask": AskCard(prompt: prompt).id(prompt.id)
                        case "elicitation": ElicitationCard(prompt: prompt).id(prompt.id)
                        case "dialog": DialogCard(prompt: prompt).id(prompt.id)
                        default: PermissionCard(prompt: prompt).id(prompt.id)
                        }
                    } else if model.sending && model.streaming.isEmpty {
                        Thinking(reason: model.routedTo)
                    }
                    if !model.chatError.isEmpty {
                        Banner(text: model.chatError, tone: Palette.danger) { EmptyView() }
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .column(alignment: .leading)
                .frame(maxWidth: .infinity)          // centre the column
                .padding(.horizontal, Metric.gutter)
                .padding(.vertical, Space.xxl)
            }
            // the page fades into the composer rather than stopping at a line
            .overlay(alignment: .bottom) {
                LinearGradient(colors: [Palette.canvas.opacity(0), Palette.canvas],
                               startPoint: .top, endPoint: .bottom)
                    .frame(height: Space.xl)
                    .allowsHitTesting(false)
            }
            // opened from a new chat, the transcript is made with its turns
            // already in, so no count changes: start at the latest here
            .onAppear {
                DispatchQueue.main.async { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: model.turns.count) {
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("bottom") }
            }
            .onChange(of: model.streaming) { proxy.scrollTo("bottom") }
        }
    }

    /// The router names the backend before the first token arrives.
    private var routedBackend: String? {
        model.routedTo.split(separator: ":").first.map(String.init)
    }
}

/// The top of a new chat: eki's mark and one line, large and light.
struct Greeting: View {
    var body: some View {
        HStack(spacing: Space.m) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable()
                .interpolation(.high)
                .frame(width: 40, height: 40)
            Text("What are we doing?")
                .font(.hubGreeting)
                .foregroundStyle(Palette.ink)
        }
        .help("Auto sends this to the cheapest backend that can do the job and still has "
              + "quota. Give it a folder and only the ones that can edit files are considered.")
        .padding(.horizontal, Metric.gutter)
    }
}

/// A few ways to begin, under the composer on a new chat. Each one starts
/// the question for you; you finish it.
struct StarterChips: View {
    let pick: (String) -> Void

    private static let all: [(icon: String, label: String, start: String)] = [
        ("pencil.line", "Write", "Help me write "),
        ("chevron.left.forwardslash.chevron.right", "Code", "In this folder, "),
        ("photo", "Draw", "Draw a picture of "),
        ("lightbulb", "Explain", "Explain "),
    ]

    var body: some View {
        HStack(spacing: Space.s) {
            ForEach(Self.all, id: \.label) { s in
                Button { pick(s.start) } label: {
                    HStack(spacing: Space.xs) {
                        Image(systemName: s.icon).font(.hubIconSmall)
                        Text(s.label)
                    }
                    .foregroundStyle(Palette.inkMuted)
                    .pill(fill: .clear)
                    .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, Metric.gutter)
    }
}

struct MessageView: View {
    let turn: Turn
    var streaming: Bool = false
    @Environment(\.zoom) private var zoom

    private var isUser: Bool { turn.role == "user" }

    var body: some View {
        if isUser {
            // the question: a soft filled bubble on the right, never wider
            // than four fifths of the column, so the two voices read apart
            Text(turn.content)
                .font(.hubMessage)
                .lineSpacing(Metric.leading * zoom)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Metric.bubbleX)
                .padding(.vertical, Metric.bubbleY)
                .background(Palette.fill, in: RoundedRectangle(cornerRadius: Radius.large))
                .padding(.leading, Metric.indent)
                .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            // the answer: plain text on the page; who wrote it is said once,
            // small, at the end, with the actions
            VStack(alignment: .leading, spacing: Space.m) {
                MarkdownText(content: turn.content)
                if streaming {
                    Dot(color: Palette.backend(turn.backend ?? ""), size: 7, pulsing: true)
                } else {
                    TurnActions(turn: turn)
                }
            }
        }
    }
}

/// The small row under an answer: copy it, see what it changed, redo it.
struct TurnActions: View {
    @EnvironmentObject var model: AppModel
    let turn: Turn
    @State private var showingDiff = false
    @State private var diff = ""
    @State private var copied = false

    var body: some View {
        HStack(spacing: Space.xxs) {
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(turn.content, forType: .string)
                copied = true
                Task { try? await Task.sleep(for: .seconds(1.5)); copied = false }
            } label: {
                Label(copied ? "Copied" : "Copy",
                      systemImage: copied ? "checkmark" : "doc.on.doc")
            }
            .buttonStyle(GhostButton())
            .help(copied ? "Copied" : "Copy")

            // an answer that worked in a folder may have changed files there;
            // the change is what you actually need to look at
            if let run = turn.run, let cwd = turn.cwd, !cwd.isEmpty {
                Button {
                    Task {
                        diff = (try? await model.client.diff(run: run)) ?? ""
                        showingDiff = true
                    }
                } label: {
                    Label("Review changes", systemImage: "plusminus")
                }
                .buttonStyle(GhostButton())
                .help("Review changes in " + cwd)
            }

            if turn.checkpoint != nil, let cwd = turn.cwd, !cwd.isEmpty {
                Button {
                    Task {
                        if let r = await model.claude("rewind", args: ["turn": turn.id], timeout: 120) {
                            model.say(rewindWord(r))
                        }
                    }
                } label: {
                    Label("Rewind files", systemImage: "arrow.uturn.backward")
                }
                .buttonStyle(GhostButton())
                .help("Put the folder back as it was before this answer (Claude Code's /rewind)")
            }

            if let run = turn.run, turn.wasInterrupted {
                Button {
                    model.resume(run)
                } label: {
                    Label("Resume", systemImage: "play.fill")
                }
                .buttonStyle(GhostButton())
                .help("Tell the program to carry on where it left off")
            } else if let run = turn.run, turn.didNotFinish {
                Button {
                    model.retry(run)
                } label: {
                    Label("Retry", systemImage: "arrow.clockwise")
                }
                .buttonStyle(GhostButton())
                .help("Retry")
            }

            // who answered, once, small; why it was them on hover
            if let backend = turn.backend, !backend.isEmpty {
                Text(backend)
                    .font(.hubCaption)
                    .foregroundStyle(Palette.inkFaint)
                    .padding(.leading, Space.s)
                    .help(turn.reason ?? backend)
            }
            if turn.continued {
                Tag(text: "carried on by itself", color: Palette.accent)
                    .padding(.leading, Space.xs)
                    .help("The program did this on its own — a background task finished "
                          + "and it picked up where it left off, the way it would in a terminal.")
            }
        }
        .labelStyle(.iconOnly)
        .padding(.leading, -Space.s.value)        // align the ghost text with the answer
        .sheet(isPresented: $showingDiff) {
            DiffSheet(folder: turn.cwd ?? "", diff: diff)
        }
    }
}

/// What the program said about a rewind, in a line: done, or why not
/// ("File rewinding is not enabled" until checkpointing is on in its settings).
func rewindWord(_ r: JSONValue) -> String {
    let reply: JSONValue = r["rewind"] ?? r
    if let err = reply["error"]?.stringValue, !err.isEmpty { return err }
    if reply["canRewind"]?.boolValue == false { return "Claude Code can't rewind this turn" }
    return "Files put back as before this answer"
}

struct DiffSheet: View {
    let folder: String
    let diff: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SheetHeader(title: "Changes", subtitle: folder) {
                Button("Done") { dismiss() }
                    .buttonStyle(AccentButton())
                    .keyboardShortcut(.defaultAction)
            }
            Hairline()
            ScrollView {
                DiffView(text: diff).padding(.all, Space.l)
            }
            // what the repo holds now, which may include edits made since —
            // said here so a stale-looking diff isn't a mystery
            Text("As the folder is now, against its last commit.")
                .font(.hubCaption)
                .foregroundStyle(Palette.inkFaint)
                .padding(.horizontal, Space.l)
                .padding(.bottom, Space.m)
        }
        .frame(minWidth: 640, minHeight: 440)
        .background(Palette.canvas)
    }
}

struct Thinking: View {
    let reason: String
    @State private var phase = 0.0

    var body: some View {
        HStack(spacing: Space.s) {
            Circle()
                .fill(Palette.accent)
                .frame(width: 7, height: 7)
                .scaleEffect(0.6 + 0.4 * phase)
                .opacity(0.45 + 0.55 * phase)
            Text(reason.isEmpty ? "routing…" : reason)
                .font(.hubCallout)
                .foregroundStyle(Palette.inkMuted)
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 0.75).repeatForever(autoreverses: true)) {
                phase = 1
            }
        }
    }
}

// MARK: - composer

struct Composer: View {
    @EnvironmentObject var model: AppModel
    @Binding var draft: String
    @Binding var repo: String
    @FocusState private var focused: Bool
    @State private var picked = 0
    @State private var keys = MenuKeys()
    @Environment(\.zoom) private var zoom

    private var empty: Bool {
        draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Typing "/" lists Claude Code's commands — the ones it reports for
    /// this thread — narrowed as you type. ↑↓ move, Tab or ↩ complete.
    private var suggestions: [SlashCommand] {
        guard draft.hasPrefix("/"), !draft.contains(where: \.isWhitespace) else { return [] }
        let typed = String(draft.dropFirst()).lowercased()
        // plain commands before plugin-namespaced ones, prefix matches first
        let ordered = model.commands.sorted { ($0.name.contains(":") ? 1 : 0, $0.name)
                                             < ($1.name.contains(":") ? 1 : 0, $1.name) }
        let starts = ordered.filter { $0.name.lowercased().hasPrefix(typed) }
        let within = ordered.filter {
            !$0.name.lowercased().hasPrefix(typed) && $0.name.lowercased().contains(typed)
        }
        return starts + within
    }

    /// While the menu shows: ↑↓ move, Tab completes, ↩ completes (or sends
    /// a command already complete), Esc closes.
    private func handleKey(_ event: NSEvent) -> Bool {
        let list = suggestions
        guard !list.isEmpty, focused else { return false }
        let current = min(picked, list.count - 1)
        switch event.keyCode {
        case 125: picked = min(list.count - 1, current + 1); return true           // ↓
        case 126: picked = max(0, current - 1); return true                        // ↑
        case 48: complete(list[current]); return true                              // ⇥
        case 36 where !event.modifierFlags.contains(.shift):                       // ↩
            submit(); return true
        case 53: draft = ""; return true                                           // ⎋
        default: return false
        }
    }

    private func complete(_ c: SlashCommand) {
        draft = "/" + c.name + ((c.argumentHint ?? "").isEmpty ? "" : " ")
        picked = 0
    }

    private func submit() {
        if !suggestions.isEmpty, draft.dropFirst().lowercased() != suggestions[min(picked, suggestions.count - 1)].name.lowercased() {
            complete(suggestions[min(picked, suggestions.count - 1)])
        } else {
            send()
        }
    }

    var body: some View {
        VStack(spacing: Space.s) {
            if (model.cost?.turns ?? 0) > 0 || model.contextUse != nil {
                CostStrip(cost: model.cost, context: model.contextUse)
            }
            VStack(spacing: 0) {
                if !suggestions.isEmpty {
                    CommandMenu(commands: suggestions, picked: min(picked, suggestions.count - 1)) { c in
                        complete(c)
                    }
                    Hairline()
                } else if draft.hasPrefix("/"), !draft.contains(where: \.isWhitespace), model.commands.isEmpty {
                    HStack(spacing: Space.s) {
                        if model.commandsLoading {
                            ProgressView().controlSize(.small)
                            Text("Asking \(model.agentName) for its commands…")
                        } else if !model.usesAgent {
                            Text("Pick Claude Code or Codex beside the composer for its commands")
                        } else {
                            Text("No commands yet — is \(model.agentName) set up?")
                        }
                    }
                    .font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    .padding(.horizontal, Space.l).padding(.vertical, Space.s)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Hairline()
                }
                if !model.attachments.isEmpty {
                    AttachmentStrip()
                }
                // A vertical TextField rather than a TextEditor: an editor
                // takes every point of height offered and the composer ends up
                // half the window. This grows line by line and stops at ten.
                TextField("", text: $draft,
                          prompt: Text("Ask anything…").foregroundColor(Palette.inkFaint),
                          axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.hubMessage)
                    .lineLimit(1...10)
                    .padding(.horizontal, Space.l)
                    .padding(.top, Space.l)
                    .focused($focused)
                    .onSubmit(submit)        // ⇧↩ still makes a new line
                    .onChange(of: draft) { _, now in
                        if now.hasPrefix("/") { model.loadCommands(cwd: repo) }
                        picked = 0
                    }
                    .onAppear { keys.install(handleKey) }
                    .onDisappear { keys.remove() }
                    .onChange(of: model.draftRequest) { _, now in
                        guard !now.isEmpty else { return }
                        draft = now
                        model.draftRequest = ""
                        focused = true
                    }

                // one row of controls inside the card: where it goes on the
                // left, the send button on the right
                HStack(spacing: Space.xs) {
                    Button(action: pickPictures) {
                        IconChip(systemName: "plus", fill: .clear)
                    }
                    .buttonStyle(.plain)
                    .help("Add pictures (or drop them here)")
                    BackendPicker()
                    RepoField(repo: $repo)
                    if model.usesAgent {
                        PermissionModeMenu()
                        ClaudePanelButton()
                    }
                    Spacer()
                    if model.sending {
                        Button {
                            model.stopRun()
                        } label: {
                            Image(systemName: "stop.fill")
                                .font(.hubIconSmall)
                                .foregroundStyle(Palette.onAccent)
                                .frame(width: Metric.send, height: Metric.send)
                                .background(Palette.ink, in: Circle())
                        }
                        .buttonStyle(.plain)
                        .help("Stop this run. (Closing the window doesn't — it keeps going.)")
                    } else {
                        Button(action: send) {
                            Image(systemName: "arrow.up")
                                .font(.hubIcon.weighted(.bold))
                                .foregroundStyle(Palette.onAccent)
                                .frame(width: Metric.send, height: Metric.send)
                                .background(Palette.accent.opacity(empty ? 0.35 : 1), in: Circle())
                        }
                        .buttonStyle(.plain)
                        .disabled(empty)
                        .keyboardShortcut(.return, modifiers: .command)
                        .help("↩ to send · ⇧↩ for a new line")
                    }
                }
                .padding(.horizontal, Space.s)
                .padding(.top, Space.s)
                .padding(.bottom, Space.s)
            }
            .frame(minHeight: Metric.composer.value * zoom)
            .background(Palette.surface,
                        in: RoundedRectangle(cornerRadius: Radius.composer))
            .overlay(RoundedRectangle(cornerRadius: Radius.composer)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .raised()
            .onDrop(of: [.fileURL, .image], isTargeted: nil, perform: dropped)
        }
        .column()
        .frame(maxWidth: .infinity)
        .padding(.horizontal, Metric.gutter)
        .padding(.bottom, Space.l)
        .onAppear { focused = true }
    }

    /// Pictures from a file, to go with the next question.
    private func pickPictures() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = true
        panel.prompt = "Add"
        guard panel.runModal() == .OK else { return }
        for url in panel.urls {
            if let image = NSImage(contentsOf: url) {
                model.attach(image: image, name: url.lastPathComponent)
            }
        }
    }

    private func send() {
        model.send(draft, repo: repo)
        draft = ""
    }

    /// Pictures dropped on the composer: image data, or image files.
    private func dropped(_ providers: [NSItemProvider]) -> Bool {
        var took = false
        for p in providers {
            if p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                took = true
                p.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                    guard let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil),
                          let image = NSImage(contentsOf: url) else { return }
                    Task { @MainActor in model.attach(image: image, name: url.lastPathComponent) }
                }
            } else if p.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                took = true
                p.loadDataRepresentation(forTypeIdentifier: UTType.image.identifier) { data, _ in
                    guard let data, let image = NSImage(data: data) else { return }
                    Task { @MainActor in model.attach(image: image, name: "dropped.png") }
                }
            }
        }
        return took
    }
}

/// The pictures going with the next question, with a way to drop one.
struct AttachmentStrip: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.s) {
                ForEach(model.attachments) { a in
                    ZStack(alignment: .topTrailing) {
                        Image(nsImage: a.image)
                            .resizable().aspectRatio(contentMode: .fill)
                            .frame(width: 56, height: 56)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.small))
                        Button {
                            model.attachments.removeAll { $0.id == a.id }
                        } label: {
                            Image(systemName: "xmark.circle.fill").font(.hubRow)
                                .foregroundStyle(Palette.surface, Palette.inkMuted)
                        }
                        .buttonStyle(.plain).padding(.all, Space.xs)
                    }
                }
            }
            .padding(.horizontal, Space.m).padding(.vertical, Space.s)
        }
    }
}

/// The files that match what's typed after "@", above the composer.
struct FileMenu: View {
    let paths: [String]
    let picked: Int
    let choose: (String) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: Space.xxs) {
                    ForEach(Array(paths.enumerated()), id: \.offset) { i, p in
                        Button { choose(p) } label: {
                            HStack(spacing: Space.s) {
                                Image(systemName: p.hasSuffix("/") ? "folder" : "doc.text")
                                    .font(.hubIconSmall).foregroundStyle(Palette.inkFaint)
                                Text(p).font(.hubMonoSmall).foregroundStyle(Palette.ink)
                                    .lineLimit(1).truncationMode(.middle)
                                Spacer(minLength: 0)
                            }
                            .listRow(selected: i == picked)
                        }
                        .buttonStyle(.plain)
                        .id(i)
                    }
                }
                .padding(.all, Space.s)
            }
            .frame(maxHeight: 220)
            .onChange(of: picked) { _, now in proxy.scrollTo(now) }
        }
    }
}

/// macOS kept the screen tools out: the switch to flip, one click away,
/// and the program to add if it isn't listed yet.
struct PermissionNeedCard: View {
    @EnvironmentObject var model: AppModel
    let need: AppModel.PermissionNeed

    private var pane: (title: String, url: String) {
        need.what == "screen"
            ? ("Screen Recording", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
            : ("Accessibility", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(spacing: Space.s) {
                Image(systemName: "hand.raised.circle").font(.hubIcon).foregroundStyle(Palette.warn)
                Text("macOS hasn't let the screen tools in").font(.hubHeading)
                Spacer()
                Button { model.permissionNeed = nil } label: {
                    Image(systemName: "xmark").font(.hubIconSmall.weighted(.semibold)).foregroundStyle(Palette.inkFaint)
                }
                .buttonStyle(.plain)
            }
            Text(need.program.hasSuffix("eki.app")
                 ? "eki needs \(pane.title) and Accessibility to see and use the screen. Click “Ask macOS” — "
                   + "macOS adds “eki” to both lists — switch it on in each, then ask again."
                 : "Open System Settings › Privacy & Security › \(pane.title), turn on "
                   + "“\(need.program.hasSuffix("eki-hid") ? "eki-hid" : "claude")” — or add the "
                   + "program with “+” if it isn't listed — then ask again.")
                .font(.hubCallout).foregroundStyle(Palette.inkMuted)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: Space.s) {
                if need.program.hasSuffix("eki.app") {
                    Button("Ask macOS") {
                        Task { await model.requestScreenAccess() }
                    }
                    .buttonStyle(AccentButton())
                    Button("Open \(pane.title) settings") {
                        if let url = URL(string: pane.url) { NSWorkspace.shared.open(url) }
                    }
                    .buttonStyle(GhostButton())
                } else {
                    Button("Open \(pane.title) settings") {
                        if let url = URL(string: pane.url) { NSWorkspace.shared.open(url) }
                    }
                    .buttonStyle(AccentButton())
                }
                if !need.program.isEmpty {
                    Button("Show the program in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: need.program)])
                    }
                    .buttonStyle(GhostButton())
                    .help(need.program)
                    Button("Copy its path") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(need.program, forType: .string)
                        model.say("Path copied — paste it with ⌘⇧G in the settings' file dialog")
                    }
                    .buttonStyle(GhostButton())
                }
            }
        }
        .card(tone: Palette.warn)
        .column()
        .frame(maxWidth: .infinity)
        .padding(.horizontal, Metric.gutter)
        .padding(.bottom, Space.s)
    }
}

/// The panels, as a menu, for those who don't type the slash commands.
struct ClaudePanelButton: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Menu {
            ForEach(ClaudePanel.panels(codex: model.usesCodex)) { p in
                Button(p.title) { model.claudePanel = p }
            }
        } label: {
            IconChip(systemName: "slider.horizontal.3")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .tint(Palette.inkMuted)
        .help("\(model.agentName)'s panels: /mcp, /permissions, /usage, /context, /rewind…")
    }
}

struct BackendPicker: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Menu {
            Button { pick("", "") } label: {
                Text("Auto — cheapest that fits")
            }
            Divider()
            ForEach(model.backends.filter(\.answers)) { backend in
                let models = model.modelsBehind(backend.key)
                if models.isEmpty {
                    Button { pick(backend.key, "") } label: {
                        Text("\(backend.key) · \(backend.priceWord)")
                    }
                } else {
                    // a provider with models behind it: the router's pick, or one by name
                    Menu("\(backend.key) · \(backend.priceWord)") {
                        Button { pick(backend.key, "") } label: {
                            Text("Auto — the router picks the model")
                        }
                        Divider()
                        ForEach(models) { m in
                            Button { pick(backend.key, m.model) } label: {
                                Text(m.label + (m.cost.map { String(format: "  ×%g", $0) } ?? ""))
                            }
                        }
                    }
                }
            }
        } label: {
            HStack(spacing: Space.xs) {
                if model.preferredBackend.isEmpty {
                    Image(systemName: "wand.and.stars").font(.hubIconSmall)
                    Text("Auto")
                } else {
                    Dot(color: Palette.backend(model.preferredBackend), size: 6)
                    Text(model.preferredModel.isEmpty ? model.preferredBackend
                         : "\(model.preferredBackend) · \(model.preferredModel)")
                }
                Image(systemName: "chevron.down").font(.hubGlyph)
            }
            .foregroundStyle(Palette.inkMuted)
            .pill(fill: .clear)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        // the app-wide accent would make this idle control shout
        .tint(Palette.inkMuted)
        .help("Auto picks the cheapest backend that can do the job and has quota left")
    }

    private func pick(_ key: String, _ modelName: String) {
        model.preferredBackend = key
        model.preferredModel = modelName
    }
}

struct RepoField: View {
    @EnvironmentObject var model: AppModel
    @Binding var repo: String

    var body: some View {
        HStack(spacing: Space.xs) {
            Button {
                if let picked = model.chooseRepo() { repo = picked; model.lastRepo = picked }
            } label: {
                HStack(spacing: Space.xs) {
                    Image(systemName: repo.isEmpty ? "folder.badge.plus" : "folder.fill")
                        .font(.hubIconSmall)
                    Text(repo.isEmpty ? "Folder" : (repo as NSString).lastPathComponent)
                        .lineLimit(1)
                }
                .foregroundStyle(repo.isEmpty ? Palette.inkMuted : Palette.accent)
            }
            .buttonStyle(.plain)
            if !repo.isEmpty {
                Button { repo = "" } label: {
                    Image(systemName: "xmark").font(.hubGlyph)
                        .foregroundStyle(Palette.inkFaint)
                }
                .buttonStyle(.plain)
            }
        }
        .pill(fill: repo.isEmpty ? .clear : Palette.accentSoft)
        .help("Give the answer a working folder — only backends that can edit "
              + "files are considered")
    }
}

/// What this thread has cost so far, split by who answered.
///
/// No dollar figure: a local model and a CLI on a subscription both cost
/// nothing per token, and inventing a price would be the least honest thing
/// on the screen. Turns and reported tokens are what's true.
struct CostStrip: View {
    let cost: CostReport?
    var context: ContextUse? = nil

    var body: some View {
        HStack(spacing: Space.m) {
            if let cost {
                ForEach(cost.by_backend.sorted(by: { $0.value.turns > $1.value.turns }),
                        id: \.key) { key, entry in
                    HStack(spacing: Space.xs) {
                        Dot(color: Palette.backend(key), size: 5)
                        Text("\(key) ×\(entry.turns)")
                        if entry.output_tokens > 0 {
                            Text("\(entry.output_tokens) tok")
                                .foregroundStyle(Palette.inkFaint)
                        }
                    }
                }
            }
            Spacer()
            if let context {
                ContextMeter(use: context)
            }
        }
        .font(.hubCaption.monospacedDigit())
        .foregroundStyle(Palette.inkFaint)
        .padding(.horizontal, Space.m)
    }
}

/// How full the harness's window is. Past 85% the next thing that happens
/// is a compaction — worth seeing coming rather than wondering why the
/// program went quiet.
struct ContextMeter: View {
    let use: ContextUse

    private var tone: Color {
        use.fraction >= 0.85 ? Palette.warn : use.fraction >= 0.6 ? Palette.inkMuted : Palette.inkFaint
    }

    var body: some View {
        HStack(spacing: Space.s) {
            Text("context \(use.text)")
            if use.window > 0 {
                ZStack(alignment: .leading) {
                    Capsule().fill(Palette.hairline)
                    Capsule().fill(tone).frame(width: max(2, 56 * use.fraction))
                }
                .frame(width: 56, height: 4)
            }
        }
        .foregroundStyle(use.fraction >= 0.85 ? Palette.warn : Palette.inkMuted)
        .help(use.window > 0
              ? "\(use.used) tokens of the \(use.window) the program was given; it compacts near the top"
              : "\(use.used) tokens in the program's context")
    }
}

struct Banner<Trailing: View>: View {
    let text: String
    let tone: Color
    @ViewBuilder let trailing: () -> Trailing

    var body: some View {
        HStack(spacing: Space.s) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.hubIconSmall)
                .foregroundStyle(tone)
            Text(text).font(.hubCallout)
            Spacer()
            trailing()
        }
        .padding(.horizontal, Space.m)
        .padding(.vertical, Space.s)
        .background(tone.opacity(0.10), in: RoundedRectangle(cornerRadius: Radius.small))
        .overlay(RoundedRectangle(cornerRadius: Radius.small)
            .strokeBorder(tone.opacity(0.25), lineWidth: 1))
        .padding(.horizontal, Metric.gutter)
        .padding(.top, Space.m)
    }
}


/// A title for a thread, instead of its first line.
struct RenameSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let row: ConversationRow
    @State private var title = ""

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            Text("Rename").font(.hubTitle)
            TextField("Title", text: $title)
                .textFieldStyle(.roundedBorder)
                .onSubmit(save)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
                Button("Save", action: save)
                    .buttonStyle(AccentButton())
                    .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(.all, Space.xl)
        .frame(width: 380)
        .onAppear { title = row.title ?? "" }
    }

    private func save() {
        let t = title.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        Task { await model.rename(row.id, to: t); dismiss() }
    }
}

/// A unified diff, coloured by line — what a repo run left behind.
struct DiffView: View {
    let text: String

    var body: some View {
        if text.isEmpty {
            Text("No changes")
                .font(.hubCallout)
                .foregroundStyle(Palette.inkFaint)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(text.components(separatedBy: .newlines).enumerated()),
                        id: \.offset) { _, line in
                    Text(line.isEmpty ? " " : line)
                        .font(.hubMonoSmall)
                        .foregroundStyle(color(line))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Space.m)
                        .padding(.vertical, Space.xxs)
                        .background(background(line))
                        .textSelection(.enabled)
                }
            }
            .padding(.vertical, Space.s)
            .background(Palette.surface,
                        in: RoundedRectangle(cornerRadius: Radius.small))
            .overlay(RoundedRectangle(cornerRadius: Radius.small)
                .strokeBorder(Palette.hairline, lineWidth: 1))
        }
    }

    private func color(_ line: String) -> Color {
        if line.hasPrefix("+++") || line.hasPrefix("---") { return Palette.inkMuted }
        if line.hasPrefix("+") { return Palette.ok }
        if line.hasPrefix("-") { return Palette.danger }
        if line.hasPrefix("@@") { return Palette.accent }
        if line.hasPrefix("diff ") || line.hasPrefix("index ") { return Palette.inkFaint }
        return Palette.ink
    }

    private func background(_ line: String) -> Color {
        if line.hasPrefix("+++") || line.hasPrefix("---") { return .clear }
        if line.hasPrefix("+") { return Palette.ok.opacity(0.10) }
        if line.hasPrefix("-") { return Palette.danger.opacity(0.10) }
        return .clear
    }
}
