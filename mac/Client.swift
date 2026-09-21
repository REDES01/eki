// SPDX-License-Identifier: Apache-2.0
// Talking to the engine.
//
// The app never does work itself — not even a chat turn. It asks the engine
// to start a run and then watches it. Closing the window stops the watching;
// the run doesn't notice.
import Foundation

struct Backend: Codable, Identifiable, Hashable {
    let key: String
    let label: String
    let kind: String
    let tier: Int
    let ok: Bool
    let detail: String
    let capabilities: Capabilities
    /// "router" for the small model that labels requests — it answers nothing
    var role: String? = ""

    var id: String { key }
    var answers: Bool { role != "router" }

    struct Capabilities: Codable, Hashable {
        var repo: Bool? = false
        var tools: Bool? = false
        var vision: Bool? = false
        var images_out: Bool? = false
        var text: Bool? = true
        var context_tokens: Int? = 0
    }

    /// 0 = free and local, 50 = a subscription you already pay for, 100 = metered.
    var priceWord: String {
        switch tier {
        case 0: return "local"
        case ..<100: return "subscription"
        default: return "metered"
        }
    }
}

struct ConversationRow: Codable, Identifiable, Hashable {
    let id: String
    let title: String?
    let n: Int
    var hit: String? = nil          // the matching line, when this came from a search
    var live: Bool? = false         // a run in it is working right now
}

struct Turn: Codable, Identifiable, Hashable {
    let id: Int
    let role: String
    let content: String
    let backend: String?
    let reason: String?
    var meta: String? = nil

    /// The run that produced this answer, and the folder it worked in.
    var run: String? { metaObject?["run"] as? String }
    var cwd: String? { metaObject?["cwd"] as? String }
    /// An answer that ended badly, and so is worth offering to redo.
    var didNotFinish: Bool {
        (metaObject?["failed"] as? Bool ?? false) || (metaObject?["stopped"] as? Bool ?? false)
    }

    private var metaObject: [String: Any]? {
        guard let meta, let data = meta.data(using: .utf8) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
}

struct ConversationView: Codable {
    let id: String
    let turns: [Turn]
    let active_run: String?
}

struct Started: Codable {
    let run: String
    let conversation: String
}

struct Run: Codable, Identifiable, Hashable {
    let id: String
    let prompt: String
    let backend: String?
    let state: String
    let created_at: Int
    var conversation_id: String? = nil
    var ended_at: Int? = nil
    var cwd: String? = nil
    var output: String? = nil
    var output_len: Int? = nil
    var error: String? = nil
    var reason: String? = nil

    var isLive: Bool { state == "running" || state == "queued" }
    var canRetry: Bool { ["failed", "cancelled", "interrupted"].contains(state) }
    var hasFolder: Bool { !(cwd ?? "").isEmpty }
}

struct LocalModelRow: Codable, Identifiable, Hashable {
    let key: String
    let label: String
    let kind: String
    let port: Int
    let gb: Double
    let note: String
    let backend: String
    let running: Bool
    let can_start: Bool
    let blocked_by_memory: Bool

    var id: String { key }
}

struct MemoryReport: Codable, Hashable {
    let total_gb: Double
    let ceiling_gb: Double
    let committed_gb: Double
    let free_gb: Double
}

struct ModelsReport: Codable, Hashable {
    let models: [LocalModelRow]
    let memory: MemoryReport
}

struct CostReport: Codable, Hashable {
    struct Entry: Codable, Hashable {
        let turns: Int
        let input_tokens: Int
        let output_tokens: Int
        var tier: Int? = nil
        var note: String? = nil
    }
    let conversation: String
    let by_backend: [String: Entry]
    let turns: Int
}

struct UsageWindow: Codable, Hashable, Identifiable {
    let key: String
    let label: String
    let used: Double                  // 0..1, may pass 1 for credits
    let resets_at: Int?
    let window_seconds: Int?
    let kind: String                  // "window" | "credits"
    /// false for a limit that covers one model rather than the whole account
    var primary: Bool? = true

    var id: String { key }
    var isPrimary: Bool { primary ?? true }
}

struct ProviderUsage: Codable, Hashable, Identifiable {
    let provider: String
    let label: String
    let windows: [UsageWindow]
    let observed_at: Int?
    let age_seconds: Int?
    let error: String
    let note: String

    var id: String { provider }

    /// The two the menu bar draws: account-wide windows, not per-model ones —
    /// a Fable or Opus allowance is worth seeing in Usage, but it isn't what
    /// "how much is left" means at a glance.
    private var meterable: [UsageWindow] {
        let real = windows.filter { $0.kind == "window" }
        let account = real.filter(\.isPrimary)
        return account.isEmpty ? real : account
    }

    /// Shortest window first — the one that bites soonest.
    var short: UsageWindow? {
        meterable.min { ($0.window_seconds ?? 0) < ($1.window_seconds ?? 0) }
    }
    /// Longest window, when there is more than one.
    var long: UsageWindow? {
        guard meterable.count > 1 else { return nil }
        return meterable.max { ($0.window_seconds ?? 0) < ($1.window_seconds ?? 0) }
    }
}

struct UsageReport: Codable, Hashable {
    let providers: [ProviderUsage]
    let ceiling: Double
    let claude_bridge: Bool
    var claude_probe: Bool? = false          // background refresh is on
    var claude_probe_ready: Bool? = false    // the probe folder is trusted
    var claude_probe_hint: String? = ""
    var message: String? = nil
}

struct PolicyDTO: Codable, Hashable {
    var disabled: [String] = []
    var tiers: [String: Int] = [:]
    var order: [String] = []
    var quota_ceiling: Double? = nil
}

/// One event off a run's stream.
struct RunEvent: Codable {
    var event: String?          // route | output | state | error
    var text: String?
    var end: Int?
    var message: String?
    var backend: String?
    var reason: String?
    var state: String?
}

enum ClientError: LocalizedError {
    case offline
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .offline: return "The engine isn't running."
        case .http(let code, let body): return "Engine error \(code): \(body)"
        }
    }
}

actor EngineClient {
    let base: URL
    private let session: URLSession

    init(port: Int = 8787) {
        self.base = URL(string: "http://127.0.0.1:\(port)")!
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 30
        // a run can think for a long time before it says anything
        cfg.timeoutIntervalForResource = 86_400
        self.session = URLSession(configuration: cfg)
    }

    private func url(_ path: String) -> URL {
        // appendingPathComponent escapes "?" into the path, so a query string
        // would arrive as part of the path and 404. Split it off first.
        let parts = path.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        var url = base.appendingPathComponent(String(parts[0]))
        if parts.count == 2, var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            comps.percentEncodedQuery = String(parts[1])
            url = comps.url ?? url
        }
        return url
    }

    private func request(_ method: String, _ path: String, body: Any? = nil,
                         timeout: TimeInterval? = nil) throws -> URLRequest {
        var req = URLRequest(url: url(path))
        req.httpMethod = method
        if let timeout { req.timeoutInterval = timeout }
        if let body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    func decode<T: Decodable>(_ type: T.Type, _ method: String, _ path: String,
                              body: Any? = nil, timeout: TimeInterval? = nil) async throws -> T {
        let req = try request(method, path, body: body, timeout: timeout)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw ClientError.offline
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw ClientError.http(code, String(data: data, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    // ---- work -----------------------------------------------------------

    /// Start a run. Returns as soon as the engine has written the question.
    func ask(prompt: String, conversation: String, backend: String,
             repo: String) async throws -> Started {
        let body: [String: Any] = ["prompt": prompt, "conversation": conversation,
                                   "backend": backend, "repo": repo]
        return try await decode(Started.self, "POST", "api/ask", body: body)
    }

    func cancel(run id: String) async {
        guard let req = try? request("POST", "api/runs/\(id)/cancel") else { return }
        _ = try? await session.data(for: req)
    }

    func retry(run id: String) async throws -> Started {
        try await decode(Started.self, "POST", "api/runs/\(id)/retry")
    }

    /// A run's events: the stored log first, then live, until it ends.
    func watch(run id: String) -> AsyncThrowingStream<RunEvent, Error> {
        let session = self.session
        let req = try? request("GET", "api/runs/\(id)/stream")
        return AsyncThrowingStream { continuation in
            guard let req else { continuation.finish(); return }
            let task = Task {
                do {
                    let (bytes, response) = try await session.bytes(for: req)
                    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard (200..<300).contains(code) else {
                        throw ClientError.http(code, "")
                    }
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data: ") else { continue }   // ": keepalive"
                        let payload = Data(line.dropFirst(6).utf8)
                        if let event = try? JSONDecoder().decode(RunEvent.self, from: payload) {
                            continuation.yield(event)
                        }
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // ---- reading --------------------------------------------------------

    func health() async -> Bool {
        struct Status: Codable { let ok: Bool }
        return ((try? await decode(Status.self, "GET", "api/health"))?.ok) ?? false
    }

    func backends() async throws -> [Backend] {
        try await decode([Backend].self, "GET", "api/backends")
    }

    func conversations(matching query: String = "") async throws -> [ConversationRow] {
        let escaped = query.addingPercentEncoding(
            withAllowedCharacters: .urlQueryAllowed) ?? ""
        let path = escaped.isEmpty ? "api/conversations" : "api/conversations?q=\(escaped)"
        return try await decode([ConversationRow].self, "GET", path)
    }

    func conversation(_ id: String) async throws -> ConversationView {
        try await decode(ConversationView.self, "GET", "api/conversations/\(id)")
    }

    func cost(_ id: String) async throws -> CostReport {
        try await decode(CostReport.self, "GET", "api/conversations/\(id)/cost")
    }

    func runs() async throws -> [Run] {
        try await decode([Run].self, "GET", "api/runs")
    }

    func run(_ id: String) async throws -> Run {
        try await decode(Run.self, "GET", "api/runs/\(id)")
    }

    func diff(run id: String) async throws -> String {
        struct Wrapper: Codable { let diff: String }
        return try await decode(Wrapper.self, "GET", "api/runs/\(id)/diff").diff
    }

    // ---- usage ----------------------------------------------------------

    func usage(refresh: Bool = false) async throws -> UsageReport {
        try await decode(UsageReport.self, refresh ? "POST" : "GET",
                         refresh ? "api/usage/refresh" : "api/usage")
    }

    /// Start a throwaway Claude Code session and read its status line.
    func probeClaude() async throws -> UsageReport {
        // it starts a Claude Code session and waits for its status line; a
        // minute and a half is normal, and the default 30s is not enough
        try await decode(UsageReport.self, "POST", "api/usage/claude-probe", timeout: 240)
    }

    func setClaudeBridge(_ enabled: Bool) async throws -> UsageReport {
        try await decode(UsageReport.self, "POST", "api/usage/claude-bridge",
                         body: ["enabled": enabled])
    }

    // ---- settings -------------------------------------------------------

    func models() async throws -> ModelsReport {
        try await decode(ModelsReport.self, "GET", "api/models")
    }

    @discardableResult
    func setModel(_ key: String, running: Bool, force: Bool = false) async throws -> String {
        struct Wrapper: Codable { let message: String }
        let path = "api/models/\(key)/\(running ? "start" : "stop")"
            + (force ? "?force=true" : "")
        return try await decode(Wrapper.self, "POST", path).message
    }

    func policy() async throws -> PolicyDTO {
        try await decode(PolicyDTO.self, "GET", "api/policy")
    }

    @discardableResult
    func save(policy: PolicyDTO) async throws -> PolicyDTO {
        var body: [String: Any] = ["disabled": policy.disabled,
                                   "tiers": policy.tiers,
                                   "order": policy.order]
        if let ceiling = policy.quota_ceiling { body["quota_ceiling"] = ceiling }
        return try await decode(PolicyDTO.self, "PUT", "api/policy", body: body)
    }
}
