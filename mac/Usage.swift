// SPDX-License-Identifier: Apache-2.0
// Usage: every limit every provider reports, and how old each number is.
//
// Rules carried over from tokenbar, because you set them there:
// no warning colours on the way up — a bar fills, that's all — and the
// percentage turns red only at exactly 100%. Resets read as a duration
// ("in 2h 14m"); the exact clock time is in the tooltip.
import SwiftUI

enum UsageFormat {
    static func percent(_ used: Double) -> String { "\(Int((used * 100).rounded()))%" }

    static func until(_ epoch: Int?) -> String? {
        guard let epoch else { return nil }
        let seconds = epoch - Int(Date().timeIntervalSince1970)
        if seconds <= 0 { return "resetting" }
        let d = seconds / 86400, h = (seconds % 86400) / 3600, m = (seconds % 3600) / 60
        if d > 0 { return "in \(d)d \(h)h" }
        if h > 0 { return "in \(h)h \(m)m" }
        return "in \(max(1, m))m"
    }

    static func clock(_ epoch: Int?) -> String {
        guard let epoch else { return "" }
        let f = DateFormatter()
        f.dateFormat = "EEE HH:mm"
        return "resets " + f.string(from: Date(timeIntervalSince1970: TimeInterval(epoch)))
    }

    static func age(_ seconds: Int?) -> String? {
        guard let seconds else { return nil }
        if seconds < 90 { return "just now" }
        if seconds < 3600 { return "\(seconds / 60) min ago" }
        if seconds < 86400 { return "\(seconds / 3600) h ago" }
        return "\(seconds / 86400) d ago"
    }
}

struct UsageRow: View {
    let window: UsageWindow
    let colour: Color

    private var full: Bool { window.used >= 1.0 }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
        HStack(spacing: 10) {
            Text(window.label)
                .font(.system(size: 10.5, weight: .semibold))
                .tracking(0.5)
                .foregroundStyle(window.isPrimary ? Palette.inkMuted : Palette.inkFaint)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                // wide enough for a per-model label like "FABLE WEEK"
                .frame(width: 74, alignment: .leading)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Palette.fill)
                    Capsule().fill(colour)
                        .frame(width: max(window.used > 0 ? 5 : 0,
                                          geo.size.width * min(1, window.used)))
                    // where an even burn would be by now
                    if let elapsed = window.pace?.elapsed, elapsed > 0, elapsed < 1 {
                        Rectangle().fill(Palette.ink.opacity(0.55))
                            .frame(width: 1.5, height: 10)
                            .offset(x: geo.size.width * elapsed - 0.75, y: -2)
                    }
                }
            }
            .frame(height: 6)
            .help(paceHelp)
            Text(UsageFormat.percent(window.used))
                .font(.system(size: 12, weight: .semibold).monospacedDigit())
                .foregroundStyle(full ? Color.red : Palette.ink)
                .frame(width: 42, alignment: .trailing)
            Text(UsageFormat.until(window.resets_at) ?? "")
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(Palette.inkFaint)
                .frame(width: 70, alignment: .trailing)
                .help(UsageFormat.clock(window.resets_at))
        }
        // money has a figure worth reading, not just a bar
        if let detail = window.detail, !detail.isEmpty {
            Text(detail + " spent")
                .font(.system(size: 10.5).monospacedDigit())
                .foregroundStyle(Palette.inkFaint)
                .padding(.leading, 84)
        }
        if let pace = window.pace, !pace.why.isEmpty {
            Text(pace.factor > 1 ? "ahead of pace — costed ×\(fmt(pace.factor)) for routing"
                                 : "behind pace — costed ×\(fmt(pace.factor)), spend it")
                .font(.system(size: 10.5).monospacedDigit())
                .foregroundStyle(pace.factor > 1 ? Color.orange : Palette.inkFaint)
                .padding(.leading, 84)
        }
        }
    }

    private var paceHelp: String {
        guard let p = window.pace, let e = p.elapsed else { return "" }
        return "\(Int(e * 100))% of the window has passed; the tick is where an even burn would be"
    }

    private func fmt(_ x: Double) -> String {
        x == x.rounded() ? String(Int(x)) : String(format: "%.1f", x)
    }
}

struct UsageCard: View {
    @EnvironmentObject var model: AppModel
    let usage: ProviderUsage
    var compact = false
    @State private var probing = false
    @State private var probeNote = ""

    private var isClaude: Bool { usage.provider == "claude" }
    private var bridgeOn: Bool { model.usage?.claude_bridge ?? false }

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 7 : 10) {
            HStack(spacing: 7) {
                Dot(color: Palette.backend(usage.provider), size: 7)
                Text(usage.label).font(.system(size: compact ? 12.5 : 13.5, weight: .medium))
                Spacer()
                if let age = UsageFormat.age(usage.age_seconds) {
                    Text(age)
                        .font(.system(size: 10.5))
                        .foregroundStyle(Palette.inkFaint)
                        .help("When \(usage.label) last reported these numbers")
                }
            }
            if usage.windows.isEmpty {
                Text(explanation)
                    .font(.system(size: 11.5))
                    .foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if isClaude && !bridgeOn && !compact {
                    Button("Read Claude's limits") {
                        Task { await model.setClaudeBridge(true) }
                    }
                    .buttonStyle(AccentButton())
                }
            } else {
                ForEach(usage.windows) { window in
                    UsageRow(window: window, colour: Palette.backend(usage.provider))
                }
            }
            if !usage.error.isEmpty {
                Text(usage.error)
                    .font(.system(size: 11))
                    .foregroundStyle(Palette.inkFaint)
                    .lineLimit(2)
            }
            if isClaude && bridgeOn && !compact {
                HStack(spacing: 8) {
                    if probing {
                        ProgressView().controlSize(.small)
                        Text("Reading /usage…")
                            .font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
                    } else {
                        Button("Refresh now") {
                            Task {
                                probing = true
                                probeNote = await model.probeClaude()
                                probing = false
                            }
                        }
                        .buttonStyle(GhostButton())
                        .help("Opens Claude Code's /usage panel in a throwaway session "
                              + "and reads it. /usage doesn't ask a model anything, so "
                              + "this costs nothing.")
                    }
                    Spacer()
                }
                if !probeNote.isEmpty {
                    Text(probeNote)
                        .font(.system(size: 11.5))
                        .foregroundStyle(Palette.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
        }
        .padding(compact ? 10 : 14)
        .background(Palette.surface, in: RoundedRectangle(cornerRadius: Metric.radius))
        .overlay(RoundedRectangle(cornerRadius: Metric.radius)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var explanation: String {
        if !usage.note.isEmpty {
            if usage.provider == "claude" && !(model.usage?.claude_bridge ?? false) {
                return "eki reads Claude's limits from what Claude Code shows in its "
                    + "status line — never from your login. Turn it on to see them here."
            }
            return usage.note.prefix(1).uppercased() + usage.note.dropFirst()
        }
        return "Nothing reported yet."
    }
}

struct UsagePane: View {
    @EnvironmentObject var model: AppModel
    @State private var refreshing = false
    @State private var settings = HubSettings()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Usage").font(.system(size: 20, weight: .semibold))
                        Text("What each provider says you've used of its own limits.")
                            .font(.system(size: 12.5))
                            .foregroundStyle(Palette.inkMuted)
                    }
                    Spacer()
                    Button {
                        Task {
                            refreshing = true
                            await model.refreshUsage(force: true)
                            refreshing = false
                        }
                    } label: {
                        Label(refreshing ? "Refreshing" : "Refresh",
                              systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(GhostButton())
                    .disabled(refreshing)
                    SettingsLink {
                        Label("Menu bar…", systemImage: "menubar.rectangle")
                    }
                    .buttonStyle(GhostButton())
                }

                ForEach(model.usage?.providers ?? []) { usage in
                    UsageCard(usage: usage)
                }
                if model.usage == nil {
                    Text("Asking the engine…")
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.inkFaint)
                }

                if model.usage?.claude_bridge == true {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 6) {
                            Image(systemName: "info.circle").font(.system(size: 11))
                            Text("Claude's numbers update whenever you use Claude Code in a "
                                 + "terminal. eki stops reading them if you turn this off.")
                                .font(.system(size: 11.5))
                            Spacer()
                            Button("Turn off") { Task { await model.setClaudeBridge(false) } }
                                .buttonStyle(GhostButton())
                        }
                        Divider().opacity(0.4)
                        Toggle(isOn: Binding(
                            get: { settings.claude_probe },
                            set: { on in
                                settings.claude_probe = on
                                Task { settings = (try? await model.client.save(settings: settings))
                                    ?? settings }
                            })) {
                            Text("Keep it fresh while eki is open")
                                .font(.system(size: 12.5)).foregroundStyle(Palette.ink)
                        }
                        .toggleStyle(AccentSwitch())
                        Text("Every \(settings.claude_probe_minutes) minutes while you have "
                             + "this open, eki opens Claude Code's /usage panel in a throwaway "
                             + "session and reads it — including per-model limits and usage "
                             + "credits. /usage doesn't ask a model anything, so it's free.")
                            .font(.system(size: 11.5))
                        if model.usage?.claude_probe_ready == false {
                            Text(model.usage?.claude_probe_hint ?? "")
                                .font(.system(size: 11.5))
                                .foregroundStyle(Palette.inkFaint)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .foregroundStyle(Palette.inkMuted)
                }
            }
            .frame(maxWidth: Metric.column, alignment: .leading)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, Metric.gutter)
            .padding(.vertical, 26)
        }
        .background(Palette.canvas)
        .task {
            await model.refreshUsage(force: false)
            settings = (try? await model.client.settings()) ?? settings
        }
    }
}

/// What opens from the menu bar item.
struct MenuPanel: View {
    @EnvironmentObject var model: AppModel
    @AppStorage(Pref.menuBarProviders) private var shownRaw: String = ""
    @Environment(\.openWindow) private var openWindow

    private var shown: [ProviderUsage] {
        let all = model.usage?.providers ?? []
        let keys = Pref.shown(from: all.map(\.provider))
        return keys.compactMap { k in all.first { $0.provider == k } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(shown) { usage in
                UsageCard(usage: usage, compact: true)
            }
            if shown.isEmpty {
                Text("No providers in the menu bar — pick some in Settings.")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.inkMuted)
            }

            let live = model.runs.filter(\.isLive)
            if !live.isEmpty {
                SectionLabel(text: "Running · \(live.count)").padding(.top, 2)
                ForEach(live.prefix(5)) { run in
                    HStack(spacing: 7) {
                        Dot(color: Palette.ok, size: 6, pulsing: true)
                        Text(run.prompt.replacingOccurrences(of: "\n", with: " "))
                            .font(.system(size: 12))
                            .lineLimit(1)
                        Spacer()
                        if let backend = run.backend {
                            Text(backend).font(.system(size: 10.5))
                                .foregroundStyle(Palette.backend(backend))
                        }
                    }
                }
            }

            Divider().overlay(Palette.hairline)
            HStack {
                Button("Open eki") {
                    NSApp.activate(ignoringOtherApps: true)
                    openWindow(id: "main")
                }
                .buttonStyle(GhostButton())
                SettingsLink { Text("Settings…") }.buttonStyle(GhostButton())
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
                    .buttonStyle(GhostButton())
                    .help("Closes the window onto eki. Runs keep going in the engine.")
            }
        }
        .padding(12)
        .frame(width: 340)
        .background(Palette.canvas)
        .task { await model.refreshUsage(force: false) }
    }
}
