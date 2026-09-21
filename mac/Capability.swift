// SPDX-License-Identifier: Apache-2.0
// The models behind a provider, and what each has shown it can do.
//
// A score is either a prior — a guess from the model's class — or a
// measurement from eki's own battery. The sheet never blurs the two: a
// measured cell is solid, a prior is faint, and the difference is the whole
// point of having a Measure button.
import SwiftUI

struct ModelScore: Codable, Hashable {
    struct Measured: Codable, Hashable {
        let score: Double
        let n: Int
    }
    let prior: Double
    let measured: [String: Measured]     // easy | medium | hard
}

struct RegistryModel: Codable, Identifiable, Hashable {
    let provider: String
    let model: String
    let label: String
    let enabled: Bool
    let context_tokens: Int
    var speed_tok_s: Double?
    let `class`: String
    let source: String
    let scores: [String: ModelScore]

    var id: String { "\(provider):\(model)" }
    var isDefault: Bool { model.isEmpty }
}

extension EngineClient {
    func registry() async throws -> [RegistryModel] {
        struct W: Codable { let models: [RegistryModel] }
        return try await decode(W.self, "GET", "api/registry").models
    }

    func discoverModels() async throws {
        struct W: Codable { let found: [String: [String]] }
        _ = try await decode(W.self, "POST", "api/registry/discover", timeout: 120)
    }

    func measure(provider: String, model: String) async throws -> String {
        struct W: Codable { let run: String }
        return try await decode(W.self, "POST", "api/registry/\(provider)/measure",
                                body: ["model": model]).run
    }

    func setModel(provider: String, model: String, enabled: Bool) async throws {
        _ = try await decode(RegistryModel.self, "PATCH", "api/registry/\(provider)",
                             body: ["model": model, "enabled": enabled])
    }
}

/// The columns, in the order the router thinks about them.
private let shownTasks = ["chat", "writing", "translate", "code", "repo", "math"]

struct ModelsSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let provider: ProviderDTO
    @State private var models: [RegistryModel] = []
    @State private var measuring: Set<String> = []
    @State private var note = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Models behind \(provider.label)").font(.hubTitle)
                    Text("What the router can pick from, and what each is believed or "
                         + "known to be good at. Solid numbers are measured; faint ones "
                         + "are guesses from the model's class.")
                        .font(.system(size: 12)).foregroundStyle(Palette.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button("Done") { dismiss() }.buttonStyle(GhostButton())
            }

            // header row
            HStack(spacing: 0) {
                Text("Model").font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(Palette.inkFaint)
                    .frame(width: 190, alignment: .leading)
                ForEach(shownTasks, id: \.self) { task in
                    Text(task.uppercased()).font(.system(size: 10, weight: .semibold))
                        .tracking(0.5).foregroundStyle(Palette.inkFaint)
                        .frame(width: 58, alignment: .trailing)
                }
                Spacer()
            }
            .padding(.horizontal, 12)

            ScrollView {
                VStack(spacing: 4) {
                    ForEach(models) { m in
                        row(m)
                    }
                    if models.isEmpty {
                        Text("Nothing listed yet — the provider is asked when it's reachable.")
                            .font(.system(size: 12)).foregroundStyle(Palette.inkFaint)
                            .padding(.top, 8)
                    }
                }
            }

            if !note.isEmpty {
                Text(note).font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
            }
            Text("Measure runs a short battery — capitals, arithmetic, translations, a haiku "
                 + "— against the model and keeps the scores. A local model grades the "
                 + "open-ended answers; on a paid model this spends a little of its quota.")
                .font(.system(size: 11)).foregroundStyle(Palette.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(22)
        .frame(width: 720, height: 460)
        .task { await load() }
    }

    private func row(_ m: RegistryModel) -> some View {
        HStack(spacing: 0) {
            HStack(spacing: 8) {
                Toggle("", isOn: Binding(
                    get: { m.enabled },
                    set: { on in Task {
                        try? await model.client.setModel(provider: provider.key, model: m.model,
                                                         enabled: on)
                        await load()
                    } }))
                    .labelsHidden().toggleStyle(AccentSwitch()).controlSize(.mini)
                    .disabled(m.isDefault)
                VStack(alignment: .leading, spacing: 1) {
                    Text(m.isDefault ? "Default" : m.label)
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(m.enabled ? Palette.ink : Palette.inkFaint)
                        .lineLimit(1)
                    Text(sub(m)).font(.system(size: 10.5)).foregroundStyle(Palette.inkFaint)
                        .lineLimit(1)
                }
            }
            .frame(width: 190, alignment: .leading)
            ForEach(shownTasks, id: \.self) { task in
                cell(m.scores[task])
                    .frame(width: 58, alignment: .trailing)
            }
            Spacer()
            if measuring.contains(m.id) {
                ProgressView().controlSize(.small)
            } else {
                Button("Measure") { Task { await measure(m) } }
                    .buttonStyle(GhostButton())
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 7)
        .background(Palette.surface, in: RoundedRectangle(cornerRadius: 8))
    }

    /// Best available: measured at the hardest level there is, else the prior.
    private func cell(_ s: ModelScore?) -> some View {
        guard let s else {
            return Text("–").font(.system(size: 12)).foregroundStyle(Palette.inkFaint)
        }
        if let m = s.measured["medium"] ?? s.measured["easy"] {
            return Text(String(format: "%.2f", m.score))
                .font(.system(size: 12, weight: .semibold).monospacedDigit())
                .foregroundStyle(Palette.ink)
        }
        return Text(String(format: "%.2f", s.prior))
            .font(.system(size: 12).monospacedDigit())
            .foregroundStyle(Palette.inkFaint)
    }

    private func sub(_ m: RegistryModel) -> String {
        var bits = [m.class.replacingOccurrences(of: "_", with: " ")]
        if m.context_tokens > 0 { bits.append("\(m.context_tokens / 1000)k") }
        if let s = m.speed_tok_s { bits.append("\(Int(s)) tok/s") }
        return bits.joined(separator: " · ")
    }

    private func load() async {
        models = ((try? await model.client.registry()) ?? [])
            .filter { $0.provider == provider.key }
            .sorted { ($0.isDefault ? 0 : 1, $0.label) < ($1.isDefault ? 0 : 1, $1.label) }
    }

    private func measure(_ m: RegistryModel) async {
        measuring.insert(m.id)
        defer { measuring.remove(m.id) }
        do {
            let run = try await model.client.measure(provider: provider.key, model: m.model)
            note = "Measuring \(m.isDefault ? "the default" : m.label)… it takes a minute or two."
            // wait for the run to finish, then reload the scores
            for _ in 0..<180 {
                try? await Task.sleep(for: .seconds(2))
                if let r = try? await model.client.run(run), !r.isLive {
                    note = r.state == "done" ? (lastLine(r.output) ?? "") : (r.error ?? "failed")
                    break
                }
            }
            await load()
        } catch {
            note = error.localizedDescription
        }
    }

    private func lastLine(_ output: String?) -> String? {
        output?.split(separator: "\n").last.map(String.init)
    }
}
