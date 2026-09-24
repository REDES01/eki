// SPDX-License-Identifier: Apache-2.0
// Claude Code under eki's interface: what it's doing, and what it's asking.
//
// The program runs for real (see eki/live.py); eki draws its terminal's
// moments its own way — a line per tool call, a card for a question, a card
// for a permission prompt — and sends the answers back.
import AppKit
import SwiftUI

/// The tool calls of the turn in progress, the way the terminal lists them.
struct ActivityLines: View {
    let lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            ForEach(Array(lines.suffix(12).enumerated()), id: \.offset) { i, line in
                HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                    Text(i == lines.suffix(12).count - 1 ? "●" : "○")
                        .font(.hubGlyph.weighted(.regular))
                        .foregroundStyle(line.hasPrefix("⚠") ? Palette.danger : Palette.inkFaint)
                    Text(line)
                        .font(.hubMonoSmall)
                        .foregroundStyle(line.hasPrefix("⚠") ? Palette.danger : Palette.inkMuted)
                        .lineLimit(1)
                }
            }
        }
        .card(padding: Space.m)
    }
}

/// A question Claude Code asked: its options, or your own words.
struct AskCard: View {
    @EnvironmentObject var model: AppModel
    let prompt: PendingPrompt
    @State private var chosen: [String: Set<String>] = [:]
    @State private var other: [String: String] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            ForEach(Array(prompt.questions.enumerated()), id: \.offset) { _, q in
                VStack(alignment: .leading, spacing: Space.s) {
                    if let h = q.header, !h.isEmpty {
                        SectionLabel(text: h)
                    }
                    Text(q.question).font(.hubHeading)
                        .fixedSize(horizontal: false, vertical: true)
                    ForEach(q.options ?? [], id: \.label) { opt in
                        Button { toggle(q, opt.label) } label: {
                            HStack(alignment: .top, spacing: Space.s) {
                                Image(systemName: picked(q, opt.label)
                                      ? (q.multiSelect == true ? "checkmark.square.fill" : "largecircle.fill.circle")
                                      : (q.multiSelect == true ? "square" : "circle"))
                                    .font(.hubRow)
                                    .foregroundStyle(picked(q, opt.label) ? Palette.accent : Palette.inkFaint)
                                VStack(alignment: .leading, spacing: Space.xxs) {
                                    Text(opt.label).font(.hubRow).foregroundStyle(Palette.ink)
                                    if let d = opt.description, !d.isEmpty {
                                        Text(d).font(.hubCaption).foregroundStyle(Palette.inkMuted)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                                Spacer(minLength: 0)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    TextField("Something else…", text: Binding(
                        get: { other[q.question] ?? "" },
                        set: { other[q.question] = $0 }))
                        .textFieldStyle(.roundedBorder)
                        .font(.hubCallout)
                }
            }
            HStack {
                Spacer()
                Button("Answer") { send() }
                    .buttonStyle(AccentButton())
                    .disabled(!complete)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .card(tone: Palette.accent)
    }

    private func picked(_ q: AskQuestion, _ label: String) -> Bool {
        chosen[q.question]?.contains(label) == true
    }

    private func toggle(_ q: AskQuestion, _ label: String) {
        var set = chosen[q.question] ?? []
        if q.multiSelect == true {
            if set.contains(label) { set.remove(label) } else { set.insert(label) }
        } else {
            set = set.contains(label) ? [] : [label]
        }
        chosen[q.question] = set
    }

    private var complete: Bool {
        prompt.questions.allSatisfy { q in
            !(chosen[q.question] ?? []).isEmpty || !(other[q.question] ?? "").trimmingCharacters(in: .whitespaces).isEmpty
        }
    }

    private func send() {
        var answers: [String: Any] = [:]
        for q in prompt.questions {
            let typed = (other[q.question] ?? "").trimmingCharacters(in: .whitespaces)
            var picks = Array(chosen[q.question] ?? []).sorted()
            if !typed.isEmpty { picks.append(typed) }
            answers[q.question] = q.multiSelect == true ? picks : (picks.first ?? "")
        }
        var input = prompt.input.any as? [String: Any] ?? [:]
        input["answers"] = answers
        model.answer(prompt, with: ["behavior": "allow", "updatedInput": input])
    }
}

/// A permission prompt: what it wants to do, and your say.
struct PermissionCard: View {
    @EnvironmentObject var model: AppModel
    let prompt: PendingPrompt

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(spacing: Space.s) {
                Image(systemName: "hand.raised").font(.hubIcon).foregroundStyle(Palette.warn)
                Text(prompt.title.isEmpty ? "\(prompt.tool) wants to run" : prompt.title)
                    .font(.hubHeading)
            }
            if !prompt.description.isEmpty {
                Text(prompt.description).font(.hubCallout).foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !detail.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(detail)
                        .font(.hubMonoSmall)
                        .foregroundStyle(Palette.ink)
                        .textSelection(.enabled)
                        .padding(.all, Space.m)
                }
                .background(Palette.fill, in: RoundedRectangle(cornerRadius: Radius.small))
            }
            HStack(spacing: Space.s) {
                Spacer()
                Button("Deny") {
                    model.answer(prompt, with: ["behavior": "deny",
                                                "message": "The user declined this action."])
                }
                .buttonStyle(GhostButton())
                if !prompt.suggestions.isEmpty {
                    Button("Always allow") {
                        model.answer(prompt, with: ["behavior": "allow",
                                                    "updatedInput": prompt.input.any,
                                                    "updatedPermissions": prompt.suggestions.map(\.any)])
                    }
                    .buttonStyle(GhostButton())
                    .help("Allow this, and not ask again for the same thing")
                }
                Button("Allow") {
                    model.answer(prompt, with: ["behavior": "allow", "updatedInput": prompt.input.any])
                }
                .buttonStyle(AccentButton())
                .keyboardShortcut(.defaultAction)
            }
        }
        .card(tone: Palette.warn)
    }

    /// The part of the input worth reading: the command, the file, the URL.
    private var detail: String {
        for key in ["command", "file_path", "path", "url", "query", "pattern", "prompt"] {
            if let v = prompt.input[key]?.stringValue, !v.isEmpty {
                return key == "command" ? v : "\(key): \(v)"
            }
        }
        if case .object(let o) = prompt.input, !o.isEmpty {
            return o.map { "\($0.key): \(String(describing: $0.value.any))" }.sorted().joined(separator: "\n")
        }
        return ""
    }
}

/// The slash commands that match what's typed, above the composer.
struct CommandMenu: View {
    let commands: [SlashCommand]
    let picked: Int
    let choose: (SlashCommand) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: Space.xxs) {
                    ForEach(Array(commands.enumerated()), id: \.element.id) { i, c in
                        Button { choose(c) } label: {
                            HStack(alignment: .firstTextBaseline, spacing: Space.m) {
                                Text("/" + c.name)
                                    .font(.hubMonoSmall.weighted(.medium))
                                    .foregroundStyle(Palette.ink)
                                    .lineLimit(1).truncationMode(.middle)
                                    .frame(maxWidth: 260, alignment: .leading)
                                    .layoutPriority(2)
                                if let hint = c.argumentHint, !hint.isEmpty {
                                    Text(hint).font(.hubMonoSmall)
                                        .foregroundStyle(Palette.inkFaint)
                                        .lineLimit(1).truncationMode(.tail)
                                        .frame(maxWidth: 180, alignment: .leading)
                                }
                                Text(c.description ?? "")
                                    .font(.hubCaption).foregroundStyle(Palette.inkMuted)
                                    .lineLimit(1).truncationMode(.tail)
                                Spacer(minLength: 0)
                            }
                            .listRow(selected: i == picked)
                        }
                        .buttonStyle(.plain)
                        .id(i)
                    }
                }
                .padding(.all, Space.s)
            }
            .frame(maxHeight: 260)
            .onChange(of: picked) { _, now in proxy.scrollTo(now) }
        }
    }
}

/// Arrow keys, Tab and Return while the command menu is up. The text field's
/// own editor takes those keys before SwiftUI sees them, so they're caught
/// at the window: only while the menu shows, and only those keys.
final class MenuKeys {
    private var monitor: Any?

    func install(_ handle: @escaping (NSEvent) -> Bool) {
        remove()
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            handle(event) ? nil : event
        }
    }

    func remove() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
    }

    deinit { remove() }
}
