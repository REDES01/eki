// SPDX-License-Identifier: Apache-2.0
// Things eki does on a timetable: a request, a folder, a provider, and when.
//
// Each firing is an ordinary thread — named after the schedule and the
// time, routed like anything typed — so it lives in the chat list with its
// reason line, and can be resumed or retried like the rest. The engine
// keeps the clock whether or not this window is open.
import SwiftUI

struct ScheduleDTO: Codable, Identifiable, Hashable {
    let id: String
    var name: String
    var prompt: String
    var cwd: String
    var backend: String
    var spec: JSONValue
    var enabled: Bool
    var created_at: Int
    var last_run_at: Int?
    var last_conversation: String
    var last_run: String
    var next_run_at: Int?
    var when: String

    var kind: String { spec["kind"]?.stringValue ?? "interval" }
    var minutes: Int {
        if case .number(let n)? = spec["minutes"] { return Int(n) }
        return 60
    }
    var at: String { spec["at"]?.stringValue ?? "09:00" }
    var days: Set<Int> {
        if case .array(let a)? = spec["days"] {
            return Set(a.compactMap { if case .number(let n) = $0 { return Int(n) } else { return nil } })
        }
        return Set(0..<7)
    }
}

extension EngineClient {
    func schedules() async throws -> [ScheduleDTO] {
        struct W: Codable { let schedules: [ScheduleDTO] }
        return try await decode(W.self, "GET", "api/schedules").schedules
    }

    func createSchedule(_ body: [String: Any]) async throws -> ScheduleDTO {
        try await decode(ScheduleDTO.self, "POST", "api/schedules", body: body)
    }

    func updateSchedule(_ id: String, _ body: [String: Any]) async throws -> ScheduleDTO {
        try await decode(ScheduleDTO.self, "PATCH", "api/schedules/\(id)", body: body)
    }

    func deleteSchedule(_ id: String) async throws {
        struct W: Codable { let ok: Bool }
        _ = try await decode(W.self, "DELETE", "api/schedules/\(id)")
    }

    func runSchedule(_ id: String) async throws -> Started {
        try await decode(Started.self, "POST", "api/schedules/\(id)/run")
    }
}

struct SchedulesPane: View {
    @EnvironmentObject var model: AppModel
    @State private var schedules: [ScheduleDTO] = []
    @State private var editing: ScheduleDTO?
    @State private var adding = false
    @State private var note = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                Group2("Scheduled",
                       note: "each firing is a new thread, routed like anything you type "
                           + "— or pinned to a provider — and runs whether or not eki is open. "
                           + "A time that passes while the Mac sleeps fires once on waking.") {
                    ForEach(schedules) { s in
                        ScheduleCard(schedule: s,
                                     open: { open(s) },
                                     edit: { editing = s },
                                     toggle: { on in Task { await set(s, enabled: on) } },
                                     runNow: { Task { await runNow(s) } },
                                     delete: { Task { await delete(s) } })
                    }
                    if schedules.isEmpty {
                        Text("Nothing scheduled yet.")
                            .font(.system(size: 12.5)).foregroundStyle(Palette.inkFaint)
                    }
                    Button { adding = true } label: {
                        Label("Add schedule", systemImage: "plus")
                    }
                    .buttonStyle(GhostButton())
                }
                if !note.isEmpty {
                    Text(note).font(.system(size: 12)).foregroundStyle(Palette.inkMuted)
                }
            }
            .frame(maxWidth: Metric.column, alignment: .leading)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, Metric.gutter)
            .padding(.vertical, 28)
        }
        .background(Palette.canvas)
        .task { await load() }
        .sheet(isPresented: $adding) {
            ScheduleSheet(schedule: nil) { await load() }
        }
        .sheet(item: $editing) { s in
            ScheduleSheet(schedule: s) { await load() }
        }
    }

    private func load() async {
        schedules = (try? await model.client.schedules()) ?? []
    }

    private func open(_ s: ScheduleDTO) {
        guard !s.last_conversation.isEmpty else { return }
        model.open(s.last_conversation)
        model.paneRequest = .chat(s.last_conversation)
    }

    private func set(_ s: ScheduleDTO, enabled: Bool) async {
        _ = try? await model.client.updateSchedule(s.id, ["enabled": enabled])
        await load()
    }

    private func runNow(_ s: ScheduleDTO) async {
        do {
            let started = try await model.client.runSchedule(s.id)
            note = "Started “\(s.name)” — it's in the chat list now."
            await load()
            model.open(started.conversation)
            model.paneRequest = .chat(started.conversation)
        } catch {
            note = error.localizedDescription
        }
    }

    private func delete(_ s: ScheduleDTO) async {
        try? await model.client.deleteSchedule(s.id)
        await load()
    }
}

struct ScheduleCard: View {
    let schedule: ScheduleDTO
    let open: () -> Void
    let edit: () -> Void
    let toggle: (Bool) -> Void
    let runNow: () -> Void
    let delete: () -> Void
    @State private var confirmDelete = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Toggle("", isOn: Binding(get: { schedule.enabled }, set: toggle))
                    .labelsHidden().toggleStyle(AccentSwitch()).controlSize(.mini)
                VStack(alignment: .leading, spacing: 2) {
                    Text(schedule.name).font(.system(size: 13.5, weight: .semibold))
                    Text(schedule.when + (schedule.backend.isEmpty ? "" : " · \(schedule.backend)")
                         + (schedule.cwd.isEmpty ? "" : " · " + URL(fileURLWithPath: schedule.cwd).lastPathComponent))
                        .font(.system(size: 11.5)).foregroundStyle(Palette.inkMuted)
                }
                Spacer()
                Button("Run now", action: runNow).buttonStyle(GhostButton())
                Button("Edit", action: edit).buttonStyle(GhostButton())
                Button { confirmDelete = true } label: { Image(systemName: "trash") }
                    .buttonStyle(GhostButton())
                    .confirmationDialog("Delete “\(schedule.name)”?", isPresented: $confirmDelete) {
                        Button("Delete", role: .destructive, action: delete)
                    }
            }
            Text(schedule.prompt)
                .font(.system(size: 12.5)).foregroundStyle(Palette.ink)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 14) {
                if let next = schedule.next_run_at, schedule.enabled {
                    Label("next " + Self.when(next), systemImage: "clock")
                }
                if let last = schedule.last_run_at {
                    Button {
                        open()
                    } label: {
                        Label("last ran " + Self.when(last), systemImage: "arrow.up.right.square")
                    }
                    .buttonStyle(.plain)
                    .help("Open that thread")
                    if schedule.last_run.hasPrefix("failed") {
                        Text(schedule.last_run).foregroundStyle(Palette.danger)
                    }
                }
            }
            .font(.system(size: 11)).foregroundStyle(Palette.inkFaint)
        }
        .padding(14)
        .background(Palette.surface, in: RoundedRectangle(cornerRadius: Metric.radius))
        .overlay(RoundedRectangle(cornerRadius: Metric.radius).strokeBorder(Palette.hairline, lineWidth: 1))
        .opacity(schedule.enabled ? 1 : 0.7)
    }

    static func when(_ epoch: Int) -> String {
        let f = DateFormatter()
        f.dateFormat = "EEE d MMM, HH:mm"
        return f.string(from: Date(timeIntervalSince1970: TimeInterval(epoch)))
    }
}

/// Add or edit a schedule.
struct ScheduleSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let schedule: ScheduleDTO?
    let done: () async -> Void

    @State private var name = ""
    @State private var prompt = ""
    @State private var folder = ""
    @State private var backend = ""
    @State private var kind = "daily"
    @State private var hours = 1.0
    @State private var at = Calendar.current.date(from: DateComponents(hour: 9, minute: 0)) ?? Date()
    @State private var days: Set<Int> = [0, 1, 2, 3, 4]
    @State private var problem = ""
    @State private var saving = false

    private let dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(schedule == nil ? "New schedule" : "Edit schedule").font(.hubTitle)

            TextField("Name", text: $name).textFieldStyle(.roundedBorder)

            TextEditor(text: $prompt)
                .font(.hubBody)
                .scrollContentBackground(.hidden)
                .padding(8)
                .frame(height: 110)
                .background(Palette.surface, in: RoundedRectangle(cornerRadius: Metric.smallRadius))
                .overlay(RoundedRectangle(cornerRadius: Metric.smallRadius)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
                .overlay(alignment: .topLeading) {
                    if prompt.isEmpty {
                        Text("What should it do each time?")
                            .font(.hubBody).foregroundStyle(Palette.inkFaint)
                            .padding(.horizontal, 13).padding(.vertical, 16)
                            .allowsHitTesting(false)
                    }
                }

            HStack(spacing: 8) {
                TextField("Folder (optional)", text: $folder).textFieldStyle(.roundedBorder)
                Button("Choose…") { pickFolder() }.buttonStyle(GhostButton())
            }

            Picker("Provider", selection: $backend) {
                Text("Auto — routed each time").tag("")
                ForEach(model.backends.filter(\.answers)) { b in
                    Text(b.key).tag(b.key)
                }
            }

            Picker("When", selection: $kind) {
                Text("Daily").tag("daily")
                Text("Every so often").tag("interval")
            }
            .pickerStyle(.segmented)

            if kind == "daily" {
                HStack(spacing: 10) {
                    DatePicker("At", selection: $at, displayedComponents: .hourAndMinute)
                        .datePickerStyle(.field)
                    Spacer()
                    ForEach(0..<7, id: \.self) { d in
                        Button {
                            if days.contains(d) { days.remove(d) } else { days.insert(d) }
                        } label: {
                            Text(dayNames[d])
                                .font(.system(size: 11, weight: .medium))
                                .padding(.horizontal, 7).padding(.vertical, 4)
                                .background(days.contains(d) ? Palette.accent.opacity(0.2) : Palette.fill,
                                            in: Capsule())
                                .foregroundStyle(days.contains(d) ? Palette.ink : Palette.inkFaint)
                        }
                        .buttonStyle(.plain)
                    }
                }
            } else {
                HStack {
                    Text("Every")
                    Stepper(value: $hours, in: 0.25...168, step: hours < 1 ? 0.25 : 1) {
                        Text(hours < 1 ? "\(Int(hours * 60)) min" : String(format: "%g hour%@", hours, hours == 1 ? "" : "s"))
                            .monospacedDigit()
                    }
                }
            }

            if !problem.isEmpty {
                Text(problem).font(.system(size: 12)).foregroundStyle(Palette.danger)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(GhostButton())
                Button(saving ? "Saving…" : "Save") { Task { await save() } }
                    .buttonStyle(AccentButton())
                    .disabled(saving || prompt.trimmingCharacters(in: .whitespaces).isEmpty)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(width: 540)
        .onAppear {
            guard let s = schedule else { folder = model.lastRepo; return }
            name = s.name; prompt = s.prompt; folder = s.cwd; backend = s.backend
            kind = s.kind
            hours = Double(s.minutes) / 60
            days = s.days
            let parts = s.at.split(separator: ":").compactMap { Int($0) }
            if parts.count == 2, let d = Calendar.current.date(from: DateComponents(hour: parts[0], minute: parts[1])) {
                at = d
            }
        }
    }

    private func pickFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { folder = url.path }
    }

    private var spec: [String: Any] {
        if kind == "daily" {
            let c = Calendar.current.dateComponents([.hour, .minute], from: at)
            return ["kind": "daily", "at": String(format: "%02d:%02d", c.hour ?? 9, c.minute ?? 0),
                    "days": Array(days).sorted()]
        }
        return ["kind": "interval", "minutes": Int(hours * 60)]
    }

    private func save() async {
        saving = true
        defer { saving = false }
        let body: [String: Any] = ["name": name.isEmpty ? String(prompt.prefix(40)) : name,
                                   "prompt": prompt, "cwd": folder, "backend": backend, "spec": spec]
        do {
            if let s = schedule {
                _ = try await model.client.updateSchedule(s.id, body)
            } else {
                _ = try await model.client.createSchedule(body)
            }
            await done()
            dismiss()
        } catch {
            problem = error.localizedDescription
        }
    }
}
