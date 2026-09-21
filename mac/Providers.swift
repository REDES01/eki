// SPDX-License-Identifier: Apache-2.0
// Providers and new models.
//
// Adding a provider is picking what it is and giving it the one thing it
// needs — nothing, a key, or an address — then testing before saving, so a
// typo shows up here and not as a failed answer later. Adding a model is
// choosing one that fits: the sheet says how much memory it will want before
// anything is downloaded.
import SwiftUI

// MARK: - data

struct ProviderDTO: Codable, Identifiable, Hashable {
    struct Options: Codable, Hashable {
        var model: String?
        var base_url: String?
        var secret: Bool?
    }
    struct Runtime: Codable, Hashable {
        var port: Int?
        var tok_s: Double?
        var repo: String?
        var idle_minutes: Double?
    }
    let key: String
    let kind: String
    let label: String
    let enabled: Bool
    let tier: Int
    let note: String
    var quota_source: String?
    var options: Options
    var runtime: Runtime
    var has_key: Bool
    var ok: Bool
    var detail: String

    var id: String { key }
    var needsKey: Bool { options.secret == true }
    var kindWord: String {
        switch kind {
        case "claude_code": return "Claude Code CLI"
        case "codex": return "Codex CLI"
        case "anthropic_api": return "Anthropic API"
        case "openai_compat": return "OpenAI-compatible"
        case "mlx": return "MLX"
        case "comfyui": return "ComfyUI"
        default: return kind
        }
    }
}

struct ProviderTemplate: Codable, Identifiable, Hashable {
    struct Options: Codable, Hashable { var base_url: String? }
    let id: String
    let title: String
    let kind: String
    let blurb: String
    let needs: String            // "binary" | "key" | "url"
    let tier: Int
    let note: String
    var options: Options
    var found: String?
}

struct ProbeResult: Codable, Hashable {
    let ok: Bool
    let detail: String
    let models: [String]
}

struct CatalogModel: Codable, Identifiable, Hashable {
    let repo: String
    let downloads: Int
    let likes: Int
    let task: String
    var id: String { repo }
}

/// A model worth downloading on this Mac (see eki/suggest.py).
struct Suggestion: Codable, Identifiable, Hashable {
    struct Other: Codable, Hashable {
        let repo: String
        let bits: Int
        let need_gb: Double
        let context: Int
        let fits: Bool
    }
    let base: String
    let name: String
    let repo: String
    let bits: Int
    let weights_gb: Double
    let need_gb: Double
    let context: Int
    let native: Int
    let fits_now: Bool
    let downloads: Int
    let scores: [String: Double]
    let installed: Bool
    var label: String? = ""
    var others: [Other]? = []

    var id: String { repo }
    var build: String { repo.replacingOccurrences(of: "mlx-community/", with: "") }
    /// "Code 68 · Chat 78 · Math 86" — percent of the best model on the boards
    var scoreLine: String {
        [("code", "Code"), ("chat", "Chat"), ("math", "Math")]
            .compactMap { key, word in scores[key].map { "\(word) \(Int(($0 * 100).rounded()))" } }
            .joined(separator: " · ")
    }
}

struct Suggestions: Codable {
    let suggestions: [Suggestion]
    let ceiling_gb: Double
    let free_gb: Double
    var attribution: String? = ""
}

struct ModelFit: Codable, Hashable {
    let repo: String
    let weights_gb: Double
    let download_gb: Double
    let context: Int
    let vision: Bool
    let license: String
    let gated: Bool
    let need_gb: Double
    let free_gb: Double
    var ceiling_gb: Double? = nil
    let fits: Bool                  // on this Mac at all
    var fits_now: Bool? = nil       // beside what's loaded this minute
    let verdict: String
    /// how the window was sized (see eki/context.py); nil when the repo has no config
    var window: ContextWindow? = nil
    /// what the build is, from the Hub's config and file list
    var profile: ModelProfile? = nil
}

struct HubSettings: Codable, Hashable {
    var mlx_python: String = ""
    var hf_home: String = ""
    var context_budget: Int = 32768
    var router_model: String = ""
    var router: String = "rules"
    var claude_probe: Bool = false
    var claude_probe_minutes: Int = 30
    var auto_measure: String = "local"
    var permissions: String = "auto"
    var claude_system_prompt: String = ""
}

extension EngineClient {
    func settings() async throws -> HubSettings {
        try await decode(HubSettings.self, "GET", "api/settings")
    }

    @discardableResult
    func save(settings: HubSettings) async throws -> HubSettings {
        try await decode(HubSettings.self, "PUT", "api/settings", body: [
            "mlx_python": settings.mlx_python,
            "hf_home": settings.hf_home,
            "context_budget": settings.context_budget,
            "router_model": settings.router_model,
            "router": settings.router,
            "claude_probe": settings.claude_probe,
            "claude_probe_minutes": settings.claude_probe_minutes,
            "auto_measure": settings.auto_measure,
            "permissions": settings.permissions,
            "claude_system_prompt": settings.claude_system_prompt,
        ])
    }

    func providers() async throws -> [ProviderDTO] {
        struct W: Codable { let providers: [ProviderDTO] }
        return try await decode(W.self, "GET", "api/providers").providers
    }

    func templates() async throws -> [ProviderTemplate] {
        struct W: Codable { let templates: [ProviderTemplate] }
        return try await decode(W.self, "GET", "api/providers/templates").templates
    }

    func discover() async throws -> [ProviderTemplate] {
        struct W: Codable { let found: [ProviderTemplate] }
        return try await decode(W.self, "GET", "api/providers/discover").found
    }

    func testProvider(_ body: [String: Any]) async throws -> ProbeResult {
        try await decode(ProbeResult.self, "POST", "api/providers/test", body: body)
    }

    func addProvider(_ body: [String: Any]) async throws -> ProviderDTO {
        try await decode(ProviderDTO.self, "POST", "api/providers", body: body)
    }

    func patchProvider(_ key: String, _ body: [String: Any]) async throws -> ProviderDTO {
        try await decode(ProviderDTO.self, "PATCH", "api/providers/\(key)", body: body)
    }

    func removeProvider(_ key: String) async throws {
        struct W: Codable { let deleted: String }
        _ = try await decode(W.self, "DELETE", "api/providers/\(key)")
    }

    func providerModels(_ key: String) async throws -> ProbeResult {
        try await decode(ProbeResult.self, "GET", "api/providers/\(key)/models")
    }

    func catalog(_ query: String) async throws -> [CatalogModel] {
        struct W: Codable { let models: [CatalogModel] }
        let q = query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        return try await decode(W.self, "GET", "api/catalog?q=\(q)").models
    }

    func suggestions(fresh: Bool = false) async throws -> Suggestions {
        try await decode(Suggestions.self, "GET", "api/catalog/suggest?fresh=\(fresh)")
    }

    func fit(_ repo: String) async throws -> ModelFit {
        let q = repo.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? repo
        return try await decode(ModelFit.self, "GET", "api/catalog/fit?repo=\(q)")
    }

    func deploy(_ repo: String, label: String) async throws -> String {
        struct W: Codable { let run: String }
        return try await decode(W.self, "POST", "api/deploy",
                                body: ["repo": repo, "label": label]).run
    }
}

// MARK: - the list

struct ProviderCard: View {
    @EnvironmentObject var model: AppModel
    let provider: ProviderDTO
    @State private var confirmRemove = false
    @State private var editingKey = false
    @State private var showingModels = false
    @State private var fixing = false
    @State private var fixNote = ""

    /// Codex without its code-mode helper: it answers, but can't touch a file.
    private var codexNeedsHost: Bool {
        provider.kind == "codex" && provider.detail.contains("codex-code-mode-host")
    }

    private var inAuto: Bool { !model.policy.disabled.contains(provider.key) }
    private var isFirst: Bool { model.policy.order.first == provider.key }

    var body: some View {
        Card {
            HStack(spacing: 12) {
                Dot(color: Palette.backend(provider.key), size: 8)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 7) {
                        Text(provider.label)
                            .font(.system(size: 13.5, weight: .medium))
                            .foregroundStyle(inAuto ? Palette.ink : Palette.inkFaint)
                        if isFirst { Tag(text: "first choice", color: Palette.accent) }
                        if !provider.ok && provider.runtime.port == nil {
                            Tag(text: "not reachable", color: Palette.danger)
                        }
                        if codexNeedsHost { Tag(text: "can't edit files", color: Palette.inkMuted) }
                    }
                    Text(detail)
                        .font(.system(size: 11.5))
                        .foregroundStyle(Palette.inkMuted)
                        .lineLimit(1)
                        .help(provider.detail)
                }
                Spacer()
                if codexNeedsHost {
                    if fixing {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Fix editing") { Task { await fixCodex() } }
                            .buttonStyle(AccentButton())
                            .help("Downloads the codex-code-mode-host helper matching your "
                                  + "Codex version from Codex's own GitHub release, checks it "
                                  + "is signed by OpenAI, and puts it beside the codex binary.")
                    }
                }
                if inAuto && !isFirst {
                    Button("Prefer") { Task { await model.prefer(provider.key) } }
                        .buttonStyle(GhostButton())
                        .help("Wins ties against providers of the same cost")
                }
                Menu {
                    Button("Models…") { showingModels = true }
                    if provider.needsKey {
                        Button("Replace API key…") { editingKey = true }
                    }
                    Divider()
                    Button("Remove…", role: .destructive) { confirmRemove = true }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Palette.inkMuted)
                        .frame(width: 22, height: 22)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .tint(Palette.inkMuted)
                .fixedSize()
                Toggle("", isOn: Binding(
                    get: { inAuto },
                    set: { on in Task { await model.toggle(backend: provider.key, enabled: on) } }))
                    .labelsHidden()
                    .toggleStyle(AccentSwitch())
                    .help("Off means never chosen automatically — you can still pick it by name")
            }
        }
        .confirmationDialog("Remove \(provider.label)?", isPresented: $confirmRemove) {
            Button("Remove", role: .destructive) {
                Task { await model.removeProvider(provider.key) }
            }
        } message: {
            Text(provider.runtime.repo != nil
                 ? "eki forgets it. The downloaded weights stay in the Hugging Face cache."
                 : provider.needsKey ? "Its API key is deleted from the Keychain too."
                 : "Nothing on disk is touched.")
        }
        .sheet(isPresented: $editingKey) { ReplaceKeySheet(provider: provider) }
        .sheet(isPresented: $showingModels) { ModelsSheet(provider: provider) }
        .alert("Codex", isPresented: Binding(get: { !fixNote.isEmpty },
                                             set: { if !$0 { fixNote = "" } })) {
            Button("OK") { fixNote = "" }
        } message: { Text(fixNote) }
    }

    private func fixCodex() async {
        fixing = true
        defer { fixing = false }
        do {
            let message = try await model.client.installCodexHost(provider.key)
            // it can edit now: say so to the router as well
            _ = try await model.client.patchProvider(provider.key,
                                                     ["capabilities": ["tools": true, "repo": true]])
            fixNote = message + ". Codex can now take repo work."
        } catch ClientError.http(_, let body) {
            fixNote = body
        } catch {
            fixNote = error.localizedDescription
        }
        await model.refreshProviders()
    }

    private var detail: String {
        var bits = [provider.kindWord]
        if let m = provider.options.model, !m.isEmpty { bits.append(m.split(separator: "/").last.map(String.init) ?? m) }
        bits.append(provider.note.isEmpty ? "tier \(provider.tier)" : provider.note)
        if let t = provider.runtime.tok_s { bits.append("\(Int(t)) tok/s") }
        if !provider.ok, !provider.detail.isEmpty { bits.append(provider.detail) }
        return bits.joined(separator: " · ")
    }
}

struct ReplaceKeySheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let provider: ProviderDTO
    @State private var key = ""
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New API key for \(provider.label)").font(.hubTitle)
            SecureField("Paste the key", text: $key).textFieldStyle(.roundedBorder)
            Text("Stored in your Keychain, never in eki's files.")
                .font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
            if !error.isEmpty { Text(error).font(.system(size: 12)).foregroundStyle(Palette.danger) }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
                Button("Save") {
                    Task {
                        do {
                            _ = try await model.client.patchProvider(provider.key, ["api_key": key])
                            await model.refreshProviders()
                            dismiss()
                        } catch { self.error = error.localizedDescription }
                    }
                }
                .buttonStyle(AccentButton())
                .disabled(key.isEmpty)
            }
        }
        .padding(22)
        .frame(width: 420)
    }
}

// MARK: - adding a provider

struct AddProviderSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var templates: [ProviderTemplate] = []
    @State private var found: [ProviderTemplate] = []
    @State private var picked: ProviderTemplate?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let picked {
                ProviderForm(template: picked, back: { self.picked = nil }, done: { dismiss() })
            } else {
                chooser
            }
        }
        .frame(width: 520, height: 520)
        .task {
            async let t = try? await model.client.templates()
            async let f = try? await model.client.discover()
            templates = await t ?? []
            found = await f ?? []
        }
    }

    private var chooser: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Add a provider").font(.hubTitle)
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if !found.isEmpty {
                        SectionLabel(text: "Found on this Mac")
                        ForEach(found) { t in
                            TemplateRow(template: t, detail: t.found ?? "") { picked = t }
                        }
                    }
                    SectionLabel(text: "Everything eki can use")
                    // a second Claude Code or Codex would be the same login twice
                    ForEach(templates.filter { t in
                        t.needs != "binary" || !model.providers.contains { $0.kind == t.kind }
                    }) { t in
                        TemplateRow(template: t, detail: t.blurb) { picked = t }
                    }
                }
            }
        }
        .padding(22)
    }
}

struct TemplateRow: View {
    let template: ProviderTemplate
    let detail: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(template.title).font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Palette.ink)
                    Text(detail).font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
                        .lineLimit(1)
                }
                Spacer()
                Tag(text: template.note.isEmpty ? "tier \(template.tier)" : template.note)
                Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Palette.inkFaint)
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            .background(hovering ? Palette.fill : Palette.surface,
                        in: RoundedRectangle(cornerRadius: Metric.radius))
            .overlay(RoundedRectangle(cornerRadius: Metric.radius)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

struct ProviderForm: View {
    @EnvironmentObject var model: AppModel
    let template: ProviderTemplate
    let back: () -> Void
    let done: () -> Void

    @State private var label = ""
    @State private var url = ""
    @State private var apiKey = ""
    @State private var chosenModel = ""
    @State private var probe: ProbeResult?
    @State private var busy = false
    @State private var error = ""

    private var body_: [String: Any] {
        var b: [String: Any] = ["template": template.id, "label": label]
        if template.needs == "url" { b["base_url"] = url }
        if template.needs == "key" { b["api_key"] = apiKey }
        if !chosenModel.isEmpty { b["model"] = chosenModel }
        return b
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Button { back() } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(GhostButton())
                Text(template.title).font(.hubTitle)
                Spacer()
            }
            Text(template.blurb).font(.system(size: 12)).foregroundStyle(Palette.inkMuted)

            field("Name") { TextField(template.title, text: $label).textFieldStyle(.roundedBorder) }
            if template.needs == "url" {
                field("Address") { TextField("http://127.0.0.1:8000/v1", text: $url)
                    .textFieldStyle(.roundedBorder) }
            }
            if template.needs == "key" {
                field("API key") { SecureField("Paste the key", text: $apiKey)
                    .textFieldStyle(.roundedBorder) }
                Text("Kept in your Keychain. eki never writes it to a file.")
                    .font(.system(size: 11)).foregroundStyle(Palette.inkFaint)
            }
            if template.needs == "binary" {
                Text(template.found.map { "Uses \($0), signed in with your own account. "
                    + "eki runs it; it never reads its login." }
                     ?? "Install it and sign in first; eki runs it as you.")
                    .font(.system(size: 12)).foregroundStyle(Palette.inkMuted)
            }
            if let probe, !probe.models.isEmpty, template.needs != "binary" {
                field("Model") {
                    Picker("", selection: $chosenModel) {
                        if template.needs == "url" { Text("Whatever is loaded").tag("") }
                        ForEach(probe.models, id: \.self) { Text($0).tag($0) }
                    }
                    .labelsHidden()
                }
            }
            if let probe {
                HStack(spacing: 6) {
                    Image(systemName: probe.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(probe.ok ? Palette.ok : Palette.danger)
                    Text(probe.ok ? "Reachable · \(probe.detail)" : probe.detail)
                        .font(.system(size: 12)).lineLimit(2)
                }
            }
            if !error.isEmpty {
                Text(error).font(.system(size: 12)).foregroundStyle(Palette.danger)
            }
            Spacer()
            HStack {
                if busy { ProgressView().controlSize(.small) }
                Spacer()
                Button("Test") { Task { await test() } }
                    .buttonStyle(GhostButton())
                    .disabled(busy || !ready)
                Button("Add") { Task { await add() } }
                    .buttonStyle(AccentButton())
                    .disabled(busy || !ready || needsModel)
            }
        }
        .padding(22)
        .onAppear {
            url = template.found ?? template.options.base_url ?? ""
        }
    }

    private var ready: Bool {
        switch template.needs {
        case "key": return !apiKey.isEmpty
        case "url": return !url.isEmpty
        default: return true
        }
    }

    /// A hosted API won't guess which model you mean.
    private var needsModel: Bool {
        template.needs == "key" && template.kind == "openai_compat" && chosenModel.isEmpty
    }

    private func field<C: View>(_ name: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(name).font(.system(size: 11.5, weight: .medium)).foregroundStyle(Palette.inkMuted)
            content()
        }
    }

    private func test() async {
        busy = true; error = ""
        defer { busy = false }
        do {
            let result = try await model.client.testProvider(body_)
            probe = result
            if chosenModel.isEmpty, template.needs == "key", let first = result.models.first {
                chosenModel = first
            }
        } catch { self.error = error.localizedDescription }
    }

    private func add() async {
        busy = true; error = ""
        defer { busy = false }
        do {
            _ = try await model.client.addProvider(body_)
            await model.refreshProviders()
            done()
        } catch { self.error = error.localizedDescription }
    }
}

// MARK: - adding a model

struct AddModelSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var results: [CatalogModel] = []
    @State private var selected: CatalogModel?
    @State private var fit: ModelFit?
    @State private var loading = false
    @State private var error = ""
    @State private var suggested: Suggestions?
    @State private var suggesting = true

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Add a local model").font(.hubTitle)
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
            }
            Text("MLX builds from Hugging Face's mlx-community. eki downloads it, "
                 + "writes a start script, measures it and adds it as a provider.")
                .font(.system(size: 12)).foregroundStyle(Palette.inkMuted)
                .fixedSize(horizontal: false, vertical: true)
            TextField("Search, e.g. Qwen3.5 4B", text: $query)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await search() } }
            HStack(alignment: .top, spacing: 12) {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        if query.isEmpty {
                            recommended
                            Text("Popular")
                                .font(.system(size: 10.5, weight: .semibold))
                                .foregroundStyle(Palette.inkFaint)
                                .textCase(.uppercase)
                                .padding(.horizontal, 9).padding(.top, 10).padding(.bottom, 2)
                        }
                        ForEach(results) { m in
                            Button {
                                selected = m
                                Task { await loadFit() }
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(m.repo.replacingOccurrences(of: "mlx-community/", with: ""))
                                        .font(.system(size: 12.5))
                                        .foregroundStyle(Palette.ink)
                                    Text("\(m.downloads.formatted()) downloads")
                                        .font(.system(size: 10.5)).foregroundStyle(Palette.inkFaint)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 9).padding(.vertical, 6)
                                .background(selected?.id == m.id ? Palette.fill : Color.clear,
                                            in: RoundedRectangle(cornerRadius: 6))
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(4)
                }
                .background(Palette.surface, in: RoundedRectangle(cornerRadius: Metric.radius))
                .overlay(RoundedRectangle(cornerRadius: Metric.radius)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
                .frame(width: 300)
                fitPanel.frame(maxWidth: .infinity, alignment: .topLeading)
            }
            if !error.isEmpty { Text(error).font(.system(size: 12)).foregroundStyle(Palette.danger) }
        }
        .padding(22)
        .frame(width: 680, height: 560)
        .task { await search() }
        .task { await loadSuggestions() }
    }

    /// The models worth having on this Mac: the boards' quality, the
    /// catalogue's builds, this machine's memory — joined by the engine.
    @ViewBuilder private var recommended: some View {
        HStack(spacing: 6) {
            Text("Recommended for this Mac")
                .font(.system(size: 10.5, weight: .semibold))
                .foregroundStyle(Palette.inkFaint)
                .textCase(.uppercase)
            if suggesting { ProgressView().controlSize(.mini) }
            Spacer()
        }
        .padding(.horizontal, 9).padding(.top, 4).padding(.bottom, 2)
        .help("Ranked by public benchmark results for the base model, among builds that fit "
              + "in this Mac's memory. Once a model is measured here, that number takes over.")
        if let suggested {
            ForEach(suggested.suggestions) { s in
                Button {
                    selected = CatalogModel(repo: s.repo, downloads: s.downloads, likes: 0, task: "text-generation")
                    Task { await loadFit() }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(s.name).font(.system(size: 12.5)).foregroundStyle(Palette.ink)
                            if let label = s.label, !label.isEmpty {
                                Tag(text: label, color: Palette.accent)
                            }
                            if s.installed { Tag(text: "installed", color: Palette.ok) }
                        }
                        Text("\(s.bits)-bit · \(String(format: "%.1f", s.need_gb)) GB · up to \(s.context / 1024)k context"
                             + (s.fits_now ? "" : " · room needed"))
                            .font(.system(size: 10.5)).foregroundStyle(Palette.inkFaint)
                        if !s.scoreLine.isEmpty {
                            Text(s.scoreLine).font(.system(size: 10.5)).foregroundStyle(Palette.inkMuted)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 9).padding(.vertical, 6)
                    .background(selected?.repo == s.repo ? Palette.fill : Color.clear,
                                in: RoundedRectangle(cornerRadius: 6))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            if suggested.suggestions.isEmpty && !suggesting {
                Text("Nothing on the boards fits in \(String(format: "%.0f", suggested.ceiling_gb)) GB.")
                    .font(.system(size: 11)).foregroundStyle(Palette.inkFaint)
                    .padding(.horizontal, 9)
            }
        }
    }

    private func loadSuggestions() async {
        suggesting = true
        defer { suggesting = false }
        do { suggested = try await model.client.suggestions() }
        catch { self.error = error.localizedDescription }
    }

    @ViewBuilder private var fitPanel: some View {
        if loading {
            ProgressView().controlSize(.small)
        } else if let fit, let selected {
            VStack(alignment: .leading, spacing: 9) {
                Text(selected.repo.replacingOccurrences(of: "mlx-community/", with: ""))
                    .font(.system(size: 13.5, weight: .medium))
                if let summary = fit.profile?.summary {
                    Text(summary)
                        .font(.system(size: 11.5))
                        .foregroundStyle(Palette.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                row("Download", String(format: "%.1f GB", fit.download_gb))
                row("Needs", String(format: "%.1f GB with a %dk context", fit.need_gb, fit.context / 1024))
                if let window = fit.window {
                    row("Context", window.limited_by == "memory"
                        ? "\(window.tokens / 1024)k — the most this Mac can give it (native \(window.native / 1024)k); you can set it smaller later"
                        : window.limited_by == "speed"
                        ? "\(window.tokens / 1024)k — the speed cap (native \(window.native / 1024)k); you can change it later"
                        : "\(window.tokens / 1024)k — the model's maximum")
                }
                row("This Mac", String(format: "%.1f GB for models, %.1f GB free now",
                                        fit.ceiling_gb ?? 0, fit.free_gb))
                if !fit.license.isEmpty { row("License", fit.license) }
                HStack(spacing: 6) {
                    Image(systemName: fit.fits ? "checkmark.circle.fill" : "exclamationmark.circle")
                        .foregroundStyle(fit.fits ? Palette.ok : Palette.inkMuted)
                    Text(!fit.fits ? "Too big for this Mac's memory, even with nothing else loaded"
                         : (fit.verdict == "tight" ? "Fits, but only just"
                            : "Fits on this Mac")
                           + (fit.fits_now == false ? " — starts once other models unload to make room" : ""))
                        .font(.system(size: 12))
                        .fixedSize(horizontal: false, vertical: true)
                }
                if fit.gated {
                    Text("Gated: accept its terms on Hugging Face first.")
                        .font(.system(size: 12)).foregroundStyle(Palette.danger)
                }
                Spacer()
                HStack {
                    Spacer()
                    Button("Download and set up") {
                        Task {
                            do {
                                let run = try await model.client.deploy(selected.repo, label: "")
                                model.show(deploy: run)
                                dismiss()
                            } catch { self.error = error.localizedDescription }
                        }
                    }
                    .buttonStyle(AccentButton())
                    .disabled(fit.gated)
                }
                Text("Runs in the background — close this and watch it under Local servers.")
                    .font(.system(size: 11)).foregroundStyle(Palette.inkFaint)
            }
        } else {
            Text("Pick a model to see whether it fits.")
                .font(.system(size: 12)).foregroundStyle(Palette.inkFaint)
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack {
            Text(k).font(.system(size: 12)).foregroundStyle(Palette.inkMuted)
                .frame(width: 70, alignment: .leading)
            Text(v).font(.system(size: 12).monospacedDigit())
        }
    }

    private func search() async {
        error = ""
        do { results = try await model.client.catalog(query) }
        catch { self.error = error.localizedDescription }
    }

    private func loadFit() async {
        guard let selected else { return }
        loading = true; fit = nil
        defer { loading = false }
        do { fit = try await model.client.fit(selected.repo) }
        catch { self.error = error.localizedDescription }
    }
}
