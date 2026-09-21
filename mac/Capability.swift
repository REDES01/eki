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
    var `public`: [String: Double]? = [:]   // easy | medium | hard, from the boards
    let measured: [String: Measured]     // easy | medium | hard
}

struct PublicRef: Codable, Hashable {
    let base: String
    let name: String
    let date: String
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
    var cost: Double? = 1
    var `public`: PublicRef? = nil
    let scores: [String: ModelScore]

    var id: String { "\(provider):\(model)" }
    var isDefault: Bool { model.isEmpty }
}

struct BenchStatus: Codable {
    struct SetInfo: Codable { let items: Int; let task: String; let difficulty: String }
    struct Due: Codable, Hashable { let provider: String; let hold: String }
    let sets: [String: SetInfo]
    let due: [Due]
    let auto_measure: String
}

extension EngineClient {
    func bench() async throws -> BenchStatus {
        try await decode(BenchStatus.self, "GET", "api/bench")
    }

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
    /// nil: every provider's models in one table
    let provider: ProviderDTO?
    @State private var models: [RegistryModel] = []
    @State private var bench: BenchStatus?
    @State private var measuring: Set<String> = []
    @State private var note = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(provider.map { "Models behind \($0.label)" } ?? "Every model").font(.hubTitle)
                    Text("What the router can pick from, and what each is good at. "
                         + "Bold is eki's own measurement, plain is the public boards, "
                         + "faint is a guess from the model's class.")
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
                    .frame(width: nameWidth, alignment: .leading)
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
            Text(footer)
                .font(.system(size: 11)).foregroundStyle(Palette.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(22)
        .frame(width: provider == nil ? 820 : 720, height: provider == nil ? 560 : 460)
        .task { await load() }
    }

    private var nameWidth: CGFloat { provider == nil ? 250 : 190 }

    private var footer: String {
        var text = "Measure puts the same public items to a model — GSM8K, MATH, AIME, MBPP, "
            + "HumanEval, TriviaQA, MMLU-Pro, thirty each — so every model, local or not, "
            + "lands on one scale; a few dozen items here outrank the boards. Public scores: "
            + "Epoch AI and Hugging Face's leaderboard data, relative to the best model on "
            + "each benchmark. Cost is relative to the fast tier, from API prices where known."
        if let b = bench {
            let have = b.sets.values.reduce(0) { $0 + $1.items }
            text += have > 0 ? " \(have) items are fetched." : " Items are fetched on first use."
            let waiting = b.due.filter { !$0.hold.isEmpty }
            let ready = b.due.filter { $0.hold.isEmpty }
            if b.auto_measure == "off" {
                text += " Measuring on its own is off."
            } else if !ready.isEmpty {
                text += " Next on its own: \(ready.map(\.provider).joined(separator: ", "))."
            } else if !waiting.isEmpty {
                text += " Waiting: " + waiting.map { "\($0.provider) (\($0.hold))" }
                    .joined(separator: "; ") + "."
            }
        }
        return text
    }

    private func row(_ m: RegistryModel) -> some View {
        HStack(spacing: 0) {
            HStack(spacing: 8) {
                Toggle("", isOn: Binding(
                    get: { m.enabled },
                    set: { on in Task {
                        try? await model.client.setModel(provider: m.provider, model: m.model,
                                                         enabled: on)
                        await load()
                    } }))
                    .labelsHidden().toggleStyle(AccentSwitch()).controlSize(.mini)
                    .disabled(m.isDefault)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title(m))
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(m.enabled ? Palette.ink : Palette.inkFaint)
                        .lineLimit(1)
                    Text(sub(m)).font(.system(size: 10.5)).foregroundStyle(Palette.inkFaint)
                        .lineLimit(1)
                }
            }
            .frame(width: nameWidth, alignment: .leading)
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

    /// The hard-work number, since that's what a choice between models turns
    /// on: measured if eki has it, else the public boards, else the prior.
    private func cell(_ s: ModelScore?) -> some View {
        guard let s else {
            return Text("–").font(.system(size: 12)).foregroundStyle(Palette.inkFaint)
        }
        if let m = s.measured["hard"] ?? s.measured["medium"] ?? s.measured["easy"] {
            return Text(String(format: "%.2f", m.score))
                .font(.system(size: 12, weight: .bold).monospacedDigit())
                .foregroundStyle(Palette.ink)
        }
        if let p = s.public?["hard"] ?? s.public?["medium"] ?? s.public?["easy"] {
            return Text(String(format: "%.2f", p))
                .font(.system(size: 12).monospacedDigit())
                .foregroundStyle(Palette.ink)
        }
        return Text(String(format: "%.2f", s.prior))
            .font(.system(size: 12).monospacedDigit())
            .foregroundStyle(Palette.inkFaint)
    }

    private func title(_ m: RegistryModel) -> String {
        guard provider == nil else { return m.isDefault ? "Default" : m.label }
        let name = model.providers.first { $0.key == m.provider }?.label ?? m.provider
        return m.isDefault ? name : "\(name) · \(m.label)"
    }

    private func sub(_ m: RegistryModel) -> String {
        var bits: [String] = []
        if let p = m.public, !p.name.isEmpty { bits.append("boards: \(p.name)") }
        else { bits.append(m.class.replacingOccurrences(of: "_", with: " ")) }
        if let c = m.cost { bits.append(String(format: "cost ×%g", c)) }
        if let s = m.speed_tok_s { bits.append("\(Int(s)) tok/s") }
        return bits.joined(separator: " · ")
    }

    private func load() async {
        let all = (try? await model.client.registry()) ?? []
        if let provider {
            models = all.filter { $0.provider == provider.key }
                .sorted { ($0.isDefault ? 0 : 1, $0.label) < ($1.isDefault ? 0 : 1, $1.label) }
        } else {
            let order = Dictionary(uniqueKeysWithValues:
                model.providers.enumerated().map { ($1.key, $0) })
            models = all.filter { $0.enabled }
                .sorted { (order[$0.provider] ?? 99, $0.isDefault ? 0 : 1, $0.label)
                        < (order[$1.provider] ?? 99, $1.isDefault ? 0 : 1, $1.label) }
        }
        bench = try? await model.client.bench()
    }

    private func measure(_ m: RegistryModel) async {
        measuring.insert(m.id)
        defer { measuring.remove(m.id) }
        do {
            let run = try await model.client.measure(provider: m.provider, model: m.model)
            note = "Measuring \(title(m))… slots land as they complete; a local model takes "
                + "half an hour or more."
            // wait for the run to finish, then reload the scores
            for _ in 0..<2700 {
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
