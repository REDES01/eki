// SPDX-License-Identifier: Apache-2.0
// The window.
//
// Shaped like the chat apps it sits beside: a quiet rail on the left, one
// centred reading column, and a composer that is the only bright thing on the
// page. The assistant's words sit directly on the canvas rather than in a
// bubble — a bubble around every answer turns a long reply into a slab.
import SwiftUI

enum Pane: Hashable {
    case chat(String)      // "" = a new one
    case usage
    case models
}

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @State private var pane: Pane = .chat("")
    @AppStorage(Pref.onboarded) private var onboarded: Bool = false
    @State private var showWelcome = false

    var body: some View {
        NavigationSplitView {
            Sidebar(pane: $pane)
                .navigationSplitViewColumnWidth(min: 210, ideal: 248, max: 330)
        } detail: {
            Group {
                switch pane {
                case .chat: ChatPane()
                case .usage: UsagePane()
                case .models: ModelsPane()
                }
            }
            .frame(minWidth: 520, minHeight: 400)
            .background(Palette.canvas)
        }
        .toolbar {
            ToolbarItem(placement: .principal) { Spacer() }
            ToolbarItem(placement: .primaryAction) { EngineBadge() }
        }
        .toolbarBackground(Palette.canvas, for: .windowToolbar)
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
}

// MARK: - the rail

struct Sidebar: View {
    @EnvironmentObject var model: AppModel
    @Binding var pane: Pane
    @State private var renaming: ConversationRow?

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 10) {
                Button {
                    choose(.chat(""))
                } label: {
                    HStack(spacing: 7) {
                        Image(systemName: "square.and.pencil").font(.system(size: 12))
                        Text("New chat").font(.system(size: 13, weight: .medium))
                        Spacer()
                    }
                    .padding(.horizontal, 11)
                    .padding(.vertical, 8)
                    .background(Palette.surface,
                                in: RoundedRectangle(cornerRadius: Metric.smallRadius))
                    .overlay(RoundedRectangle(cornerRadius: Metric.smallRadius)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                }
                .buttonStyle(.plain)

                SearchField(text: $model.search)
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 12)

            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    RailRow(icon: "gauge.with.dots.needle.33percent", title: "Usage",
                            trailing: usageSummary,
                            selected: pane == .usage) { choose(.usage) }
                    RailRow(icon: "slider.horizontal.3", title: "Models & routing",
                            selected: pane == .models) { choose(.models) }

                    // what you're waiting on comes first, then what you keep
                    let working = model.conversations.filter { $0.live == true }
                    let pinned = model.conversations.filter { $0.isPinned && $0.live != true }
                    let rest = model.conversations.filter { !$0.isPinned && $0.live != true }
                    if !working.isEmpty && model.search.isEmpty {
                        SectionLabel(text: "Working")
                            .padding(.horizontal, 11).padding(.top, 16).padding(.bottom, 6)
                        ForEach(working) { row in
                            ChatRow(row: row, selected: pane == .chat(row.id),
                                    rename: { renaming = row }) { choose(.chat(row.id)) }
                        }
                    }
                    if !pinned.isEmpty && model.search.isEmpty {
                        SectionLabel(text: "Pinned")
                            .padding(.horizontal, 11).padding(.top, 16).padding(.bottom, 6)
                        ForEach(pinned) { row in
                            ChatRow(row: row, selected: pane == .chat(row.id),
                                    rename: { renaming = row }) { choose(.chat(row.id)) }
                        }
                    }
                    HStack {
                        SectionLabel(text: !model.search.isEmpty ? "Results"
                                     : model.showArchived ? "Archived" : "Chats")
                        Spacer()
                        if model.search.isEmpty {
                            Button(model.showArchived ? "Back" : "Archived") {
                                model.showArchived.toggle()
                                Task { await model.refreshConversations() }
                            }
                            .buttonStyle(.plain)
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundStyle(Palette.inkFaint)
                        }
                    }
                    .padding(.horizontal, 11)
                    .padding(.top, 16)
                    .padding(.bottom, 6)

                    ForEach(model.search.isEmpty ? rest : model.conversations) { row in
                        ChatRow(row: row, selected: pane == .chat(row.id),
                                rename: { renaming = row }) { choose(.chat(row.id)) }
                    }
                    if model.conversations.isEmpty {
                        Text(!model.search.isEmpty ? "No matches"
                             : model.showArchived ? "Nothing archived" : "Nothing yet")
                            .font(.system(size: 12))
                            .foregroundStyle(Palette.inkFaint)
                            .padding(.horizontal, 11)
                            .padding(.top, 4)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 14)
            }
        }
        .background(Palette.rail)
        .onChange(of: model.search) {
            Task { await model.refreshConversations() }
        }
        .sheet(item: $renaming) { row in RenameSheet(row: row) }
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

struct SearchField: View {
    @Binding var text: String

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 11))
                .foregroundStyle(Palette.inkFaint)
            TextField("Search", text: $text)
                .textFieldStyle(.plain)
                .font(.system(size: 12.5))
            if !text.isEmpty {
                Button { text = "" } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.inkFaint)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 6)
        .background(Palette.fill.opacity(0.7),
                    in: RoundedRectangle(cornerRadius: Metric.smallRadius))
    }
}

struct RailRow: View {
    let icon: String
    let title: String
    var trailing: String? = nil
    var iconColor: Color? = nil
    let selected: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Image(systemName: icon)
                    .font(.system(size: icon == "circle.fill" ? 7 : 12))
                    .foregroundStyle(iconColor ?? Palette.inkMuted)
                    .frame(width: 14)
                Text(title).font(.system(size: 13))
                Spacer()
                if let trailing {
                    Text(trailing)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Palette.inkMuted)
                }
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 7)
            .background(background, in: RoundedRectangle(cornerRadius: 7))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Palette.ink)
        .onHover { hovering = $0 }
    }

    private var background: Color {
        selected ? Palette.accentSoft : (hovering ? Palette.fill.opacity(0.8) : .clear)
    }
}

struct ChatRow: View {
    @EnvironmentObject var model: AppModel
    let row: ConversationRow
    let selected: Bool
    var rename: () -> Void = {}
    let action: () -> Void
    @State private var hovering = false
    @State private var confirmDelete = false
    @State private var problem = ""

    var body: some View {
        Button(action: action) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.system(size: 13))
                        .lineLimit(1)
                    if let subtitle {
                        Text(subtitle)
                            .font(.system(size: 11))
                            .foregroundStyle(row.live == true ? Palette.ok : Palette.inkFaint)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
                if hovering || selected {
                    Menu { menuItems } label: {
                        Image(systemName: "ellipsis")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(Palette.inkMuted)
                            .frame(width: 20, height: 18)
                            .contentShape(Rectangle())
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .tint(Palette.inkMuted)
                    .fixedSize()
                } else if row.isPinned {
                    Image(systemName: "pin.fill")
                        .font(.system(size: 8))
                        .foregroundStyle(Palette.inkFaint)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 11)
            .padding(.vertical, 7)
            .background(selected ? Palette.accentSoft
                                 : (hovering ? Palette.fill.opacity(0.8) : .clear),
                        in: RoundedRectangle(cornerRadius: 7))
            .overlay(alignment: .leading) {
                if row.live == true {
                    // being answered right now: a pulse at the very left
                    Dot(color: Palette.ok, size: 5, pulsing: true)
                        .padding(.leading, 3)
                }
            }
            .contentShape(Rectangle())
        }
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

    /// While searching, the row says what matched instead of a turn count.
    private var subtitle: String? {
        if row.live == true { return "working…" }
        if !model.search.isEmpty, let hit = row.hit {
            let line = hit.replacingOccurrences(of: "\n", with: " ")
            if !line.hasPrefix(title) { return line }
        }
        return "\(row.n) turns"
    }
}

struct EngineBadge: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        HStack(spacing: 6) {
            Dot(color: color, size: 6, pulsing: model.engine == .starting)
            Text(label)
                .font(.system(size: 11.5))
                .foregroundStyle(Palette.inkMuted)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 4)
        .background(Palette.fill.opacity(0.6), in: Capsule())
        .help(detail)
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
    @State private var draft: String = ""
    @State private var repo: String = ""

    var body: some View {
        VStack(spacing: 0) {
            if case .down(let why) = model.engine {
                Banner(text: why, tone: Palette.danger) {
                    Button("Retry") { Task { await model.ensureEngine() } }
                        .buttonStyle(GhostButton())
                }
            }
            transcript
            Composer(draft: $draft, repo: $repo)
        }
        .background(Palette.canvas)
        .onAppear { repo = model.lastRepo }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 26) {
                    if model.turns.isEmpty && model.streaming.isEmpty && !model.sending {
                        EmptyChat()
                    }
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
                    if !model.activity.isEmpty && model.sending {
                        ActivityLines(lines: model.activity)
                    }
                    if let prompt = model.prompt {
                        if prompt.kind == "ask" {
                            AskCard(prompt: prompt).id(prompt.id)
                        } else {
                            PermissionCard(prompt: prompt).id(prompt.id)
                        }
                    } else if model.sending && model.streaming.isEmpty {
                        Thinking(reason: model.routedTo)
                    }
                    if !model.chatError.isEmpty {
                        Banner(text: model.chatError, tone: Palette.danger) { EmptyView() }
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .frame(maxWidth: Metric.column, alignment: .leading)
                .frame(maxWidth: .infinity)          // centre the column
                .padding(.horizontal, Metric.gutter)
                .padding(.vertical, 28)
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

struct EmptyChat: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("What are we doing?")
                .font(.system(size: 22, weight: .semibold))
            Text("Auto sends this to the cheapest backend that can do the job "
                 + "and still has quota. Give it a folder and only the ones that "
                 + "can edit files are considered.")
                .font(.system(size: 13))
                .foregroundStyle(Palette.inkMuted)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 7) {
                ForEach(model.backends.filter { $0.ok && $0.answers }) { backend in
                    HStack(spacing: 5) {
                        Dot(color: Palette.backend(backend.key), size: 6)
                        Text(backend.key).font(.system(size: 11.5))
                            .foregroundStyle(Palette.inkMuted)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Palette.fill.opacity(0.6), in: Capsule())
                }
            }
            .padding(.top, 4)
        }
        .padding(.vertical, 40)
    }
}

struct MessageView: View {
    let turn: Turn
    var streaming: Bool = false

    private var isUser: Bool { turn.role == "user" }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if isUser {
                // the question, as a compact bubble that stops short of the margin
                Text(turn.content)
                    .font(.hubMessage)
                    .lineSpacing(4)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Palette.fill,
                                in: RoundedRectangle(cornerRadius: Metric.radius))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.trailing, 60)
            } else {
                HStack(spacing: 7) {
                    Dot(color: Palette.backend(turn.backend ?? ""), size: 7,
                        pulsing: streaming)
                    Text((turn.backend ?? "assistant").uppercased())
                        .font(.system(size: 10.5, weight: .semibold))
                        .tracking(0.6)
                        .foregroundStyle(Palette.inkMuted)
                    if let reason = turn.reason, !reason.isEmpty {
                        Text(reason)
                            .font(.system(size: 10.5))
                            .foregroundStyle(Palette.inkFaint)
                            .lineLimit(1)
                    }
                }
                MarkdownText(content: turn.content)
                if !streaming {
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
        HStack(spacing: 2) {
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
                .help(cwd)
            }

            if let run = turn.run, turn.didNotFinish {
                Button {
                    model.retry(run)
                } label: {
                    Label("Retry", systemImage: "arrow.clockwise")
                }
                .buttonStyle(GhostButton())
            }
        }
        .labelStyle(.titleAndIcon)
        .padding(.leading, -9)                    // align the ghost text with the answer
        .sheet(isPresented: $showingDiff) {
            DiffSheet(folder: turn.cwd ?? "", diff: diff)
        }
    }
}

struct DiffSheet: View {
    let folder: String
    let diff: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Changes").font(.hubTitle)
                    Text(folder)
                        .font(.hubMonoSmall)
                        .foregroundStyle(Palette.inkFaint)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
                Spacer()
                Button("Done") { dismiss() }
                    .buttonStyle(AccentButton())
                    .keyboardShortcut(.defaultAction)
            }
            .padding(16)
            Divider().overlay(Palette.hairline)
            ScrollView {
                DiffView(text: diff).padding(16)
            }
            // what the repo holds now, which may include edits made since —
            // said here so a stale-looking diff isn't a mystery
            Text("As the folder is now, against its last commit.")
                .font(.system(size: 11))
                .foregroundStyle(Palette.inkFaint)
                .padding(.horizontal, 16)
                .padding(.bottom, 12)
        }
        .frame(minWidth: 640, minHeight: 440)
        .background(Palette.canvas)
    }
}

struct Thinking: View {
    let reason: String
    @State private var phase = 0.0

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(Palette.accent)
                .frame(width: 7, height: 7)
                .scaleEffect(0.6 + 0.4 * phase)
                .opacity(0.45 + 0.55 * phase)
            Text(reason.isEmpty ? "routing…" : reason)
                .font(.system(size: 12))
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
        VStack(spacing: 8) {
            if let cost = model.cost, cost.turns > 0 {
                CostStrip(cost: cost)
            }
            VStack(spacing: 0) {
                if !suggestions.isEmpty {
                    CommandMenu(commands: suggestions, picked: min(picked, suggestions.count - 1)) { c in
                        complete(c)
                    }
                    Divider().overlay(Palette.hairline)
                } else if draft.hasPrefix("/"), !draft.contains(where: \.isWhitespace), model.commands.isEmpty {
                    HStack(spacing: 8) {
                        if model.commandsLoading {
                            ProgressView().controlSize(.small)
                            Text("Asking Claude Code for its commands…")
                        } else {
                            Text("No commands yet — is Claude Code set up?")
                        }
                    }
                    .font(.system(size: 12)).foregroundStyle(Palette.inkMuted)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Divider().overlay(Palette.hairline)
                }
                // A vertical TextField rather than a TextEditor: an editor
                // takes every point of height offered and the composer ends up
                // half the window. This grows line by line and stops at eight.
                TextField("Ask anything…", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.hubMessage)
                    .lineLimit(1...8)
                    .padding(.horizontal, 12)
                    .padding(.top, 11)
                    .focused($focused)
                    .onSubmit(submit)        // ⇧↩ still makes a new line
                    .onChange(of: draft) { _, now in
                        if now.hasPrefix("/") { model.loadCommands(cwd: repo) }
                        picked = 0
                    }
                    .onAppear { keys.install(handleKey) }
                    .onDisappear { keys.remove() }

                HStack(spacing: 8) {
                    BackendPicker()
                    RepoField(repo: $repo)
                    Spacer()
                    if model.sending {
                        Button {
                            model.stopRun()
                        } label: {
                            Image(systemName: "stop.circle.fill")
                                .font(.system(size: 21))
                                .foregroundStyle(Palette.inkMuted)
                        }
                        .buttonStyle(.plain)
                        .help("Stop this run. (Closing the window doesn't — it keeps going.)")
                    } else {
                        Button(action: send) {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundStyle(empty ? Palette.inkFaint : .white)
                                .frame(width: 26, height: 26)
                                .background(empty ? Palette.fill : Palette.accent,
                                            in: Circle())
                        }
                        .buttonStyle(.plain)
                        .disabled(empty)
                        .keyboardShortcut(.return, modifiers: .command)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
            }
            .background(Palette.surface,
                        in: RoundedRectangle(cornerRadius: Metric.radius + 2))
            .overlay(RoundedRectangle(cornerRadius: Metric.radius + 2)
                .strokeBorder(focused ? Palette.accent.opacity(0.45) : Palette.hairline,
                              lineWidth: 1))

            Text("↩ to send · ⇧↩ for a new line")
                .font(.system(size: 10.5))
                .foregroundStyle(Palette.inkFaint)
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .frame(maxWidth: Metric.column)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, Metric.gutter)
        .padding(.bottom, 16)
        .onAppear { focused = true }
    }

    private func send() {
        model.send(draft, repo: repo)
        draft = ""
    }
}

struct BackendPicker: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Menu {
            Button { model.preferredBackend = "" } label: {
                Text("Auto — cheapest that fits")
            }
            Divider()
            ForEach(model.backends.filter(\.answers)) { backend in
                Button { model.preferredBackend = backend.key } label: {
                    Text("\(backend.key) · \(backend.priceWord)")
                }
            }
        } label: {
            HStack(spacing: 5) {
                if model.preferredBackend.isEmpty {
                    Image(systemName: "wand.and.stars").font(.system(size: 10.5))
                    Text("Auto")
                } else {
                    Dot(color: Palette.backend(model.preferredBackend), size: 6)
                    Text(model.preferredBackend)
                }
                Image(systemName: "chevron.down").font(.system(size: 8, weight: .semibold))
            }
            .font(.system(size: 11.5, weight: .medium))
            .foregroundStyle(Palette.inkMuted)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(Palette.fill.opacity(0.7), in: Capsule())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        // the app-wide accent would make this idle control shout
        .tint(Palette.inkMuted)
        .help("Auto picks the cheapest backend that can do the job and has quota left")
    }
}

struct RepoField: View {
    @EnvironmentObject var model: AppModel
    @Binding var repo: String

    var body: some View {
        HStack(spacing: 4) {
            Button {
                if let picked = model.chooseRepo() { repo = picked; model.lastRepo = picked }
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: repo.isEmpty ? "folder.badge.plus" : "folder.fill")
                        .font(.system(size: 10.5))
                    Text(repo.isEmpty ? "Folder" : (repo as NSString).lastPathComponent)
                        .lineLimit(1)
                }
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(repo.isEmpty ? Palette.inkMuted : Palette.accent)
            }
            .buttonStyle(.plain)
            if !repo.isEmpty {
                Button { repo = "" } label: {
                    Image(systemName: "xmark").font(.system(size: 8, weight: .bold))
                        .foregroundStyle(Palette.inkFaint)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(repo.isEmpty ? Palette.fill.opacity(0.7) : Palette.accentSoft,
                    in: Capsule())
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
    let cost: CostReport

    var body: some View {
        HStack(spacing: 12) {
            ForEach(cost.by_backend.sorted(by: { $0.value.turns > $1.value.turns }),
                    id: \.key) { key, entry in
                HStack(spacing: 5) {
                    Dot(color: Palette.backend(key), size: 5)
                    Text("\(key) ×\(entry.turns)")
                    if entry.output_tokens > 0 {
                        Text("\(entry.output_tokens) tok")
                            .foregroundStyle(Palette.inkFaint)
                    }
                }
            }
            Spacer()
        }
        .font(.system(size: 11).monospacedDigit())
        .foregroundStyle(Palette.inkMuted)
        .padding(.horizontal, 4)
    }
}

struct Banner<Trailing: View>: View {
    let text: String
    let tone: Color
    @ViewBuilder let trailing: () -> Trailing

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 11))
                .foregroundStyle(tone)
            Text(text).font(.system(size: 12.5))
            Spacer()
            trailing()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(tone.opacity(0.10), in: RoundedRectangle(cornerRadius: Metric.smallRadius))
        .overlay(RoundedRectangle(cornerRadius: Metric.smallRadius)
            .strokeBorder(tone.opacity(0.25), lineWidth: 1))
        .padding(.horizontal, Metric.gutter)
        .padding(.top, 12)
    }
}


/// A title for a thread, instead of its first line.
struct RenameSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let row: ConversationRow
    @State private var title = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
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
        .padding(22)
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
                .font(.system(size: 12))
                .foregroundStyle(Palette.inkFaint)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(text.components(separatedBy: .newlines).enumerated()),
                        id: \.offset) { _, line in
                    Text(line.isEmpty ? " " : line)
                        .font(.hubMonoSmall)
                        .foregroundStyle(color(line))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 1.5)
                        .background(background(line))
                        .textSelection(.enabled)
                }
            }
            .padding(.vertical, 6)
            .background(Palette.surface,
                        in: RoundedRectangle(cornerRadius: Metric.smallRadius))
            .overlay(RoundedRectangle(cornerRadius: Metric.smallRadius)
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
