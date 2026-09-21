// SPDX-License-Identifier: Apache-2.0
// A model being set up, shown where models live.
//
// Setting a model up is a run like any other — it survives the window and
// streams its progress — but it has no chat to appear in, so it appears here,
// under the servers it is about to join. When it finishes the card stays
// until dismissed, so a failure is read rather than missed.
import SwiftUI

struct DeployCard: View {
    @EnvironmentObject var model: AppModel
    let id: String

    @State private var run: Run?
    @State private var output = ""
    @State private var state = ""
    @State private var watcher: Task<Void, Never>?

    private var live: Bool { state == "running" || state == "queued" }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 9) {
                    Dot(color: colour, size: 8, pulsing: live)
                    Text(run?.prompt ?? "Setting up…")
                        .font(.zoomed(size: 13.5, weight: .medium))
                        .lineLimit(1)
                    Spacer()
                    if live {
                        Button("Stop") { Task { await model.cancel(id) } }
                            .buttonStyle(GhostButton())
                    } else {
                        Button("Dismiss") { model.deploys.removeAll { $0 == id } }
                            .buttonStyle(GhostButton())
                    }
                }
                if !recent.isEmpty {
                    Text(recent.joined(separator: "\n"))
                        .font(.zoomed(size: 11.5).monospacedDigit())
                        .foregroundStyle(state == "failed" ? Palette.danger : Palette.inkMuted)
                        .lineLimit(4)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                if let error = run?.error, !error.isEmpty, state == "failed" {
                    Text(error)
                        .font(.zoomed(size: 11.5))
                        .foregroundStyle(Palette.danger)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
        }
        .task { await load() }
        .onDisappear { watcher?.cancel() }
    }

    private var colour: Color {
        switch state {
        case "done": return Palette.ok
        case "failed", "interrupted": return Palette.danger
        case "cancelled": return Palette.inkFaint
        default: return Palette.accent
        }
    }

    /// The last few things it said: what it is, and how the download is going.
    private var recent: [String] {
        let lines = output.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && !$0.hasPrefix(">") }
        return Array(lines.suffix(live ? 3 : 2))
    }

    private func load() async {
        run = try? await model.client.run(id)
        state = run?.state ?? ""
        output = run?.output ?? ""
        guard run?.isLive == true else { return }
        let stream = await model.client.watch(run: id)
        var fresh = ""
        watcher = Task { @MainActor in
            do {
                for try await event in stream {
                    switch event.event {
                    case "output":
                        fresh += event.text ?? ""
                        output = fresh
                    case "state":
                        state = event.state ?? state
                    default: break
                    }
                }
            } catch { /* the engine went away; what arrived stays on screen */ }
            run = try? await model.client.run(id)
            state = run?.state ?? state
            // it's a provider and a server now
            await model.refreshProviders()
        }
    }
}
