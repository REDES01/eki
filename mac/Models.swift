// SPDX-License-Identifier: Apache-2.0
// Local weights, and the rules the router follows.
//
// Two settings that belong together: what is loaded into memory right now, and
// who gets picked when you don't say. Both are things you change and then
// forget, so they say plainly what they will do rather than hiding behind
// numbers.
import SwiftUI

struct ModelsPane: View {
    @EnvironmentObject var model: AppModel
    @State private var addingProvider = false
    @State private var addingModel = false
    @State private var showingAll = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                MemoryCard()

                Group2("Providers",
                       note: "on Auto, the cheapest provider that can do the job and "
                           + "still has quota wins. Switch one off to keep it out of "
                           + "Auto; you can still pick it by name") {
                    ForEach(model.providers) { provider in
                        ProviderCard(provider: provider)
                    }
                    HStack(spacing: 8) {
                        Button { addingProvider = true } label: {
                            Label("Add provider", systemImage: "plus")
                        }
                        .buttonStyle(GhostButton())
                        Button { addingModel = true } label: {
                            Label("Add local model", systemImage: "arrow.down.circle")
                        }
                        .buttonStyle(GhostButton())
                        Button { showingAll = true } label: {
                            Label("Every model", systemImage: "tablecells")
                        }
                        .buttonStyle(GhostButton())
                    }
                }

                Group2("Local servers",
                       note: "started on demand when a request needs them, unloaded "
                           + "after a while unused if eki started them, and never "
                           + "loaded past what fits in memory") {
                    ForEach(model.localModels) { row in
                        LocalModelCard(row: row)
                    }
                    if !model.modelMessage.isEmpty {
                        HStack(spacing: 6) {
                            Image(systemName: "info.circle").font(.system(size: 11))
                            Text(model.modelMessage).font(.system(size: 12))
                        }
                        .foregroundStyle(Palette.inkMuted)
                        .padding(.top, 2)
                    }
                }

            }
            .frame(maxWidth: Metric.column, alignment: .leading)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, Metric.gutter)
            .padding(.vertical, 26)
        }
        .background(Palette.canvas)
        .task { await model.refreshProviders() }
        .sheet(isPresented: $addingProvider) { AddProviderSheet() }
        .sheet(isPresented: $addingModel) { AddModelSheet() }
        .sheet(isPresented: $showingAll) { ModelsSheet(provider: nil) }
    }
}

struct Group2<Content: View>: View {
    let title: String
    let note: String
    @ViewBuilder let content: () -> Content

    init(_ title: String, note: String = "", @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.note = note
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: title)
            if !note.isEmpty {
                Text(note)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.inkMuted)
                    .padding(.bottom, 2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            content()
        }
    }
}

struct MemoryCard: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        if let memory = model.memory {
            let other = memory.other_gb ?? 0
            let free = memory.available_gb ?? memory.free_gb
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Unified memory").font(.hubTitle)
                        if memory.pressure == "warning" || memory.pressure == "critical" {
                            Tag(text: "under pressure", color: Palette.danger)
                        }
                        Spacer()
                        Text("\(fmt(free)) GB free of \(fmt(memory.total_gb))")
                            .font(.system(size: 13, weight: .medium).monospacedDigit())
                            .foregroundStyle(Palette.inkMuted)
                    }
                    // one bar, three parts: eki's models, everything else, free
                    GeometryReader { geo in
                        let total = max(memory.total_gb, 1)
                        HStack(spacing: 2) {
                            Capsule().fill(Palette.accent)
                                .frame(width: max(memory.committed_gb > 0 ? 6 : 0,
                                                  geo.size.width * memory.committed_gb / total))
                            Capsule().fill(Palette.inkFaint.opacity(0.55))
                                .frame(width: max(other > 0 ? 6 : 0, geo.size.width * other / total))
                            Capsule().fill(Palette.fill)
                        }
                    }
                    .frame(height: 9)
                    HStack(spacing: 14) {
                        legend(Palette.accent, "eki models \(fmt(memory.committed_gb)) GB")
                        legend(Palette.inkFaint.opacity(0.55), "everything else \(fmt(other)) GB")
                        Spacer()
                        Text("models can take \(fmt(memory.free_gb)) GB more")
                            .font(.system(size: 11.5))
                            .foregroundStyle(Palette.inkFaint)
                            .help("The smaller of what's under MLX's \(fmt(memory.ceiling_gb)) GB "
                                  + "ceiling and what the Mac can hand out, keeping 4 GB back")
                    }
                    if let holders = memory.holders, !holders.isEmpty {
                        Text(holders.map { "\($0.name) \(fmt($0.gb)) GB" }
                                .joined(separator: " · "))
                            .font(.system(size: 11))
                            .foregroundStyle(Palette.inkFaint)
                            .lineLimit(1)
                            .help("The biggest things holding memory right now. eki never "
                                  + "stops these — only its own servers.")
                    }
                }
            }
        }
    }

    private func legend(_ colour: Color, _ text: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(colour).frame(width: 7, height: 7)
            Text(text).font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
        }
    }

    private func fmt(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(format: "%.1f", value)
    }
}

struct LocalModelCard: View {
    @EnvironmentObject var model: AppModel
    let row: LocalModelRow

    var body: some View {
        Card {
            HStack(spacing: 12) {
                Dot(color: row.running ? Palette.ok : Palette.inkFaint.opacity(0.5),
                    size: 8, pulsing: model.busyModel == row.key)
                VStack(alignment: .leading, spacing: 3) {
                    Text(row.label).font(.system(size: 13.5, weight: .medium))
                    HStack(spacing: 8) {
                        Text(":\(row.port)")
                        Text(String(format: "%.1f GB", row.gb))
                        if !row.note.isEmpty { Text(row.note) }
                        if let fate { Text(fate) }
                    }
                    .font(.system(size: 11.5))
                    .foregroundStyle(Palette.inkMuted)
                    if let profile = row.profile, let summary = profile.summary {
                        Text(summary)
                            .font(.system(size: 11.5))
                            .foregroundStyle(Palette.inkFaint)
                            .help("Read from the build's own files (\(profile.repo)) each time "
                                  + "eki loads its models — nothing here is typed in.")
                    }
                    if let window = row.context, let summary = window.summary {
                        Text(summary)
                            .font(.system(size: 11.5))
                            .foregroundStyle(Palette.inkFaint)
                            .help("Worked out from the model's config and the memory beside "
                                  + "its weights each time eki loads its models; Codex and "
                                  + "Claude Code are told this number.")
                    }
                }
                Spacer()
                if row.busy == true {
                    Tag(text: "answering", color: Palette.ok)
                }
                idleMenu
                if model.busyModel == row.key {
                    ProgressView().controlSize(.small)
                } else if row.running {
                    Button("Stop") { Task { await model.setModel(row.key, running: false) } }
                        .buttonStyle(GhostButton())
                        .disabled(row.busy == true)
                        .help(row.busy == true ? "A run is using it right now"
                              : "Frees its memory; it starts again on demand")
                } else {
                    Button("Start") { Task { await model.setModel(row.key, running: true) } }
                        .buttonStyle(AccentButton())
                        .disabled(row.blocked_by_memory)
                        .opacity(row.blocked_by_memory ? 0.45 : 1)
                        .help(row.blocked_by_memory
                              ? "Won't fit in what the Mac has free right now"
                              : "Loads the weights and waits for the port")
                }
            }
        }
    }
}

extension LocalModelCard {
    static let idleChoices: [(String, Double)] = [
        ("5 minutes", 5), ("15 minutes", 15), ("30 minutes", 30),
        ("1 hour", 60), ("4 hours", 240),
    ]

    /// What the idle timer has in store, in words.
    var fate: String? {
        guard row.running else { return nil }
        if row.pinned == true { return "stays loaded" }
        guard let at = row.unloads_at else { return nil }
        let minutes = Int(((at - Date().timeIntervalSince1970) / 60).rounded(.up))
        if minutes <= 1 { return "unloads within a minute" }
        return minutes < 90 ? "unloads in \(minutes) min"
                            : "unloads in \(Int((Double(minutes) / 60).rounded())) h"
    }

    var idleMenu: some View {
        let current = row.idle_minutes ?? 15
        return Menu {
            Section("Unload after unused for") {
                ForEach(Self.idleChoices, id: \.1) { name, minutes in
                    Toggle(name, isOn: Binding(
                        get: { current == minutes },
                        set: { _ in Task { await model.setIdle(row.key, minutes: minutes) } }))
                }
            }
            Divider()
            Toggle("Keep loaded", isOn: Binding(
                get: { current == 0 },
                set: { _ in Task { await model.setIdle(row.key, minutes: 0) } }))
        } label: {
            Image(systemName: row.pinned == true ? "pin.fill" : "timer")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(row.pinned == true ? Palette.accent : Palette.inkMuted)
                .frame(width: 22, height: 22)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .tint(Palette.inkMuted)
        .fixedSize()
        .help(row.pinned == true
              ? "Kept loaded: the idle timer leaves it alone, and it's the last "
                + "thing unloaded when something else needs the memory"
              : "Unloaded after \(Int(current)) min unused, if eki started it; "
                + "it loads again on demand in a few seconds")
    }
}

struct RoutingCard: View {
    @EnvironmentObject var model: AppModel
    let backend: Backend

    private var enabled: Bool { !model.policy.disabled.contains(backend.key) }
    private var overridden: Int? { model.policy.tiers[backend.key] }
    private var isFirst: Bool { model.policy.order.first == backend.key }

    var body: some View {
        Card {
            HStack(spacing: 12) {
                Dot(color: Palette.backend(backend.key), size: 8)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 7) {
                        Text(backend.label)
                            .font(.system(size: 13.5, weight: .medium))
                            .foregroundStyle(enabled ? Palette.ink : Palette.inkFaint)
                        if isFirst { Tag(text: "first choice", color: Palette.accent) }
                        if !backend.ok { Tag(text: "down", color: Palette.danger) }
                    }
                    Text(detail)
                        .font(.system(size: 11.5))
                        .foregroundStyle(Palette.inkMuted)
                        .lineLimit(1)
                }
                Spacer()
                if enabled && !isFirst {
                    Button("Prefer") { Task { await model.prefer(backend.key) } }
                        .buttonStyle(GhostButton())
                        .help("Wins ties against backends of the same cost")
                }
                Toggle("", isOn: Binding(
                    get: { enabled },
                    set: { on in
                        Task { await model.toggle(backend: backend.key, enabled: on) }
                    }))
                    .labelsHidden()
                    .toggleStyle(AccentSwitch())
                    .controlSize(.small)
                    .help("Off means never chosen automatically — you can still "
                          + "pick it by name")
            }
        }
    }

    private var detail: String {
        var bits = ["tier \(overridden ?? backend.tier)\(overridden != nil ? " (yours)" : "")",
                    backend.priceWord]
        bits.append(backend.ok ? backend.detail : backend.detail)
        return bits.joined(separator: " · ")
    }
}
