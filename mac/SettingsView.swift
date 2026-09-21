// SPDX-License-Identifier: Apache-2.0
// Settings (⌘,): how eki looks, and what the menu bar shows.
import SwiftUI

struct SettingsView: View {
    var body: some View {
        TabView {
            GeneralSettings()
                .tabItem { Label("General", systemImage: "gearshape") }
            MenuBarSettings()
                .tabItem { Label("Menu Bar", systemImage: "menubar.rectangle") }
            RoutingSettings()
                .tabItem { Label("Routing", systemImage: "arrow.triangle.branch") }
        }
        .frame(width: 520, height: 430)
    }
}

struct GeneralSettings: View {
    @AppStorage(Pref.theme) private var theme: String = Theme.system.rawValue
    @AppStorage(Pref.accent) private var accent: String = Accent.terracotta.rawValue
    @EnvironmentObject var model: AppModel

    var body: some View {
        Form {
            Picker("Appearance", selection: $theme) {
                ForEach(Theme.allCases) { Text($0.title).tag($0.rawValue) }
            }
            .pickerStyle(.segmented)

            LabeledContent("Accent") {
                HStack(spacing: 10) {
                    ForEach(Accent.allCases) { option in
                        let (l, d) = option.hex
                        Button {
                            accent = option.rawValue
                        } label: {
                            Circle()
                                .fill(Color.adaptive(l, d))
                                .frame(width: 18, height: 18)
                                .overlay(Circle().strokeBorder(
                                    Color.primary.opacity(accent == option.rawValue ? 0.9 : 0),
                                    lineWidth: 2).padding(-3))
                        }
                        .buttonStyle(.plain)
                        .help(option.title)
                    }
                }
            }

            Section("Claude usage") {
                Toggle("Read Claude's limits from Claude Code's status line",
                       isOn: Binding(
                        get: { model.usage?.claude_bridge ?? false },
                        set: { on in Task { await model.setClaudeBridge(on) } }))
                    .toggleStyle(AccentSwitch())
                Text("Adds a status line to Claude Code that also records the 5-hour and "
                     + "weekly limits it reports. Any status line you already had keeps "
                     + "showing. eki never reads your Claude login.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

struct MenuBarSettings: View {
    @EnvironmentObject var model: AppModel
    @AppStorage(Pref.menuBarProviders) private var shownRaw: String = ""
    @AppStorage(Pref.meterStyle) private var style: String = MeterStyle.stacked.rawValue
    @AppStorage(Pref.meterColour) private var colour: String = MeterColour.mono.rawValue
    @State private var order: [String] = []
    @State private var shown: Set<String> = []

    private var available: [ProviderUsage] { model.usage?.providers ?? [] }

    var body: some View {
        Form {
            Section("Providers") {
                List {
                    ForEach(order, id: \.self) { key in
                        HStack {
                            Image(systemName: "line.3.horizontal")
                                .foregroundStyle(.tertiary)
                            Dot(color: Palette.backend(key), size: 7)
                            Text(label(key))
                            Spacer()
                            Toggle("", isOn: Binding(
                                get: { shown.contains(key) },
                                set: { on in
                                    if on { shown.insert(key) } else { shown.remove(key) }
                                    save()
                                }))
                                .labelsHidden()
                                .toggleStyle(AccentSwitch())
                                .controlSize(.small)
                        }
                    }
                    .onMove { from, to in
                        order.move(fromOffsets: from, toOffset: to)
                        save()
                    }
                }
                .frame(minHeight: 90)
                Text("Drag to reorder. Hidden providers still route, and still show in Usage.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Style") {
                Picker("Meters", selection: $style) {
                    ForEach(MeterStyle.allCases) { Text($0.title).tag($0.rawValue) }
                }
                Picker("Colour", selection: $colour) {
                    ForEach(MeterColour.allCases) { Text($0.title).tag($0.rawValue) }
                }
                LabeledContent("Preview") {
                    Image(nsImage: preview)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.primary.opacity(0.07),
                                    in: RoundedRectangle(cornerRadius: 5))
                }
            }
        }
        .formStyle(.grouped)
        .onAppear(perform: load)
        .onChange(of: model.usage) { load() }
    }

    private func label(_ key: String) -> String {
        available.first { $0.provider == key }?.label ?? key.capitalized
    }

    private func load() {
        let all = available.map(\.provider)
        let current = Pref.shown(from: all)
        order = current + all.filter { !current.contains($0) }
        shown = Set(current)
    }

    private func save() {
        let keys = order.filter(shown.contains)
        Pref.setShown(keys)
        shownRaw = keys.joined(separator: ",")
    }

    private var preview: NSImage {
        model.menuBarImage(style: MeterStyle(rawValue: style) ?? .stacked,
                           colour: MeterColour(rawValue: colour) ?? .mono)
    }
}


/// How eki decides what a request is, and where local models come from.
struct RoutingSettings: View {
    @EnvironmentObject var model: AppModel
    @State private var settings = HubSettings()
    @State private var saving = false

    private var localProviders: [ProviderDTO] {
        model.providers.filter { $0.runtime.port != nil }
    }

    var body: some View {
        Form {
            Section("Labelling") {
                Picker("Read requests with", selection: Binding(
                    get: { settings.router },
                    set: { settings.router = $0; save() })) {
                    Text("Rules").tag("rules")
                    Text("A small model").tag("model")
                }
                .pickerStyle(.segmented)
                Picker("Router model", selection: Binding(
                    get: { settings.router_model },
                    set: { settings.router_model = $0; save() })) {
                    Text("None").tag("")
                    ForEach(localProviders) { Text($0.label).tag($0.key) }
                }
                .disabled(localProviders.isEmpty)
                Text("Before routing, eki decides what a request is — a question, a "
                     + "translation, a change to your code — and how demanding it is. "
                     + "The rules cost nothing and never wait. A small local model can "
                     + "read the ones the rules get wrong, at about a quarter second a "
                     + "request; if it's asleep or slow, the rules answer instead.\n\n"
                     + "The router model is kept out of automatic routing, so it never "
                     + "ends up answering the questions it labels.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Permissions") {
                Picker("Commands and edits", selection: Binding(
                    get: { settings.permissions },
                    set: { settings.permissions = $0; save() })) {
                    Text("Run without asking").tag("auto")
                    Text("Ask me each time").tag("ask")
                }
                .pickerStyle(.segmented)
                Text("Claude Code and Codex can run commands and change files as they work. "
                     + "By default eki lets them, using their own skip-permissions modes, so "
                     + "a task runs through without a dozen prompts. “Ask me each time” turns "
                     + "every permission into a card in the thread — Allow, Always allow, Deny. "
                     + "Questions they ask you come through either way.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                LabeledContent("Claude system prompt") {
                    TextField("Added to Claude Code's own", text: Binding(
                        get: { settings.claude_system_prompt },
                        set: { settings.claude_system_prompt = $0 }), axis: .vertical)
                        .lineLimit(1...4)
                        .onSubmit(save)
                }
            }

            Section("Measuring") {
                Picker("Measure on its own", selection: Binding(
                    get: { settings.auto_measure },
                    set: { settings.auto_measure = $0; save() })) {
                    Text("Off").tag("off")
                    Text("Local models").tag("local")
                    Text("Everything").tag("all")
                }
                .pickerStyle(.segmented)
                Text("eki puts the same public test items — GSM8K, MATH, AIME, MBPP, "
                     + "HumanEval, TriviaQA, MMLU-Pro — to every provider, so a 4-bit "
                     + "local build and a frontier model land on one scale. Local models "
                     + "are measured when nothing else is running and cost only time. "
                     + "“Everything” also measures Claude, Codex and API providers, only "
                     + "while their windows are nearly idle; a full run is a real bite of "
                     + "a 5-hour window, once per provider.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Engines") {
                EnginesList()
                Text("The programs that serve models. eki fetches each one — a pinned, "
                     + "checksum-verified release into its own folder — the first time a model "
                     + "needs it. Nothing to install by hand; remove one here when no model uses it.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Local models") {
                LabeledContent("MLX Python") {
                    TextField("", text: Binding(
                        get: { settings.mlx_python },
                        set: { settings.mlx_python = $0 }))
                        .onSubmit(save)
                }
                LabeledContent("Model cache") {
                    TextField("", text: Binding(
                        get: { settings.hf_home },
                        set: { settings.hf_home = $0 }))
                        .onSubmit(save)
                }
                Text("An MLX Python of your own, if you'd rather eki used it than its own "
                     + "(leave it if not sure), and where downloaded weights live. Each model's "
                     + "context window is worked out from its own config and the memory beside "
                     + "its weights — see the Models pane.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .task {
            settings = (try? await model.client.settings()) ?? settings
            await model.refreshProviders()
        }
    }

    private func save() {
        guard !saving else { return }
        saving = true
        Task {
            settings = (try? await model.client.save(settings: settings)) ?? settings
            await model.refreshProviders()
            saving = false
        }
    }
}


/// Engines and whether eki has them; Get starts the fetch as a run you
/// can watch under Models, Remove is allowed once no model uses it.
struct EnginesList: View {
    @EnvironmentObject var model: AppModel
    @State private var engines: [EngineStatus] = []
    @State private var message = ""

    var body: some View {
        ForEach(engines) { e in
            LabeledContent {
                HStack(spacing: 8) {
                    Text(e.installed ? "ready" : "not fetched")
                        .font(.system(size: 11)).foregroundStyle(e.installed ? Palette.ok : Palette.inkFaint)
                    if e.installed {
                        Button("Remove") {
                            Task {
                                do { message = try await model.client.removeEngine(e.name) }
                                catch { message = error.localizedDescription }
                                await load()
                            }
                        }.buttonStyle(GhostButton())
                    } else {
                        Button("Get (\(e.size_mb) MB)") {
                            Task {
                                do {
                                    let run = try await model.client.installEngine(e.name)
                                    model.show(deploy: run)
                                } catch { message = error.localizedDescription }
                            }
                        }.buttonStyle(GhostButton())
                    }
                }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(e.title) \(e.version)")
                    Text((e.note ?? "").isEmpty ? e.source : (e.note ?? ""))
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .help(e.installed ? "At \(e.path)" : "From \(e.source)")
        }
        if !message.isEmpty {
            Text(message).font(.caption).foregroundStyle(.secondary)
        }
        Color.clear.frame(height: 0).task { await load() }
    }

    private func load() async {
        engines = (try? await model.client.engines()) ?? engines
    }
}
