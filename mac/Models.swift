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
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Unified memory").font(.hubTitle)
                        Spacer()
                        Text("\(fmt(memory.committed_gb)) / \(fmt(memory.ceiling_gb)) GB")
                            .font(.system(size: 13, weight: .medium).monospacedDigit())
                            .foregroundStyle(Palette.inkMuted)
                    }
                    GeometryReader { geo in
                        let fraction = memory.ceiling_gb > 0
                            ? min(1, memory.committed_gb / memory.ceiling_gb) : 0
                        ZStack(alignment: .leading) {
                            Capsule().fill(Palette.fill)
                            Capsule()
                                .fill(LinearGradient(
                                    colors: [Palette.accent.opacity(0.85), Palette.accent],
                                    startPoint: .leading, endPoint: .trailing))
                                .frame(width: max(6, geo.size.width * fraction))
                        }
                    }
                    .frame(height: 9)
                    // the ceiling is what MLX will actually work with, not the
                    // sticker number — worth saying, since they differ by 10GB
                    Text("\(fmt(memory.free_gb)) GB free · the ceiling is what MLX will "
                         + "work with, of \(fmt(memory.total_gb)) GB installed")
                        .font(.system(size: 11.5))
                        .foregroundStyle(Palette.inkFaint)
                }
            }
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
                    }
                    .font(.system(size: 11.5))
                    .foregroundStyle(Palette.inkMuted)
                }
                Spacer()
                if model.busyModel == row.key {
                    ProgressView().controlSize(.small)
                } else if row.running {
                    Button("Stop") { Task { await model.setModel(row.key, running: false) } }
                        .buttonStyle(GhostButton())
                } else {
                    Button("Start") { Task { await model.setModel(row.key, running: true) } }
                        .buttonStyle(AccentButton())
                        .disabled(row.blocked_by_memory)
                        .opacity(row.blocked_by_memory ? 0.45 : 1)
                        .help(row.blocked_by_memory
                              ? "Not enough headroom — stop something first"
                              : "Loads the weights and waits for the port")
                }
            }
        }
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
