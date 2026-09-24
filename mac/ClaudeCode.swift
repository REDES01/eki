// SPDX-License-Identifier: Apache-2.0
// Claude Code's panels, drawn by eki.
//
// The terminal has /mcp, /model, /permissions, /usage, /context, /rewind…
// Each is one control request the program answers over its streaming
// protocol (eki/live.py, Engine.claude_control); these views draw the
// replies in eki's own style. Typing the slash command in the composer
// opens the panel here instead of sending the words.
import AppKit
import SwiftUI

/// The sheet the composer opens for a slash command.
struct ClaudePanelSheet: View {
    @EnvironmentObject var model: AppModel
    let panel: ClaudePanel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            SheetHeader(title: panel.title) {
                Button("Done") { dismiss() }
                    .buttonStyle(AccentButton())
                    .keyboardShortcut(.defaultAction)
            }
            Hairline()
            switch panel {
            case .mcp: McpPanel()
            case .model: ModelPanel()
            case .permissions: PermissionsPanel()
            case .usage: UsagePanel()
            case .context: ContextPanel()
            case .rewind: RewindPanel()
            case .hooks: HooksPanel()
            case .agents: AgentsPanel()
            case .tasks: TasksPanel()
            case .status: StatusPanel()
            case .config: ConfigPanel()
            case .memory: MemoryPanel()
            case .skills: SkillsPanel()
            case .plugins: PluginsPanel()
            }
        }
        .frame(minWidth: 620, idealWidth: 680, minHeight: 420, idealHeight: 520)
        .background(Palette.canvas)
    }
}

/// A panel's loading and error states, shared.
struct PanelState<Content: View>: View {
    let loading: Bool
    let error: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        if loading {
            VStack { Spacer(); ProgressView().controlSize(.small); Spacer() }
                .frame(maxWidth: .infinity)
        } else if !error.isEmpty {
            VStack { Spacer(); Text(error).font(.hubCallout).foregroundStyle(Palette.danger)
                .multilineTextAlignment(.center).padding(.all, Space.xl); Spacer() }
                .frame(maxWidth: .infinity)
        } else {
            content()
        }
    }
}

// ---- /mcp -------------------------------------------------------------------

/// One server as the program reports it (mcp_status).
struct McpServer: Identifiable, Hashable {
    let name: String
    let status: String          // connected | failed | needs-auth | pending | disabled
    let scope: String           // user | project | local | dynamic | claudeai | sdk | plugin …
    let tools: Int
    let error: String
    let kind: String            // stdio | http | sse | sdk | claudeai-proxy
    var id: String { name }

    init(_ v: JSONValue) {
        name = v["name"]?.text ?? "?"
        status = v["status"]?.text ?? "pending"
        let cfg = v["config"]?.objectValue ?? [:]
        kind = cfg["type"]?.text ?? ""
        scope = v["scope"]?.text ?? v["source"]?.text ?? (kind == "sdk" ? "eki" : "")
        tools = v["tools"]?.arrayValue.count ?? 0
        error = v["error"]?.text ?? ""
    }

    var color: Color {
        switch status {
        case "connected": return Palette.ok
        case "needs-auth": return Palette.warn
        case "failed": return Palette.danger
        case "disabled": return Palette.inkFaint
        default: return Palette.inkFaint
        }
    }

    var word: String {
        switch status {
        case "needs-auth": return "needs authentication"
        default: return status
        }
    }

    var group: String {
        if name == "computer-use" { return "Built-in (through eki)" }
        if name == "eki" { return "eki" }
        switch scope {
        case "claudeai": return "claude.ai"
        case "sdk", "eki": return "eki"
        case "dynamic": return "eki's registry (this session)"
        case "plugin": return "Plugins"
        case "user": return "Your Claude Code config"
        case "project", "local": return "This project"
        default: return scope.isEmpty ? "Servers" : scope
        }
    }
}

struct McpPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var servers: [McpServer] = []
    @State private var registry: [String: JSONValue] = [:]
    @State private var loading = true
    @State private var error = ""
    @State private var working = ""
    @State private var adding = false
    @State private var catalog: [JSONValue] = []
    @State private var picking: JSONValue? = nil

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.l) {
                    ForEach(groups, id: \.self) { group in
                        VStack(alignment: .leading, spacing: Space.s) {
                            SectionLabel(text: group)
                            ForEach(servers.filter { $0.group == group }) { s in
                                row(s)
                            }
                        }
                    }
                    registrySection
                    catalogSection
                }
                .padding(.all, Space.l)
            }
        }
        .task { await load(); catalog = (try? await model.client.mcpCatalog()) ?? [] }
        .sheet(isPresented: $adding) { AddMcpServerSheet { await load() } }
        .sheet(item: $picking) { entry in
            AddFromCatalogSheet(entry: entry) { await load(); await reloadRegistry() }
        }
    }

    /// Servers eki knows how to add in one step — a search, a browser, GitHub —
    /// with what each gives a backend. A local model under Codex can research
    /// once it has a search server; eki adds servers, it doesn't write them.
    private var catalogSection: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionLabel(text: "Add from the catalog").padding(.top, Space.s)
            ForEach(Array(catalog.enumerated()), id: \.offset) { _, e in
                let id: String = e["id"]?.text ?? ""
                let have: Bool = alreadyAdded(e)
                HStack(spacing: Space.m) {
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        HStack(spacing: Space.s) {
                            Text(e["title"]?.text ?? id).font(.hubHeading)
                            ForEach(e["provides"]?.arrayValue.map(\.text) ?? [], id: \.self) { p in
                                Text(p).font(.hubLabel).tracking(0.4)
                                    .padding(.horizontal, Space.s).padding(.vertical, Space.xxs)
                                    .background(Palette.accentSoft, in: Capsule())
                            }
                        }
                        Text(e["blurb"]?.text ?? "").font(.hubCaption).foregroundStyle(Palette.inkMuted)
                    }
                    Spacer()
                    Button(have ? "Added" : "Add…") { picking = e }
                        .buttonStyle(GhostButton())
                        .disabled(have)
                }
                .tile()
            }
        }
    }

    private var groups: [String] {
        var seen: [String] = []
        for s in servers where !seen.contains(s.group) { seen.append(s.group) }
        return seen
    }

    private func row(_ s: McpServer) -> some View {
        HStack(spacing: Space.m) {
            Dot(color: s.color, size: 7, pulsing: s.status == "pending")
            VStack(alignment: .leading, spacing: Space.xxs) {
                HStack(spacing: Space.s) {
                    Text(s.name).font(.hubHeading)
                    if s.tools > 0 {
                        Text("\(s.tools) tools").font(.hubCaption).foregroundStyle(Palette.inkMuted)
                    }
                }
                Text(s.error.isEmpty ? s.word : "\(s.word) — \(s.error)")
                    .font(.hubCaption).foregroundStyle(s.status == "failed" ? Palette.danger : Palette.inkMuted)
                    .lineLimit(2)
                if s.name == "computer-use" {
                    Text("Claude Code's own screen control (opt-in in Settings → Routing). Its per-app "
                         + "approval is a dialog only Claude Code's own front ends show, so it may grant "
                         + "nothing here; eki's screen tools under “eki” work regardless.")
                        .font(.hubCaption).foregroundStyle(Palette.inkFaint)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
            if working == s.name {
                ProgressView().controlSize(.small)
            } else {
                if s.status == "needs-auth" {
                    Button("Authenticate") { Task { await act("mcp_authenticate", s.name, timeout: 300) } }
                        .buttonStyle(AccentButton())
                }
                if s.status == "failed" || s.status == "connected" || s.status == "disabled" {
                    Button(s.status == "disabled" ? "Enable" : "Reconnect") {
                        Task { await act("mcp_reconnect", s.name) }
                    }
                    .buttonStyle(GhostButton())
                }
                if s.status == "connected", s.kind != "sdk", s.kind != "claudeai-proxy", s.kind != "codex",
                   s.name != "computer-use", registry[s.name] == nil {
                    Button("Share with Codex") {
                        Task { await act("mcp_import", s.name, args: ["names": [s.name]]) }
                    }
                    .buttonStyle(GhostButton())
                    .help("Copy this server into eki's registry, so Codex has it too")
                }
                if s.kind != "sdk", s.kind != "codex", s.status != "disabled" {
                    Button("Disable") {
                        Task { await act("mcp_toggle", s.name, args: ["enabled": false]) }
                    }
                    .buttonStyle(GhostButton())
                }
            }
        }
        .tile()
    }

    private var registrySection: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack {
                SectionLabel(text: "eki's registry")
                Spacer()
                Button("Add server…") { adding = true }.buttonStyle(GhostButton())
                if !registry.isEmpty {
                    Button("Apply to this session") { Task { await act("mcp_apply", "") } }
                        .buttonStyle(GhostButton())
                        .help("Start the registry's servers in the open session without restarting it")
                }
            }
            if registry.isEmpty {
                Text("Servers declared here are rendered into Claude Code (per session) and "
                     + "Codex's config.toml. Nothing is written into ~/.claude by hand.")
                    .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(registry.keys.sorted(), id: \.self) { name in
                let spec = registry[name] ?? .null
                let backends = spec["backends"]?.arrayValue.map(\.text) ?? []
                let enabled = spec["enabled"]?.boolValue ?? true
                HStack(spacing: Space.m) {
                    Dot(color: enabled ? Palette.accent : Palette.inkFaint, size: 7)
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        Text(name).font(.hubHeading)
                        Text(commandLine(spec))
                            .font(.hubMonoSmall).foregroundStyle(Palette.inkMuted)
                            .lineLimit(1).truncationMode(.middle)
                    }
                    Spacer()
                    ForEach(["claude", "codex", "gemini"], id: \.self) { b in
                        Toggle(["claude": "Claude", "codex": "Codex"][b] ?? "Gemini", isOn: Binding(
                            get: { backends.contains(b) },
                            set: { on in Task { await registryToggle(name, on, backend: b) } }))
                            .toggleStyle(.checkbox)
                            .font(.hubCaption)
                    }
                    Button(enabled ? "Disable" : "Enable") {
                        Task { await registryToggle(name, !enabled) }
                    }
                    .buttonStyle(GhostButton())
                    Button("Remove") { Task { await registryRemove(name) } }
                        .buttonStyle(GhostButton())
                }
                .tile()
            }
        }
    }

    /// Whether a registry entry runs this catalog server (same command word).
    private func alreadyAdded(_ e: JSONValue) -> Bool {
        let line: String = e["command"]?.text ?? ""
        let words = line.split(separator: " ").map(String.init)
        guard words.count >= 2 else { return false }
        let marker = words[words.count - 1]            // the package name is the last word
        for spec in registry.values {
            let args: [String] = spec["args"]?.arrayValue.map(\.text) ?? []
            if args.contains(marker) { return true }
        }
        return false
    }

    private func commandLine(_ spec: JSONValue) -> String {
        if let url = spec["url"]?.stringValue, !url.isEmpty { return url }
        let command: String = spec["command"]?.text ?? ""
        let args: [String] = spec["args"]?.arrayValue.map(\.text) ?? []
        return ([command] + args).joined(separator: " ")
    }

    private func load() async {
        loading = servers.isEmpty
        error = ""
        do {
            let reply = try await model.client.claude("mcp", conversation: model.conversationID,
                                                      cwd: model.lastRepo, backend: model.preferredBackend, timeout: 120)
            apply(reply)
        } catch {
            self.error = "\(model.agentName) didn't answer: \(model.plainError(error))"
        }
        loading = false
    }

    private func apply(_ reply: JSONValue) {
        if let list = reply["servers"]?.arrayValue { servers = list.map(McpServer.init) }
        if let reg = reply["registry"]?.objectValue { registry = reg }
    }

    private func act(_ op: String, _ name: String, args: [String: Any] = [:],
                     timeout: TimeInterval = 90) async {
        working = name
        defer { working = "" }
        var a = args
        if !name.isEmpty { a["name"] = name }
        do {
            let reply = try await model.client.claude(op, conversation: model.conversationID,
                                                      cwd: model.lastRepo, backend: model.preferredBackend, args: a, timeout: timeout)
            apply(reply)
            if op == "mcp_authenticate" {
                // the program opened the browser (or said where to go); a
                // build that answers with the URL gets it opened here
                for key in ["url", "authUrl", "authorizationUrl", "opened"] {
                    if let u = reply["reply"]?[key]?.stringValue, let url = URL(string: u) {
                        NSWorkspace.shared.open(url)
                        break
                    }
                }
                model.say("Finish signing in in the browser, then Reconnect")
            }
            if op == "mcp_import" { await reloadRegistry() }
        } catch {
            // said in the panel, without replacing it: the rows stay usable
            model.chatError = "Claude Code: \(plain(error))"
        }
    }

    /// The engine's error text without the HTTP wrapping.
    private func plain(_ error: Error) -> String {
        let s = error.localizedDescription
        if let r = s.range(of: "\"detail\":\""), let e = s[r.upperBound...].range(of: "\"}") {
            return String(s[r.upperBound..<e.lowerBound])
        }
        return s
    }

    private func reloadRegistry() async {
        if let reg = try? await model.client.mcpRegistry()["servers"]?.objectValue { registry = reg }
    }

    private func registryToggle(_ name: String, _ on: Bool, backend: String = "") async {
        if let reg = try? await model.client.mcpEnabled(name, enabled: on, backend: backend)["servers"]?.objectValue {
            registry = reg
        }
    }

    private func registryRemove(_ name: String) async {
        if let reg = try? await model.client.mcpDelete(name)["servers"]?.objectValue { registry = reg }
    }
}

extension JSONValue: Identifiable {
    public var id: String { self["id"]?.text ?? self.text }
}

/// One catalog entry: a name, the key it asks for, which backends.
struct AddFromCatalogSheet: View {
    @EnvironmentObject var model: AppModel
    let entry: JSONValue
    let done: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var key = ""
    @State private var claude = true
    @State private var codex = true
    @State private var gemini = true
    @State private var error = ""

    private var keyEnv: String { entry["key_env"]?.text ?? "" }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            Text("Add \(entry["title"]?.text ?? "")").font(.hubTitle)
            Text(entry["blurb"]?.text ?? "").font(.hubCallout).foregroundStyle(Palette.inkMuted)
            Text(entry["command"]?.text ?? "").font(.hubMonoSmall).foregroundStyle(Palette.inkFaint)
            TextField("Name in eki", text: $name).textFieldStyle(.roundedBorder)
            if !keyEnv.isEmpty {
                SecureField("\(keyEnv) — kept in the server's environment", text: $key).textFieldStyle(.roundedBorder)
            }
            HStack(spacing: Space.l) {
                Toggle("Claude Code", isOn: $claude).toggleStyle(.checkbox)
                Toggle("Codex (and local models under it)", isOn: $codex).toggleStyle(.checkbox)
                Toggle("Gemini CLI", isOn: $gemini).toggleStyle(.checkbox)
            }
            if !error.isEmpty { Text(error).font(.hubCallout).foregroundStyle(Palette.danger) }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
                Button("Add") { Task { await add() } }.buttonStyle(AccentButton())
                    .disabled(name.isEmpty || (!keyEnv.isEmpty && key.isEmpty))
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(.all, Space.xl)
        .frame(width: 520)
        .background(Palette.canvas)
        .onAppear { name = entry["id"]?.text ?? "" }
    }

    private func add() async {
        var backends: [String] = []
        if claude { backends.append("claude") }
        if codex { backends.append("codex") }
        if gemini { backends.append("gemini") }
        do {
            _ = try await model.client.mcpPut(name, spec: ["name": name, "catalog": entry["id"]?.text ?? "",
                                                          "key": key, "backends": backends])
            await done()
            dismiss()
        } catch {
            self.error = model.plainError(error)
        }
    }
}

struct AddMcpServerSheet: View {
    @EnvironmentObject var model: AppModel
    let done: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var command = ""
    @State private var url = ""
    @State private var env = ""
    @State private var claude = true
    @State private var codex = true
    @State private var gemini = true
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            Text("Add an MCP server").font(.hubTitle)
            TextField("Name (letters, digits, - _ .)", text: $name).textFieldStyle(.roundedBorder)
            TextField("Command, e.g. npx -y @modelcontextprotocol/server-filesystem ~/Projects", text: $command)
                .textFieldStyle(.roundedBorder).font(.hubMonoSmall)
            TextField("…or a URL for a remote (HTTP) server", text: $url).textFieldStyle(.roundedBorder).font(.hubMonoSmall)
            TextField("Environment, KEY=value per line", text: $env, axis: .vertical)
                .textFieldStyle(.roundedBorder).font(.hubMonoSmall).lineLimit(1...4)
            HStack(spacing: Space.l) {
                Toggle("Claude Code", isOn: $claude).toggleStyle(.checkbox)
                Toggle("Codex", isOn: $codex).toggleStyle(.checkbox)
                Toggle("Gemini CLI", isOn: $gemini).toggleStyle(.checkbox)
            }
            if !error.isEmpty {
                Text(error).font(.hubCallout).foregroundStyle(Palette.danger)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
                Button("Add") { Task { await add() } }.buttonStyle(AccentButton())
                    .disabled(name.isEmpty || (command.isEmpty && url.isEmpty))
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(.all, Space.xl)
        .frame(width: 520)
        .background(Palette.canvas)
    }

    private func add() async {
        var envMap: [String: String] = [:]
        for line in env.split(separator: "\n") {
            let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            if parts.count == 2 { envMap[parts[0]] = parts[1] }
        }
        var backends: [String] = []
        if claude { backends.append("claude") }
        if codex { backends.append("codex") }
        if gemini { backends.append("gemini") }
        let spec: [String: Any] = ["name": name, "command": command, "url": url,
                                   "env": envMap, "backends": backends]
        do {
            _ = try await model.client.mcpPut(name, spec: spec)
            await done()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// ---- /model ------------------------------------------------------------------

struct ModelPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var models: [JSONValue] = []
    @State private var current = ""
    @State private var account: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.s) {
                    if let email = account["email"]?.stringValue {
                        Text("\(email)" + (account["subscriptionType"]?.stringValue.map { " · \($0)" } ?? ""))
                            .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                            .padding(.bottom, Space.s)
                    }
                    HStack(spacing: Space.s) {
                        Text("Thinking").font(.hubCallout.weighted(.medium))
                        ForEach(["on", "off"], id: \.self) { choice in
                            Button(choice) {
                                Task {
                                    let args: [String: Any] = choice == "off"
                                        ? ["max_tokens": 0] : ["max_tokens": NSNull(), "display": "summarized"]
                                    if await model.claude("thinking", args: args) != nil {
                                        model.say("Thinking \(choice)")
                                    }
                                }
                            }
                            .buttonStyle(GhostButton())
                        }
                        Spacer()
                    }
                    .padding(.bottom, Space.xs)
                    ForEach(Array(models.enumerated()), id: \.offset) { _, m in
                        let value = m["value"]?.text ?? ""
                        let chosen = !current.isEmpty && (current == value || current.contains(value))
                        Button {
                            Task {
                                if let r = await model.claude("set_model", args: ["model": value]) {
                                    current = r["model"]?.text ?? value
                                    model.say("Model: \(m["displayName"]?.text ?? value)")
                                }
                            }
                        } label: {
                            HStack(alignment: .top, spacing: Space.m) {
                                Image(systemName: chosen ? "largecircle.fill.circle" : "circle")
                                    .font(.hubRow)
                                    .foregroundStyle(chosen ? Palette.accent : Palette.inkFaint)
                                VStack(alignment: .leading, spacing: Space.xxs) {
                                    Text(m["displayName"]?.text ?? value).font(.hubHeading)
                                        .foregroundStyle(Palette.ink)
                                    Text(m["description"]?.text ?? "").font(.hubCaption)
                                        .foregroundStyle(Palette.inkMuted)
                                    if let levels = m["supportedEffortLevels"]?.arrayValue, !levels.isEmpty {
                                        HStack(spacing: Space.s) {
                                            Text("effort").font(.hubCaption).foregroundStyle(Palette.inkFaint)
                                            ForEach(levels.map(\.text), id: \.self) { level in
                                                Button(level) {
                                                    Task {
                                                        if await model.claude("update_settings",
                                                                              args: ["source": "localSettings",
                                                                                     "settings": ["effort": level]]) != nil {
                                                            model.say("Effort: \(level)")
                                                        }
                                                    }
                                                }
                                                .buttonStyle(GhostButton())
                                            }
                                        }
                                    }
                                }
                                Spacer()
                            }
                            .tile()
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("models") {
                models = r["models"]?.arrayValue ?? []
                current = r["model"]?.text ?? ""
                account = r["account"] ?? .null
            } else {
                error = model.chatError
            }
            loading = false
        }
    }
}

/// The permission mode of the thread's session, in the composer row: the
/// terminal's Shift+Tab cycle as a menu.
struct PermissionModeMenu: View {
    @EnvironmentObject var model: AppModel
    @State private var mode = ""

    private static let modes: [(String, String, String)] = [
        ("default", "Ask", "Every edit and command is a card"),
        ("acceptEdits", "Accept edits", "Edits go through; commands still ask"),
        ("plan", "Plan", "Read-only: it plans, you approve"),
        ("bypassPermissions", "Bypass", "Nothing asks"),
    ]

    var body: some View {
        Menu {
            ForEach(Self.modes, id: \.0) { m in
                Button {
                    Task {
                        if let r = await model.claude("permission_mode", args: ["mode": m.0]) {
                            mode = r["permission_mode"]?.text ?? m.0
                            model.say("Permissions: \(m.1)")
                        }
                    }
                } label: {
                    Text((mode == m.0 ? "✓ " : "") + m.1 + " — " + m.2)
                }
            }
        } label: {
            HStack(spacing: Space.xs) {
                Image(systemName: mode == "plan" ? "list.bullet.clipboard" : "hand.raised").font(.hubIconSmall)
                Text(Self.modes.first { $0.0 == mode }?.1 ?? "Permissions")
                Image(systemName: "chevron.down").font(.hubGlyph)
            }
            .foregroundStyle(Palette.inkMuted)
            .pill()
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .tint(Palette.inkMuted)
        .help("How Claude Code asks in this thread (the terminal's Shift+Tab)")
        .task(id: model.conversationID) {
            if let r = try? await model.client.claude("status", conversation: model.conversationID,
                                                      cwd: model.lastRepo, backend: model.preferredBackend, timeout: 20) {
                mode = r["permission_mode"]?.text ?? ""
            }
        }
    }
}

// ---- /permissions --------------------------------------------------------------

struct PermissionsPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var rules: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.m) {
                    if rows.isEmpty {
                        Text("No rules yet. “Always allow” on a permission card adds one.")
                            .font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    }
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, r in
                        let behavior: String = r["behavior"]?.text ?? r["type"]?.text ?? ""
                        let tool: String = r["toolName"]?.text ?? r["tool"]?.text ?? ""
                        let content: String = r["ruleContent"]?.text ?? r["content"]?.text ?? ""
                        let source: String = r["source"]?.text ?? ""
                        HStack(spacing: Space.m) {
                            Text(behavior)
                                .font(.hubLabel)
                                .foregroundStyle(color(behavior))
                                .frame(width: 48, alignment: .leading)
                            Text(tool + " " + content)
                                .font(.hubMono)
                            Spacer()
                            Text(source).font(.hubCaption).foregroundStyle(Palette.inkFaint)
                        }
                        .tile()
                    }
                    if rows.isEmpty {
                        Text(JSONPretty(rules).text).font(.hubMonoSmall)
                            .foregroundStyle(Palette.inkFaint).textSelection(.enabled)
                            .padding(.top, Space.s)
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("rules") { rules = r["rules"] ?? .null } else { error = model.chatError }
            loading = false
        }
    }

    /// Rules however the build lists them: flat, or grouped by behaviour.
    private var rows: [JSONValue] {
        let state = rules["state"] ?? rules
        if let flat = state["rules"]?.arrayValue, !flat.isEmpty { return flat }
        var out: [JSONValue] = []
        for (key, v) in state.objectValue {
            for item in v.arrayValue {
                var o = item.objectValue
                if o["behavior"] == nil { o["behavior"] = .string(key) }
                out.append(.object(o))
            }
        }
        return out
    }

    private func color(_ behavior: String) -> Color {
        behavior.hasPrefix("allow") ? Palette.ok : behavior.hasPrefix("deny") ? Palette.danger : Palette.warn
    }
}

// ---- /usage ----------------------------------------------------------------------

struct UsagePanel: View {
    @EnvironmentObject var model: AppModel
    @State private var usage: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.l) {
                    if let plan = usage["subscription_type"]?.stringValue {
                        Text("Plan: \(plan)").font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    }
                    let limits = usage["rate_limits"]?.objectValue ?? [:]
                    ForEach(["five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus", "seven_day_oauth_apps"], id: \.self) { key in
                        if let w = limits[key], case .object = w {
                            window(title: key.replacingOccurrences(of: "_", with: " "), w)
                        }
                    }
                    ForEach(Array((limits["model_scoped"]?.arrayValue ?? []).enumerated()), id: \.offset) { _, w in
                        window(title: w["display_name"]?.text ?? "model", w)
                    }
                    if let extra = limits["extra_usage"], extra["is_enabled"]?.boolValue == true {
                        window(title: "extra usage", extra)
                    }
                    let session = usage["session"]?.objectValue ?? [:]
                    if !session.isEmpty {
                        Hairline()
                        SectionLabel(text: "This session")
                        let added: Int = session["total_lines_added"]?.intValue ?? 0
                        let removed: Int = session["total_lines_removed"]?.intValue ?? 0
                        let minutes: Int = (session["total_duration_ms"]?.intValue ?? 0) / 60000
                        HStack(spacing: Space.l) {
                            stat("cost", String(format: "$%.2f", session["total_cost_usd"]?.doubleValue ?? 0))
                            stat("lines", "+\(added) −\(removed)")
                            stat("time", "\(minutes) min")
                        }
                        ForEach(Array((session["model_usage"]?.objectValue ?? [:]).keys.sorted()), id: \.self) { m in
                            let u: JSONValue = session["model_usage"]?[m] ?? .null
                            let inTok: String = ContextUse.k(u["inputTokens"]?.intValue ?? 0)
                            let outTok: String = ContextUse.k(u["outputTokens"]?.intValue ?? 0)
                            let cached: String = ContextUse.k(u["cacheReadInputTokens"]?.intValue ?? 0)
                            let line: String = "\(m): \(inTok) in · \(outTok) out · \(cached) cached"
                            Text(line)
                                .font(.hubMonoSmall).foregroundStyle(Palette.inkMuted)
                        }
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("usage", timeout: 60) { usage = r["usage"] ?? .null } else { error = model.chatError }
            loading = false
        }
    }

    private func window(title: String, _ w: JSONValue) -> some View {
        let used = w["utilization"]?.doubleValue ?? 0
        let fraction = used > 1 ? used / 100 : used
        return VStack(alignment: .leading, spacing: Space.xs) {
            HStack {
                Text(title).font(.hubCallout.weighted(.medium))
                Spacer()
                Text(String(format: "%.0f%%", fraction * 100)).font(.hubMonoSmall)
                    .foregroundStyle(fraction > 0.85 ? Palette.danger : Palette.inkMuted)
                if let reset = w["resets_at"]?.stringValue, let date = ISO8601DateFormatter().date(from: reset) {
                    Text("resets " + date.formatted(.relative(presentation: .named)))
                        .font(.hubCaption).foregroundStyle(Palette.inkFaint)
                }
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Palette.fill)
                    Capsule().fill(fraction > 0.85 ? Palette.danger : Palette.accent)
                        .frame(width: max(2, geo.size.width * min(1, fraction)))
                }
            }
            .frame(height: 6)
        }
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            Text(label).font(.hubCaption).foregroundStyle(Palette.inkFaint)
            Text(value).font(.hubMono.weighted(.medium))
        }
    }
}

// ---- /context ----------------------------------------------------------------------

struct ContextPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var ctx: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    private static let swatches: [String: Color] = [
        "blue": Color.blue, "green": Palette.ok, "yellow": Palette.warn, "red": Palette.danger,
        "magenta": Color.purple, "cyan": Color.teal, "gray": Palette.inkFaint, "grey": Palette.inkFaint,
    ]

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.l) {
                    let total: Int = ctx["totalTokens"]?.intValue ?? 0
                    let cap: Int = ctx["maxTokens"]?.intValue ?? 0
                    let pct: String = String(format: "%.0f%%", ctx["percentage"]?.doubleValue ?? 0)
                    HStack {
                        Text(ctx["model"]?.text ?? "").font(.hubCallout.weighted(.medium))
                        Spacer()
                        let headline: String = "\(ContextUse.k(total)) of \(ContextUse.k(cap)) · \(pct)"
                        Text(headline)
                            .font(.hubMonoSmall).foregroundStyle(Palette.inkMuted)
                    }
                    let cats = ctx["categories"]?.arrayValue ?? []
                    GeometryReader { geo in
                        HStack(spacing: 1) {
                            ForEach(Array(cats.enumerated()), id: \.offset) { _, c in
                                let t = c["tokens"]?.doubleValue ?? 0
                                if cap > 0, t > 0 {
                                    Rectangle().fill(swatch(c))
                                        .frame(width: geo.size.width * t / Double(cap))
                                }
                            }
                            Spacer(minLength: 0)
                        }
                        .background(Palette.fill)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                    .frame(height: 12)
                    ForEach(Array(cats.enumerated()), id: \.offset) { _, c in
                        if c["kind"]?.text != "free" {
                            HStack(spacing: Space.s) {
                                RoundedRectangle(cornerRadius: 2).fill(swatch(c)).frame(width: 10, height: 10)
                                Text(c["name"]?.text ?? "").font(.hubCallout)
                                Spacer()
                                Text(ContextUse.k(c["tokens"]?.intValue ?? 0)).font(.hubMonoSmall)
                                    .foregroundStyle(Palette.inkMuted)
                            }
                        }
                    }
                    list("Memory files", ctx["memoryFiles"]?.arrayValue ?? [], name: "path")
                    list("MCP tools", ctx["mcpTools"]?.arrayValue ?? [], name: "name")
                    list("Agents", ctx["agents"]?.arrayValue ?? [], name: "agentType")
                    if let s = ctx["skills"], let n = s["totalSkills"]?.intValue {
                        let skillTok: String = ContextUse.k(s["tokens"]?.intValue ?? 0)
                        Text("\(n) skills · " + skillTok + " tokens")
                            .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("context", args: ["detail": "full"], timeout: 60) {
                ctx = r["context"] ?? .null
            } else { error = model.chatError }
            loading = false
        }
    }

    private func swatch(_ c: JSONValue) -> Color {
        let name = (c["color"]?.text ?? "").lowercased()
        if c["kind"]?.text == "free" { return Palette.fill }
        return Self.swatches[name] ?? Palette.accent.opacity(0.7)
    }

    private func list(_ title: String, _ items: [JSONValue], name: String) -> some View {
        Group {
            if !items.isEmpty {
                VStack(alignment: .leading, spacing: Space.xs) {
                    SectionLabel(text: title).padding(.top, Space.s)
                    ForEach(Array(items.enumerated()), id: \.offset) { _, it in
                        HStack {
                            Text(it[name]?.text ?? "").font(.hubMonoSmall)
                                .lineLimit(1).truncationMode(.middle)
                            if let s = it["serverName"]?.stringValue {
                                Text(s).font(.hubCaption).foregroundStyle(Palette.inkFaint)
                            }
                            Spacer()
                            Text(ContextUse.k(it["tokens"]?.intValue ?? 0)).font(.hubMonoSmall)
                                .foregroundStyle(Palette.inkMuted)
                        }
                    }
                }
            }
        }
    }
}

// ---- /rewind ---------------------------------------------------------------------------

struct RewindPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var working = 0
    @State private var result = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.m) {
                Text("Put the files back as they were before an answer. The thread stays; "
                     + "only the folder changes. Needs file checkpointing on in Claude Code's settings.")
                    .font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
                let turns = model.turns.filter { $0.role == "assistant" && $0.checkpoint != nil }
                if model.usesCodex {
                    HStack {
                        Text("Codex rolls the thread back whole turns (thread/rollback).")
                            .font(.hubCallout).foregroundStyle(Palette.inkMuted)
                        Spacer()
                        Button("Roll back the last turn") {
                            Task {
                                if let r = await model.claude("rewind", args: ["turns": 1], timeout: 120) {
                                    result = JSONPretty(r["rewind"] ?? .null).text
                                    model.say("Rolled back one turn")
                                }
                            }
                        }
                        .buttonStyle(GhostButton())
                    }
                } else if turns.isEmpty {
                    Text("No checkpoints in this thread yet — they're kept from the next Claude Code answer on.")
                        .font(.hubCallout).foregroundStyle(Palette.inkFaint)
                }
                ForEach(turns) { turn in
                    HStack(alignment: .top, spacing: Space.m) {
                        Text(String(turn.content.prefix(140)).replacingOccurrences(of: "\n", with: " "))
                            .font(.hubCallout).lineLimit(2)
                        Spacer()
                        if working == turn.id {
                            ProgressView().controlSize(.small)
                        } else {
                            Button("Rewind to before") { Task { await rewind(turn) } }
                                .buttonStyle(GhostButton())
                        }
                    }
                    .tile()
                }
                if !result.isEmpty {
                    Text(result).font(.hubMonoSmall).foregroundStyle(Palette.inkMuted)
                        .textSelection(.enabled)
                }
            }
            .padding(.all, Space.l)
        }
    }

    private func rewind(_ turn: Turn) async {
        working = turn.id
        defer { working = 0 }
        if let r = await model.claude("rewind", args: ["turn": turn.id], timeout: 120) {
            result = JSONPretty(r["rewind"] ?? .null).text
            model.say(rewindWord(r))
        }
    }
}

// ---- /hooks, /agents ----------------------------------------------------------------

struct HooksPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var hooks: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.s) {
                    let list = hooks["hooks"]?.arrayValue ?? []
                    if list.isEmpty {
                        Text("No hooks configured.").font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    }
                    ForEach(Array(list.enumerated()), id: \.offset) { _, h in
                        VStack(alignment: .leading, spacing: Space.xs) {
                            HStack {
                                Text(h["event"]?.text ?? "").font(.hubCallout.weighted(.medium))
                                if let m = h["matcher"]?.stringValue, !m.isEmpty {
                                    Text(m).font(.hubMonoSmall).foregroundStyle(Palette.inkMuted)
                                }
                                Spacer()
                                Text(h["sourceLabel"]?.text ?? h["source"]?.text ?? "")
                                    .font(.hubCaption).foregroundStyle(Palette.inkFaint)
                            }
                            Text(h["displayText"]?.text ?? h["commandText"]?.text ?? "")
                                .font(.hubMonoSmall).foregroundStyle(Palette.inkMuted)
                                .lineLimit(3)
                        }
                        .tile()
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("hooks") { hooks = r["hooks"] ?? .null } else { error = model.chatError }
            loading = false
        }
    }
}

struct AgentsPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var agents: [JSONValue] = []
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.s) {
                    if agents.isEmpty {
                        Text("No custom agents. They live in .claude/agents/ of a project or ~/.claude/agents/.")
                            .font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    }
                    ForEach(Array(agents.enumerated()), id: \.offset) { _, a in
                        VStack(alignment: .leading, spacing: Space.xs) {
                            HStack {
                                Text(a["name"]?.text ?? "").font(.hubCallout.weighted(.medium))
                                Spacer()
                                Text(a["source"]?.text ?? "").font(.hubCaption).foregroundStyle(Palette.inkFaint)
                            }
                            Text(a["description"]?.text ?? "").font(.hubCaption).foregroundStyle(Palette.inkMuted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .tile()
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("agents") { agents = r["agents"]?.arrayValue ?? [] } else { error = model.chatError }
            loading = false
        }
    }
}

// ---- cards: an MCP server asking, a dialog ---------------------------------------------

/// An MCP server asking you something through Claude Code: a small form
/// from its schema, or a link to visit (its own sign-in, say).
struct ElicitationCard: View {
    @EnvironmentObject var model: AppModel
    let prompt: PendingPrompt
    @State private var values: [String: String] = [:]

    private var fields: [(String, JSONValue)] {
        let props = prompt.schema["properties"]?.objectValue ?? [:]
        return props.keys.sorted().map { ($0, props[$0] ?? .null) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(spacing: Space.s) {
                Image(systemName: "bubble.left.and.text.bubble.right").font(.hubCallout).foregroundStyle(Palette.accent)
                Text(prompt.title.isEmpty ? "\(prompt.server) is asking" : prompt.title)
                    .font(.hubHeading)
            }
            if !prompt.message.isEmpty {
                Text(prompt.message).font(.hubCallout).fixedSize(horizontal: false, vertical: true)
            }
            if prompt.mode == "url", let url = URL(string: prompt.url) {
                Button {
                    NSWorkspace.shared.open(url)
                } label: {
                    Label(prompt.url, systemImage: "safari").font(.hubMonoSmall)
                        .lineLimit(1).truncationMode(.middle)
                }
                .buttonStyle(GhostButton())
            } else {
                ForEach(fields, id: \.0) { pair in
                    let key = pair.0
                    let spec = pair.1
                    let title = spec["title"]?.stringValue ?? key
                    if let options = spec["enum"]?.arrayValue, !options.isEmpty {
                        Picker(title, selection: Binding(get: { values[key] ?? "" }, set: { values[key] = $0 })) {
                            Text("—").tag("")
                            ForEach(options.map(\.text), id: \.self) { Text($0).tag($0) }
                        }
                        .pickerStyle(.menu)
                    } else if spec["type"]?.stringValue == "boolean" {
                        Toggle(title, isOn: Binding(get: { values[key] == "true" }, set: { values[key] = $0 ? "true" : "false" }))
                            .toggleStyle(.checkbox)
                    } else {
                        TextField(spec["description"]?.stringValue ?? title,
                                  text: Binding(get: { values[key] ?? "" }, set: { values[key] = $0 }))
                            .textFieldStyle(.roundedBorder)
                    }
                }
            }
            HStack(spacing: Space.s) {
                Spacer()
                Button("Decline") { model.answer(prompt, with: ["action": "decline"]) }
                    .buttonStyle(GhostButton())
                Button(prompt.mode == "url" ? "Done" : "Send") {
                    model.answer(prompt, with: ["action": "accept", "content": content()])
                }
                .buttonStyle(AccentButton())
                .keyboardShortcut(.defaultAction)
            }
        }
        .card(tone: Palette.accent)
    }

    /// Typed the way the schema says, so a number isn't sent as text.
    private func content() -> [String: Any] {
        var out: [String: Any] = [:]
        for (key, spec) in fields {
            guard let raw = values[key], !raw.isEmpty else { continue }
            switch spec["type"]?.stringValue ?? "string" {
            case "integer": out[key] = Int(raw) ?? raw
            case "number": out[key] = Double(raw) ?? raw
            case "boolean": out[key] = raw == "true"
            default: out[key] = raw
            }
        }
        return out
    }
}

/// A dialog of a kind eki has no special drawing for: what it carries, and
/// a way to dismiss it.
struct DialogCard: View {
    @EnvironmentObject var model: AppModel
    let prompt: PendingPrompt

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text("Claude Code: \(prompt.dialog)").font(.hubHeading)
            Text(JSONPretty(prompt.payload).text)
                .font(.hubMonoSmall).foregroundStyle(Palette.inkMuted)
                .textSelection(.enabled)
            HStack {
                Spacer()
                Button("Dismiss") { model.answer(prompt, with: ["action": "cancel"]) }
                    .buttonStyle(GhostButton())
            }
        }
        .card()
    }
}

/// The model thinking, greyed, above the tool lines.
struct ThinkingLines: View {
    let text: String

    var body: some View {
        Text(text.suffix(600))
            .font(.hubCallout)
            .foregroundStyle(Palette.inkFaint)
            .italic()
            .lineLimit(6)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// JSON, indented, for the panels that show a reply as it came.
struct JSONPretty {
    let text: String

    init(_ v: JSONValue) {
        if case .null = v { text = ""; return }
        if let data = try? JSONSerialization.data(withJSONObject: v.any, options: [.prettyPrinted, .sortedKeys]),
           let s = String(data: data, encoding: .utf8) {
            text = s
        } else {
            text = v.text
        }
    }
}

extension Turn {
    /// The id Claude Code's /rewind needs to undo this answer's edits.
    var checkpoint: String? {
        guard let meta, let data = meta.data(using: .utf8),
              let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return o["checkpoint"] as? String
    }
}

// ---- /tasks, /status, /config, /memory ---------------------------------------------

struct TasksPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var tasks: [JSONValue] = []
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.s) {
                    if tasks.isEmpty {
                        Text("Nothing running in the background.").font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    }
                    ForEach(Array(tasks.enumerated()), id: \.offset) { _, t in
                        let id: String = t["task_id"]?.text ?? t["id"]?.text ?? ""
                        let what: String = t["description"]?.text ?? t["type"]?.text ?? id
                        let status: String = t["status"]?.text ?? ""
                        HStack(spacing: Space.m) {
                            Dot(color: status == "running" || status.isEmpty ? Palette.accent : Palette.inkFaint, size: 7,
                                pulsing: status == "running")
                            VStack(alignment: .leading, spacing: Space.xxs) {
                                Text(what).font(.hubCallout.weighted(.medium)).lineLimit(2)
                                Text(status + (id.isEmpty ? "" : " · " + id)).font(.hubCaption).foregroundStyle(Palette.inkFaint)
                            }
                            Spacer()
                            if !id.isEmpty {
                                Button("Stop") {
                                    Task {
                                        if await model.claude("stop_task", args: ["task_id": id]) != nil { await load() }
                                    }
                                }
                                .buttonStyle(GhostButton())
                            }
                        }
                        .tile()
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task { await load() }
    }

    private func load() async {
        if let r = await model.claude("background") {
            let reply: JSONValue = r["tasks"] ?? .null
            tasks = reply["tasks"]?.arrayValue ?? reply["background_tasks"]?.arrayValue ?? reply.arrayValue
        } else { error = model.chatError }
        loading = false
    }
}

struct StatusPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var status: JSONValue = .null
    @State private var account: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.m) {
                    if model.usesClaude { row("Claude Code", account["version"]?.text ?? "") }
                    row("Account", (account["account"]?["email"]?.text ?? "") + " · " + (account["account"]?["subscriptionType"]?.text ?? ""))
                    row("Model", status["model"]?.text ?? "")
                    row("Permission mode", status["permission_mode"]?.text ?? "")
                    row(model.usesCodex ? "Thread" : "Session", status["session"]?.text ?? "")
                    row("Context window", ContextUse.k(status["context_window"]?.intValue ?? 0))
                    if model.usesClaude {
                        row("Output style", account["output_style"]?.text ?? "")
                        row("Tools", (account["tools"]?.arrayValue.map(\.text) ?? []).joined(separator: ", "))
                        row("Build capabilities", (account["capabilities"]?.arrayValue.map(\.text) ?? []).joined(separator: ", "))
                    }
                    Hairline()
                    SectionLabel(text: "Panels")
                    ForEach(ClaudePanel.panels(codex: model.usesCodex)) { p in
                        Button("/" + p.rawValue + "  —  " + p.title) { model.claudePanel = p }
                            .buttonStyle(GhostButton())
                    }
                    Text("Sign-in, themes and terminal setup belong to the terminal; sign in once "
                         + "there and eki runs \(model.agentName) as you.")
                        .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let s = await model.claude("status", timeout: 30) { status = s } else { error = model.chatError }
            if let a = await model.claude("account", timeout: 30) { account = a }
            loading = false
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: Space.m) {
            Text(label).font(.hubCallout).foregroundStyle(Palette.inkMuted).frame(width: 130, alignment: .leading)
            Text(value.isEmpty ? "—" : value).font(.hubMonoSmall).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct ConfigPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var settings: JSONValue = .null
    @State private var styles: [String] = []
    @State private var current = ""
    @State private var efforts: [String] = []
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.m) {
                    if !efforts.isEmpty {
                        HStack(spacing: Space.s) {
                            Text("Effort").font(.hubCallout.weighted(.medium))
                            ForEach(efforts, id: \.self) { level in
                                Button(level) {
                                    Task {
                                        if await model.claude("effort", args: ["effort": level]) != nil {
                                            model.say("Effort: \(level)")
                                        }
                                    }
                                }
                                .buttonStyle(GhostButton())
                            }
                        }
                    }
                    if model.usesClaude {
                        HStack(spacing: Space.s) {
                            Text("Thinking").font(.hubCallout.weighted(.medium))
                            ForEach(["on", "off"], id: \.self) { choice in
                                Button(choice) {
                                    Task {
                                        let args: [String: Any] = choice == "off"
                                            ? ["max_tokens": 0] : ["max_tokens": NSNull(), "display": "summarized"]
                                        if await model.claude("thinking", args: args) != nil {
                                            model.say("Thinking \(choice)")
                                        }
                                    }
                                }
                                .buttonStyle(GhostButton())
                            }
                        }
                    }
                    if !styles.isEmpty {
                        HStack(spacing: Space.s) {
                            Text("Output style").font(.hubCallout.weighted(.medium))
                            Picker("", selection: $current) {
                                ForEach(styles, id: \.self) { Text($0).tag($0) }
                            }
                            .labelsHidden()
                            .frame(width: 200)
                            .onChange(of: current) { _, now in
                                Task {
                                    if await model.claude("output_style", args: ["style": now]) != nil {
                                        model.say("Output style: \(now)")
                                    }
                                }
                            }
                        }
                    }
                    Text("As the session sees them, after every settings file and flag. Edit "
                         + "~/.claude/settings.json or a project's .claude/settings.json to change them.")
                        .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(JSONPretty(settings).text)
                        .font(.hubMonoSmall).textSelection(.enabled)
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("settings") { settings = r["settings"] ?? .null } else { error = model.chatError }
            if let a = await model.claude("account", timeout: 30) {
                styles = a["output_styles"]?.arrayValue.map(\.text) ?? []
                current = a["output_style"]?.text ?? ""
            }
            if let m = await model.claude("models", timeout: 30) {
                // the levels the current model (or the first with any) supports
                let list = m["models"]?.arrayValue ?? []
                let now = m["model"]?.text ?? ""
                let pick = list.first { now.contains($0["value"]?.text ?? "\u{0}") && !($0["supportedEffortLevels"]?.arrayValue.isEmpty ?? true) }
                    ?? list.first { !($0["supportedEffortLevels"]?.arrayValue.isEmpty ?? true) }
                efforts = pick?["supportedEffortLevels"]?.arrayValue.map(\.text) ?? []
            }
            loading = false
        }
    }
}

struct MemoryPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var memory: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.s) {
                    let files = memory["memoryFiles"]?.arrayValue ?? memory["files"]?.arrayValue ?? []
                    if files.isEmpty {
                        Text("No CLAUDE.md or memory files loaded for this folder.")
                            .font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    }
                    ForEach(Array(files.enumerated()), id: \.offset) { _, f in
                        let path: String = f["path"]?.text ?? f["file"]?.text ?? f.text
                        HStack {
                            Text(path).font(.hubMonoSmall).lineLimit(1).truncationMode(.middle)
                            Spacer()
                            if let t = f["type"]?.stringValue {
                                Text(t).font(.hubCaption).foregroundStyle(Palette.inkFaint)
                            }
                            Button("Open") { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                                .buttonStyle(GhostButton())
                        }
                        .tile()
                    }
                    if files.isEmpty, case .object = memory {
                        Text(JSONPretty(memory).text).font(.hubMonoSmall)
                            .foregroundStyle(Palette.inkMuted).textSelection(.enabled)
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("memory") { memory = r["memory"] ?? .null } else { error = model.chatError }
            loading = false
        }
    }
}

// ---- /skills ----------------------------------------------------------------------

/// Skills: eki's one store first — what each backend gets, on or off per
/// backend, an editor, import — then what Claude Code itself sees in this
/// folder (plugins, project skills, the synced ones), each usable from here.
/// Typed as /skills in any thread, Claude, Codex or a local model.
struct SkillsPanel: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var store: [JSONValue] = []
    @State private var loose: [JSONValue] = []
    @State private var program: [JSONValue] = []
    @State private var programError = ""
    @State private var programLoading = true
    @State private var filter = ""
    @State private var loading = true
    @State private var error = ""
    @State private var editing: SkillDraft?

    private static let backends: [(String, String)] = [("claude", "Claude"), ("codex", "Codex"),
                                                      ("gemini", "Gemini"), ("local", "Other models")]

    private func matches(_ s: JSONValue) -> Bool {
        let q = filter.lowercased()
        return q.isEmpty || (s["name"]?.text ?? "").lowercased().contains(q)
            || (s["description"]?.text ?? "").lowercased().contains(q)
    }

    /// The program's own list, without the ones that are eki's links.
    private var programOnly: [JSONValue] {
        let ours = Set(store.map { $0["name"]?.text ?? "" })
        return program.filter { !ours.contains($0["name"]?.text ?? "") && matches($0) }
    }

    var body: some View {
        PanelState(loading: loading, error: error) {
            VStack(spacing: 0) {
                HStack(spacing: Space.s) {
                    TextField("Filter skills", text: $filter).textFieldStyle(.roundedBorder)
                    Button("New skill…") { editing = SkillDraft(isNew: true) }.buttonStyle(GhostButton())
                    Button("Open folder") {
                        NSWorkspace.shared.open(URL(fileURLWithPath: NSString(string: "~/.eki/skills").expandingTildeInPath))
                    }
                    .buttonStyle(GhostButton())
                }
                .padding(.horizontal, Space.l).padding(.vertical, Space.m)
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.l) {
                        storeSection
                        if !loose.isEmpty { looseSection }
                        programSection
                    }
                    .padding(.horizontal, Space.l).padding(.bottom, Space.l)
                }
            }
        }
        .task { await load() }
        .sheet(item: $editing) { draft in
            SkillEditor(draft: draft) { await load() }.environmentObject(model)
        }
    }

    private func header(_ text: String) -> some View {
        SectionLabel(text: text)
    }

    private var storeSection: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            header("eki's skills — one copy, every backend")
            if store.isEmpty {
                Text("None yet. A skill is a folder with a SKILL.md; eki links each one into "
                     + "~/.claude/skills and ~/.agents/skills, and hands it to local models itself.")
                    .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(store.filter(matches).enumerated()), id: \.offset) { _, s in
                storeRow(s)
            }
        }
    }

    private func storeRow(_ s: JSONValue) -> some View {
        let name: String = s["name"]?.text ?? ""
        let folder: String = s["folder"]?.text ?? name
        let on: [String] = s["backends"]?.arrayValue.map(\.text) ?? []
        let enabled = s["enabled"]?.boolValue ?? true
        let views = s["views"]?.objectValue ?? [:]
        let conflicts = views.filter { $0.value.text == "conflict" }.map(\.key).sorted()
        let uses = s["uses"]?.intValue ?? 0
        return HStack(alignment: .top, spacing: Space.m) {
            Dot(color: enabled ? Palette.accent : Palette.inkFaint, size: 7).padding(.top, Space.xs)
            VStack(alignment: .leading, spacing: Space.xs) {
                HStack(spacing: Space.s) {
                    Text(name).font(.hubMono.weighted(.medium))
                    if s["origin"]?.text == "eki" {
                        Text("eki's own").font(.hubCaption).foregroundStyle(Palette.inkFaint)
                    }
                    if uses > 0 {
                        Text("used \(uses)×").font(.hubCaption).foregroundStyle(Palette.inkFaint)
                    }
                }
                Text(s["description"]?.text ?? "").font(.hubCaption).foregroundStyle(Palette.inkMuted)
                    .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: Space.m) {
                    ForEach(Self.backends, id: \.0) { b in
                        Toggle(b.1, isOn: Binding(
                            get: { on.contains(b.0) },
                            set: { v in Task { await toggle(folder, v, backend: b.0) } }))
                            .toggleStyle(.checkbox).font(.hubCaption)
                            .disabled(!enabled)
                    }
                }
                if !conflicts.isEmpty {
                    Text("Another skill named \(folder) already sits in "
                         + conflicts.map { ["claude": "~/.claude/skills", "gemini": "~/.gemini/skills"][$0]
                                           ?? "~/.agents/skills" }
                            .joined(separator: " and ") + " — eki left it alone")
                        .font(.hubCaption).foregroundStyle(Palette.warn)
                }
            }
            Spacer()
            Button("Use") { model.draftRequest = "/" + name + " "; dismiss() }.buttonStyle(GhostButton())
            Button("Edit") { Task { await edit(folder) } }.buttonStyle(GhostButton())
            Button(enabled ? "Turn off" : "Turn on") { Task { await toggle(folder, !enabled) } }
                .buttonStyle(GhostButton())
            Button("Remove") { Task { await remove(folder) } }.buttonStyle(GhostButton())
                .help("Out of the store and every backend. It stays in the store's git history.")
        }
        .tile()
    }

    private var looseSection: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack {
                header("Found in the programs' own folders")
                Spacer()
                Button("Import all") { Task { await importSkills([]) } }.buttonStyle(AccentButton())
                    .help("Move them into eki's store and link them back, so every backend has them")
            }
            ForEach(Array(loose.enumerated()), id: \.offset) { _, u in
                let folder: String = u["folder"]?.text ?? ""
                HStack(spacing: Space.m) {
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        Text(folder).font(.hubMono.weighted(.medium))
                        Text(u["path"]?.text ?? "").font(.hubMonoSmall)
                            .foregroundStyle(Palette.inkFaint).lineLimit(1).truncationMode(.middle)
                    }
                    Spacer()
                    if u["held"]?.boolValue == true {
                        Text("eki has one by this name").font(.hubCaption).foregroundStyle(Palette.inkFaint)
                    } else {
                        Button("Import") { Task { await importSkills([folder]) } }.buttonStyle(GhostButton())
                    }
                }
                .tile()
            }
        }
    }

    private var programSection: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack {
                header("Also in \(model.agentName) here — plugins, this project, synced")
                Spacer()
                Button("Reload") { Task { await loadProgram(reload: true) } }.buttonStyle(GhostButton())
                    .help("Re-read the skill folders (the terminal's /reload-skills)")
            }
            if programLoading {
                ProgressView().controlSize(.small)
            } else if !programError.isEmpty {
                Text(programError).font(.hubCaption).foregroundStyle(Palette.inkFaint)
            } else if programOnly.isEmpty {
                Text("Nothing beyond eki's skills.").font(.hubCaption).foregroundStyle(Palette.inkMuted)
            }
            ForEach(Array(programOnly.enumerated()), id: \.offset) { _, s in
                let name: String = s["name"]?.text ?? ""
                let hint: String = s["argumentHint"]?.text ?? ""
                HStack(alignment: .top, spacing: Space.m) {
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        HStack(spacing: Space.s) {
                            Text("/" + name).font(.hubMono.weighted(.medium))
                            if !hint.isEmpty {
                                Text(hint).font(.hubMonoSmall).foregroundStyle(Palette.inkFaint)
                            }
                        }
                        Text(s["description"]?.text ?? "").font(.hubCaption).foregroundStyle(Palette.inkMuted)
                            .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Button("Use") {
                        model.draftRequest = "/" + name + (hint.isEmpty ? "" : " ")
                        dismiss()
                    }
                    .buttonStyle(GhostButton())
                }
                .tile()
            }
        }
    }

    // ---- actions ----

    private func apply(_ reply: JSONValue) {
        if let s = reply["skills"]?.arrayValue { store = s }
        loose = reply["unmanaged"]?.arrayValue ?? loose
    }

    private func load() async {
        do {
            apply(try await model.client.skills())
        } catch {
            self.error = "eki didn't answer: \(error.localizedDescription)"
        }
        loading = false
        await loadProgram()
    }

    private func loadProgram(reload: Bool = false) async {
        programLoading = true
        defer { programLoading = false }
        do {
            let r = try await model.client.claude(reload ? "reload_skills" : "skills",
                                                  conversation: model.conversationID,
                                                  cwd: model.lastRepo, backend: model.preferredBackend, timeout: 60)
            let reply: JSONValue = r["skills"] ?? .null
            program = reply["skills"]?.arrayValue ?? reply.arrayValue
            programError = ""
        } catch {
            programError = "\(model.agentName) isn't reachable, so only eki's skills are shown."
        }
    }

    private func toggle(_ name: String, _ on: Bool, backend: String = "") async {
        do { apply(try await model.client.skillEnabled(name, enabled: on, backend: backend)) }
        catch { model.say("Couldn't change \(name): \(error.localizedDescription)") }
    }

    private func remove(_ name: String) async {
        do {
            apply(try await model.client.skillDelete(name))
            model.say("Removed \(name) — it's still in the store's history")
        } catch { model.say("Couldn't remove \(name): \(error.localizedDescription)") }
    }

    private func importSkills(_ names: [String]) async {
        do {
            let r = try await model.client.skillsImport(names)
            apply(r)
            let n = r["report"]?["imported"]?.arrayValue.count ?? 0
            model.say(n == 1 ? "Imported 1 skill" : "Imported \(n) skills")
        } catch { model.say("Import failed: \(error.localizedDescription)") }
    }

    private func edit(_ name: String) async {
        guard let r = try? await model.client.skill(name) else { return }
        editing = SkillDraft(isNew: false, name: name, text: r["text"]?.text ?? "")
    }
}

struct SkillDraft: Identifiable {
    let id = UUID()
    var isNew: Bool
    var name = ""
    var text = ""
}

/// A new skill (name, when to use it, instructions) or an existing SKILL.md as text.
struct SkillEditor: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let draft: SkillDraft
    let done: () async -> Void
    @State private var name = ""
    @State private var description = ""
    @State private var text = ""
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text(draft.isNew ? "New skill" : draft.name).font(.hubTitle)
            if draft.isNew {
                TextField("Name (letters, digits, - _ .)", text: $name).textFieldStyle(.roundedBorder)
                TextField("When to use it — this is what a model reads to decide", text: $description, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...4)
                Text("Instructions").font(.hubCaption.weighted(.semibold)).foregroundStyle(Palette.inkFaint)
            } else {
                Text("SKILL.md — saving commits it to the store and every backend sees it at once")
                    .font(.hubCaption).foregroundStyle(Palette.inkFaint)
            }
            TextEditor(text: $text)
                .font(.hubMonoSmall)
                .scrollContentBackground(.hidden)
                .padding(.all, Space.s)
                .background(Palette.surface, in: RoundedRectangle(cornerRadius: Radius.small))
                .frame(minHeight: 260)
            if !error.isEmpty {
                Text(error).font(.hubCallout).foregroundStyle(Palette.danger)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
                Button("Save") { Task { await save() } }.buttonStyle(AccentButton())
                    .disabled(draft.isNew ? (name.isEmpty || description.isEmpty) : text.isEmpty)
                    .keyboardShortcut("s", modifiers: .command)
            }
        }
        .padding(.all, Space.xl)
        .frame(width: 640, height: draft.isNew ? 520 : 560)
        .background(Palette.canvas)
        .onAppear { text = draft.text; name = draft.name }
    }

    private func save() async {
        do {
            if draft.isNew {
                _ = try await model.client.skillPut(name, body: ["description": description, "body": text])
            } else {
                _ = try await model.client.skillPut(draft.name, body: ["text": text])
            }
            await done()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// ---- /plugins (Codex) ---------------------------------------------------------------

struct PluginsPanel: View {
    @EnvironmentObject var model: AppModel
    @State private var plugins: JSONValue = .null
    @State private var loading = true
    @State private var error = ""

    var body: some View {
        PanelState(loading: loading, error: error) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.s) {
                    let markets = plugins["marketplaces"]?.arrayValue ?? []
                    if markets.isEmpty {
                        Text("No plugin marketplaces.").font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    }
                    ForEach(Array(markets.enumerated()), id: \.offset) { _, m in
                        SectionLabel(text: (m["name"]?.text ?? "")).padding(.top, Space.s)
                        ForEach(Array((m["plugins"]?.arrayValue ?? []).enumerated()), id: \.offset) { _, p in
                            HStack {
                                VStack(alignment: .leading, spacing: Space.xxs) {
                                    Text(p["name"]?.text ?? p["id"]?.text ?? "").font(.hubCallout.weighted(.medium))
                                    Text(p["id"]?.text ?? "").font(.hubMonoSmall).foregroundStyle(Palette.inkFaint)
                                }
                                Spacer()
                                Text(p["localVersion"]?.text ?? p["version"]?.text ?? "").font(.hubCaption).foregroundStyle(Palette.inkMuted)
                            }
                            .tile()
                        }
                    }
                }
                .padding(.all, Space.l)
            }
        }
        .task {
            if let r = await model.claude("plugins", timeout: 60) { plugins = r["plugins"] ?? .null } else { error = model.chatError }
            loading = false
        }
    }
}
