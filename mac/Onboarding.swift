// First run.
//
// Three things decide whether hub is useful five minutes from now: it keeps
// running when the window is closed, it knows what you already have on this
// Mac, and it's reachable from a terminal. Each is one button, each says what
// it will do, and none of them is required to get started.
import ServiceManagement
import SwiftUI

enum Engine {
    /// The engine inside the app bundle, when this is a packaged build.
    static var bundled: URL? {
        let url = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/hub-engine")
        return FileManager.default.isExecutableFile(atPath: url.path) ? url : nil
    }

    static var cli: URL? {
        let url = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/hub-cli")
        return FileManager.default.isExecutableFile(atPath: url.path) ? url : nil
    }

    static var agent: SMAppService { SMAppService.agent(plistName: "local.hub.engine.plist") }

    static var plainAgent: URL {
        URL(fileURLWithPath: NSHomeDirectory()
            + "/Library/LaunchAgents/local.hub.engine.plist")
    }

    static var runsAtLogin: Bool {
        if bundled != nil, agent.status == .enabled { return true }
        return FileManager.default.fileExists(atPath: plainAgent.path)
    }

    /// Ask launchd to keep the engine alive. Returns nil, or what went wrong.
    static func enableLogin() -> String? {
        guard bundled != nil else {
            return "This build runs the engine from the source checkout — "
                 + "`hub agent install` sets up the login agent for it."
        }
        if agent.status == .enabled { return nil }
        do {
            try agent.register()
            return nil
        } catch {
            // SMAppService insists on a Developer ID signature. A build signed
            // ad-hoc — which is what you get building this yourself — falls
            // back to an ordinary login agent, which launchd is happy to load
            // from the user's own LaunchAgents folder.
            return installPlainAgent()
        }
    }

    private static func installPlainAgent() -> String? {
        guard let engine = bundled else { return "No bundled engine to run." }
        let plist: [String: Any] = [
            "Label": "local.hub.engine",
            "ProgramArguments": [engine.path],
            "RunAtLoad": true,
            "KeepAlive": true,
            "ThrottleInterval": 10,
            "ProcessType": "Interactive",
            "StandardOutPath": NSHomeDirectory() + "/.hub/engine.log",
            "StandardErrorPath": NSHomeDirectory() + "/.hub/engine.log",
            "EnvironmentVariables": [
                "PATH": NSHomeDirectory() + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:"
                      + "/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONUNBUFFERED": "1",
            ],
        ]
        do {
            try FileManager.default.createDirectory(
                at: plainAgent.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.createDirectory(
                atPath: NSHomeDirectory() + "/.hub", withIntermediateDirectories: true)
            let data = try PropertyListSerialization.data(fromPropertyList: plist,
                                                          format: .xml, options: 0)
            try data.write(to: plainAgent)
        } catch {
            return error.localizedDescription
        }
        for args in [["bootout", "gui/\(getuid())/local.hub.engine"],
                     ["bootstrap", "gui/\(getuid())", plainAgent.path]] {
            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            task.arguments = args
            try? task.run()
            task.waitUntilExit()
        }
        return nil
    }

    static func disableLogin() {
        try? agent.unregister()
        if FileManager.default.fileExists(atPath: plainAgent.path) {
            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            task.arguments = ["bootout", "gui/\(getuid())/local.hub.engine"]
            try? task.run()
            task.waitUntilExit()
            try? FileManager.default.removeItem(at: plainAgent)
        }
    }

    /// A `hub` command on the PATH, pointing back into the app.
    static func installCLI() -> String {
        guard let cli else { return "This build has no bundled CLI." }
        let dir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/bin")
        let link = dir.appendingPathComponent("hub")
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: link.path) {
                try FileManager.default.removeItem(at: link)
            }
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: cli)
        } catch {
            return error.localizedDescription
        }
        let onPath = (ProcessInfo.processInfo.environment["PATH"] ?? "")
            .split(separator: ":").contains { $0 == dir.path }
        return onPath ? "Installed as `hub`."
                      : "Installed at ~/.local/bin/hub — add that folder to your PATH."
    }

    static var cliInstalled: Bool {
        FileManager.default.fileExists(atPath: NSHomeDirectory() + "/.local/bin/hub")
    }
}

struct OnboardingSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @AppStorage(Pref.onboarded) private var onboarded: Bool = false

    @State private var found: [ProviderTemplate] = []
    @State private var adding: Set<String> = []
    @State private var added: Set<String> = []
    @State private var loginOn = Engine.runsAtLogin
    @State private var cliNote = ""
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Welcome to hub").font(.system(size: 22, weight: .semibold))
                Text("One place to ask, whatever answers: models on this Mac, the CLIs "
                     + "you already pay for, APIs you bring a key for. hub picks the "
                     + "cheapest one that can actually do the job.")
                    .font(.system(size: 13))
                    .foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            step(number: 1, title: "Keep it running",
                 detail: "Your requests run in a small engine outside the window, so "
                       + "closing the app — or rebooting — doesn't stop the work.") {
                if loginOn {
                    Tag(text: "on at login", color: Palette.ok)
                } else {
                    Button("Turn on") {
                        if let why = Engine.enableLogin() { error = why }
                        else { loginOn = true; Task { await model.ensureEngine() } }
                    }
                    .buttonStyle(AccentButton())
                }
            }

            step(number: 2, title: "What's already here",
                 detail: found.isEmpty
                    ? "Looking for Claude Code, Codex and any local servers…"
                    : "Found on this Mac. hub runs these as you — it never reads a login.") {
                EmptyView()
            }
            if !found.isEmpty {
                VStack(spacing: 6) {
                    ForEach(found) { t in
                        HStack(spacing: 10) {
                            Text(t.title).font(.system(size: 13, weight: .medium))
                            Text(t.found ?? "").font(.system(size: 11))
                                .foregroundStyle(Palette.inkFaint).lineLimit(1)
                            Spacer()
                            if added.contains(t.id) {
                                Tag(text: "added", color: Palette.ok)
                            } else if adding.contains(t.id) {
                                ProgressView().controlSize(.small)
                            } else {
                                Button("Add") { Task { await add(t) } }
                                    .buttonStyle(GhostButton())
                            }
                        }
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(Palette.surface, in: RoundedRectangle(cornerRadius: 8))
                    }
                }
            }

            step(number: 3, title: "From a terminal",
                 detail: "Optional: `hub ask`, `hub runs`, `hub watch` — the same engine, "
                       + "same history.") {
                if Engine.cliInstalled && cliNote.isEmpty {
                    Tag(text: "installed", color: Palette.ok)
                } else {
                    Button("Install `hub`") { cliNote = Engine.installCLI() }
                        .buttonStyle(GhostButton())
                }
            }
            if !cliNote.isEmpty {
                Text(cliNote).font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
            }
            if !error.isEmpty {
                Text(error).font(.system(size: 12)).foregroundStyle(Palette.danger)
            }

            Spacer()
            HStack {
                Text("You can change all of this later in Settings.")
                    .font(.system(size: 11.5)).foregroundStyle(Palette.inkFaint)
                Spacer()
                Button("Start using hub") {
                    onboarded = true
                    dismiss()
                }
                .buttonStyle(AccentButton())
            }
        }
        .padding(26)
        .frame(width: 540, height: 560)
        .task {
            found = (try? await model.client.discover()) ?? []
            await model.refreshProviders()
            added = Set(model.providers.map(\.kind))
                .intersection(Set(found.map(\.kind)))
                .isEmpty ? [] : Set(found.filter { t in
                    model.providers.contains { $0.kind == t.kind }
                }.map(\.id))
        }
    }

    private func step<C: View>(number: Int, title: String, detail: String,
                               @ViewBuilder trailing: () -> C) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(number)")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Palette.accent)
                .frame(width: 22, height: 22)
                .background(Palette.accent.opacity(0.12), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 14, weight: .medium))
                Text(detail).font(.system(size: 12)).foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            trailing()
        }
    }

    private func add(_ t: ProviderTemplate) async {
        adding.insert(t.id)
        defer { adding.remove(t.id) }
        var body: [String: Any] = ["template": t.id]
        if t.needs == "url", let found = t.found { body["base_url"] = found }
        do {
            _ = try await model.client.addProvider(body)
            added.insert(t.id)
            await model.refreshProviders()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
