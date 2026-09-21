// Activity: everything the engine is doing, and everything it has done.
//
// There is nothing to dispatch from here — you start work by saying something
// in a conversation, like anywhere else. This is the one screen that answers
// "what is running right now, and what happened to the thing I left going?"
import SwiftUI

struct ActivityPane: View {
    @EnvironmentObject var model: AppModel
    let openConversation: (String) -> Void
    @State private var selected: String = ""

    private var live: [Run] { model.runs.filter(\.isLive) }
    private var finished: [Run] { model.runs.filter { !$0.isLive } }

    var body: some View {
        HSplitView {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 3) {
                    if !live.isEmpty {
                        SectionLabel(text: "Running · \(live.count)")
                            .padding(.horizontal, 10).padding(.top, 6).padding(.bottom, 4)
                        ForEach(live) { run in
                            RunRow(run: run, selected: selected == run.id) { selected = run.id }
                        }
                    }
                    SectionLabel(text: "Recent")
                        .padding(.horizontal, 10)
                        .padding(.top, live.isEmpty ? 6 : 16)
                        .padding(.bottom, 4)
                    ForEach(finished) { run in
                        RunRow(run: run, selected: selected == run.id) { selected = run.id }
                    }
                    if model.runs.isEmpty {
                        Text("Nothing has run yet. Say something in a conversation.")
                            .font(.system(size: 12))
                            .foregroundStyle(Palette.inkFaint)
                            .padding(.horizontal, 10)
                    }
                }
                .padding(10)
            }
            .frame(minWidth: 290, idealWidth: 350, maxHeight: .infinity)
            .background(Palette.rail)

            Group {
                if selected.isEmpty {
                    VStack(spacing: 9) {
                        Image(systemName: "waveform.path.ecg")
                            .font(.system(size: 26))
                            .foregroundStyle(Palette.inkFaint)
                        Text(live.isEmpty ? "Nothing running" : "\(live.count) running")
                            .font(.system(size: 14, weight: .medium))
                        Text("Every message you send is a run. Quitting the app "
                             + "doesn't stop one.")
                            .font(.system(size: 12))
                            .foregroundStyle(Palette.inkMuted)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: 260)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    RunDetail(id: selected, openConversation: openConversation)
                        .id(selected)
                }
            }
            .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
            .background(Palette.canvas)
        }
        .task {
            takeFocus()
            await model.refreshLive()
        }
        .onChange(of: model.focusRun) { _, _ in takeFocus() }
    }

    private func takeFocus() {
        if !model.focusRun.isEmpty {
            selected = model.focusRun
            model.focusRun = ""
        }
    }
}

struct RunRow: View {
    let run: Run
    let selected: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 9) {
                Dot(color: Self.color(run.state), pulsing: run.state == "running")
                    .padding(.top, 4)
                VStack(alignment: .leading, spacing: 5) {
                    Text(run.prompt.replacingOccurrences(of: "\n", with: " "))
                        .font(.system(size: 13))
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    HStack(spacing: 6) {
                        if let backend = run.backend, !backend.isEmpty {
                            Tag(text: backend, color: Palette.backend(backend))
                        }
                        if run.hasFolder {
                            Image(systemName: "folder")
                                .font(.system(size: 9.5))
                                .foregroundStyle(Palette.inkMuted)
                                .help(run.cwd ?? "")
                        }
                        Text(run.state)
                            .font(.system(size: 10.5))
                            .foregroundStyle(Palette.inkMuted)
                        Text(Self.when(run.created_at))
                            .font(.system(size: 10.5))
                            .foregroundStyle(Palette.inkFaint)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? Palette.accentSoft
                                 : (hovering ? Palette.fill.opacity(0.7) : .clear),
                        in: RoundedRectangle(cornerRadius: Metric.smallRadius))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Palette.ink)
        .onHover { hovering = $0 }
    }

    static func color(_ state: String) -> Color {
        switch state {
        case "running": return Palette.ok
        case "queued": return Palette.warn
        case "done": return Palette.inkFaint
        case "cancelled", "interrupted": return Palette.warn
        default: return Palette.danger
        }
    }

    static func when(_ epoch: Int) -> String {
        let fmt = RelativeDateTimeFormatter()
        fmt.unitsStyle = .abbreviated
        return fmt.localizedString(for: Date(timeIntervalSince1970: TimeInterval(epoch)),
                                   relativeTo: Date())
    }
}

struct RunDetail: View {
    @EnvironmentObject var model: AppModel
    let id: String
    let openConversation: (String) -> Void

    @State private var run: Run?
    @State private var output: String = ""
    @State private var state: String = ""
    @State private var diff: String = ""
    @State private var showingDiff = false
    @State private var watcher: Task<Void, Never>?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider().overlay(Palette.hairline)
            ScrollViewReader { proxy in
                ScrollView {
                    Group {
                        if showingDiff {
                            DiffView(text: diff)
                        } else if output.isEmpty {
                            Text(state == "running" ? "Working…" : "No output")
                                .font(.system(size: 12))
                                .foregroundStyle(Palette.inkFaint)
                        } else {
                            MarkdownText(content: output)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    Color.clear.frame(height: 1).id("end")
                }
                .onChange(of: output) {
                    if !showingDiff { proxy.scrollTo("end") }
                }
            }
        }
        .task { await load() }
        .onDisappear { watcher?.cancel() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(run?.prompt ?? "")
                .font(.system(size: 14.5, weight: .medium))
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Dot(color: RunRow.color(state), pulsing: state == "running")
                Text(state).font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
                if let backend = run?.backend, !backend.isEmpty {
                    Tag(text: backend, color: Palette.backend(backend))
                }
                Spacer()
                if run?.hasFolder == true {
                    Picker("", selection: $showingDiff) {
                        Text("Output").tag(false)
                        Text("Changes").tag(true)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 160)
                    .onChange(of: showingDiff) { _, on in
                        if on { Task { diff = (try? await model.client.diff(run: id)) ?? "" } }
                    }
                }
            }
            HStack(spacing: 6) {
                if let cid = run?.conversation_id, !cid.isEmpty {
                    Button {
                        openConversation(cid)
                    } label: {
                        Label("Open conversation", systemImage: "bubble.left")
                    }
                    .buttonStyle(GhostButton())
                }
                if run?.isLive == true {
                    Button("Cancel") { Task { await model.cancel(id) } }
                        .buttonStyle(GhostButton())
                }
                if run?.canRetry == true {
                    Button("Retry") { model.retry(id) }
                        .buttonStyle(GhostButton())
                }
                if let cwd = run?.cwd, !cwd.isEmpty {
                    Spacer()
                    Text(cwd)
                        .font(.hubMonoSmall)
                        .foregroundStyle(Palette.inkFaint)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
            }
            if let error = run?.error, !error.isEmpty {
                Text(error)
                    .font(.system(size: 11.5))
                    .foregroundStyle(Palette.danger)
            }
        }
        .padding(18)
        .background(Palette.canvas)
    }

    // `.task` hands you a @Sendable closure, so this does NOT inherit the main
    // actor from the view — without the annotation the stream updates @State
    // off-main and the pane sits empty while the log fills.
    @MainActor
    private func load() async {
        run = try? await model.client.run(id)
        state = run?.state ?? ""
        output = run?.output ?? ""
        guard run?.isLive == true else { return }
        // The stream replays the log from the start, so take it as the truth
        // rather than appending to what the snapshot already had.
        let stream = await model.client.watch(run: id)
        var fresh = ""
        watcher = Task { @MainActor in
            do {
                for try await event in stream {
                    switch event.event {
                    case "output":
                        fresh += event.text ?? ""
                        output = fresh
                    case "state":
                        state = event.state ?? state
                    default: break
                    }
                }
            } catch { /* the engine went away; the snapshot stays on screen */ }
            run = try? await model.client.run(id)
            state = run?.state ?? state
            await model.refreshLive()
        }
    }
}

/// A unified diff, coloured the way every other diff you read is coloured.
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
