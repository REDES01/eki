// SPDX-License-Identifier: Apache-2.0
// A newer build of the app, put in place while this one is open.
//
// eki rebuilds the app when its source changes (eki/appbuild.py), but an
// open app keeps running the old code until it restarts — and waiting for it
// to leave the front never ends for an app kept in front. So the app looks at
// its own bundle on disk: when the build there isn't the one running, it says
// so in a small banner and in the app menu, and restarts at a click — or by
// itself once it has been in the background, or you've been away, a while.
import AppKit
import SwiftUI

/// Which build a bundle is: the hash of mac/ it was built from and when
/// (EkiBuild, EkiBuiltAt — stamped by build_app.sh).
struct AppBuild: Equatable {
    let build: String
    let builtAt: Int

    init(info: [String: Any]?) {
        build = info?["EkiBuild"] as? String ?? ""
        builtAt = Int(info?["EkiBuiltAt"] as? String ?? "") ?? 0
    }

    /// The build this process is running, read once at launch — the bundle
    /// on disk may be replaced under it later.
    static let running = AppBuild(info: Bundle.main.infoDictionary)

    /// The build on disk where this app came from; nil while it's missing
    /// (a rebuild swapping it in).
    static func installed() -> AppBuild? {
        let plist = Bundle.main.bundleURL.appendingPathComponent("Contents/Info.plist")
        guard let info = NSDictionary(contentsOf: plist) as? [String: Any] else { return nil }
        return AppBuild(info: info)
    }

    /// "b7a8cf2 · built Sep 26, 00:51"
    var label: String {
        let name = build.isEmpty ? "unstamped" : build
        guard builtAt > 0 else { return name }
        let when = Date(timeIntervalSince1970: TimeInterval(builtAt))
        return "\(name) · built \(when.formatted(.dateTime.month(.abbreviated).day().hour().minute()))"
    }

    static var version: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
    }
}

@MainActor
final class AppUpdate: ObservableObject {
    static let shared = AppUpdate()

    /// The newer build waiting on disk, if there is one.
    @Published private(set) var ready: AppBuild?
    /// The banner was closed; it comes back for the next build.
    @Published var dismissed = false

    /// How long the app is left alone before it restarts by itself.
    static let leftAlone: TimeInterval = 5 * 60
    /// Something is under way that a restart would cut short (an answer
    /// being watched); the self-restart waits for it.
    var busy: () -> Bool = { false }

    private var loop: Task<Void, Never>?
    private var awaySince: Date?

    func start() {
        guard loop == nil else { return }
        _ = AppBuild.running
        announce()
        let center = NotificationCenter.default
        center.addObserver(forName: NSApplication.didResignActiveNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { AppUpdate.shared.awaySince = Date() }
        }
        center.addObserver(forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { AppUpdate.shared.awaySince = nil }
        }
        if !NSApp.isActive { awaySince = Date() }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                self?.tick()
                try? await Task.sleep(for: .seconds(30))
            }
        }
    }

    private func tick() {
        let disk = AppBuild.installed()
        if let disk, disk.builtAt > 0, disk != AppBuild.running {
            if ready != disk { ready = disk; dismissed = false }
        } else if disk != nil {
            ready = nil
        }
        if ready != nil, leftFor >= Self.leftAlone, !busy() { restart(quietly: true) }
    }

    /// How long nobody has used the app: in the background, or no input at all.
    private var leftFor: TimeInterval {
        let away = awaySince.map { Date().timeIntervalSince($0) } ?? 0
        let anyInput = CGEventType(rawValue: ~0)!
        let idle = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: anyInput)
        return max(away, idle)
    }

    /// Quit and open the bundle again. A small shell waits for this process
    /// to end first, or `open` would only bring the old one forward; -n so it
    /// opens this bundle even when another copy of the app is open. Quietly:
    /// in the background, so a restart you didn't ask for takes no focus.
    func restart(quietly: Bool = false) {
        let pid = ProcessInfo.processInfo.processIdentifier
        let relaunch = Process()
        relaunch.executableURL = URL(fileURLWithPath: "/bin/sh")
        relaunch.arguments = ["-c", "while kill -0 \(pid) 2>/dev/null; do sleep 0.2; done; open -n \(quietly ? "-g " : "")\"$0\"",
                              Bundle.main.bundlePath]
        do { try relaunch.run() } catch { return }
        NSApp.terminate(nil)
    }

    // ---- what `eki self` and the board read (eki/appbuild.py versions) ----

    private var note: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".eki/app-running/\(ProcessInfo.processInfo.processIdentifier).json")
    }

    private func announce() {
        let run = AppBuild.running
        let fields: [String: Any] = ["pid": Int(ProcessInfo.processInfo.processIdentifier),
                                     "build": run.build, "built_at": run.builtAt,
                                     "path": Bundle.main.bundlePath,
                                     "opened": Int(Date().timeIntervalSince1970)]
        guard let data = try? JSONSerialization.data(withJSONObject: fields) else { return }
        try? FileManager.default.createDirectory(at: note.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        try? data.write(to: note)
    }

    func forget() { try? FileManager.default.removeItem(at: note) }
}

/// "New version ready — Restart": small, at the top of the window, until you
/// restart or close it.
struct UpdateBanner: View {
    @ObservedObject private var update = AppUpdate.shared

    var body: some View {
        if let next = update.ready, !update.dismissed {
            HStack(spacing: Space.s) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.hubIconSmall)
                    .foregroundStyle(Palette.accent)
                Text("New version ready")
                    .font(.hubCallout)
                    .foregroundStyle(Palette.ink)
                Button("Restart") { update.restart() }
                    .buttonStyle(.plain)
                    .font(.hubCallout.weighted(.semibold))
                    .foregroundStyle(Palette.accent)
                Button { withAnimation(.easeOut(duration: 0.2)) { update.dismissed = true } } label: {
                    Image(systemName: "xmark").font(.hubIconSmall)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Palette.inkFaint)
                .help("Hide — the app still restarts by itself once you've left it a few minutes")
            }
            .padding(.horizontal, Space.m)
            .padding(.vertical, Space.xs)
            .background(Palette.surface, in: Capsule())
            .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
            .raised()
            .padding(.top, Space.s)
            .help("Running \(AppBuild.running.label)\nInstalled \(next.label)")
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }
}

/// The app menu's items: About with the build in it, and Restart to Update
/// while a newer build waits.
struct AppMenuItems: View {
    @ObservedObject private var update = AppUpdate.shared

    var body: some View {
        Button("About Eki") {
            NSApp.orderFrontStandardAboutPanel(options: [
                .applicationVersion: "\(AppBuild.version) (\(AppBuild.running.label))",
            ])
        }
        if update.ready != nil {
            Button("Restart to Update") { update.restart() }
        }
    }
}

/// Settings → General: the build running, and a newer one if it's there.
struct AppBuildRow: View {
    @ObservedObject private var update = AppUpdate.shared

    var body: some View {
        LabeledContent("Running", value: "\(AppBuild.version) · \(AppBuild.running.label)")
        if let next = update.ready {
            LabeledContent("Installed") {
                HStack(spacing: Space.s) {
                    Text(next.label)
                    Button("Restart") { update.restart() }
                }
            }
        }
    }
}
