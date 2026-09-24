// SPDX-License-Identifier: Apache-2.0
// Everything eki has made, in one place.
//
// A page built on Tuesday is three threads down by Friday, and nobody
// remembers which thread. The gallery is every page, drawing, diagram and
// picture from every conversation, newest first, each one a click from being
// open again and two from the chat that made it.
//
// Nothing is stored twice to make this work. The engine hands over the
// answers that might hold something; the same reader that draws the chat
// decides what in them is an artifact, so the gallery and the transcript can
// never disagree about it.
import AppKit
import SwiftUI
import WebKit

struct MadeTurn: Codable {
    let id: Int
    let conversation_id: String
    var title: String? = nil
    var archived: Int? = 0
    let content: String
    var backend: String? = nil
    var created_at: Int? = nil
    var files: [String]? = nil      // what the run wrote into its folder that can be shown here
}

extension EngineClient {
    func made(limit: Int = 400) async throws -> [MadeTurn] {
        try await decode([MadeTurn].self, "GET", "api/artifacts?limit=\(limit)")
    }
}

struct MadeItem: Identifiable, Hashable {
    enum What: Hashable {
        case artifact(Artifact)
        case picture(Picture)
    }

    let id: String
    let mark: String                // what it is called once hidden; the same every launch
    let what: What
    let conversation: String
    let chat: String
    let backend: String
    let when: Date?

    var title: String {
        switch what {
        case .artifact(let a): return a.title
        case .picture(let p): return p.alt.isEmpty ? p.name : p.alt
        }
    }

    var kindLabel: String {
        switch what {
        case .artifact(let a): return a.kindLabel
        case .picture: return "Image"
        }
    }

    /// Newest first, as the engine sent them; within one answer, in the order
    /// they were written. The same thing said twice is one thing.
    static func all(in turns: [MadeTurn]) -> [MadeItem] {
        var seen = Set<String>()
        var out: [MadeItem] = []
        for turn in turns {
            let when = turn.created_at.map { Date(timeIntervalSince1970: TimeInterval($0)) }
            let chat = (turn.title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            var n = 0
            for block in MarkdownParser.blocks(turn.content) {
                let what: What
                let mark: String
                let name: String
                switch block {
                case .code(let code, let language):
                    guard let artifact = Artifact(code: code, language: language) else { continue }
                    what = .artifact(artifact)
                    mark = turn.conversation_id + artifact.id
                    name = Hidden.mark(artifact, in: turn.conversation_id)
                case .image(let alt, let source):
                    let picture = Picture(source: source, alt: alt)
                    what = .picture(picture)
                    mark = picture.url?.path ?? source
                    name = Hidden.mark(Picture(source: source))
                default:
                    continue
                }
                guard seen.insert(mark).inserted else { continue }
                n += 1
                out.append(MadeItem(id: "\(turn.id)-\(n)", mark: name, what: what,
                                    conversation: turn.conversation_id,
                                    chat: chat.isEmpty ? "Untitled chat" : chat,
                                    backend: turn.backend ?? "", when: when))
            }
            // Claude Code and Codex write their work into the folder, not the
            // answer: the pages and pictures a run left there belong here too
            for path in turn.files ?? [] {
                guard let what = fromFile(path), seen.insert(path).inserted else { continue }
                let name: String
                switch what {
                case .artifact(let a): name = Hidden.mark(a, in: turn.conversation_id)
                case .picture(let p): name = Hidden.mark(p)
                }
                n += 1
                out.append(MadeItem(id: "\(turn.id)-\(n)", mark: name, what: what,
                                    conversation: turn.conversation_id,
                                    chat: chat.isEmpty ? "Untitled chat" : chat,
                                    backend: turn.backend ?? "", when: when))
            }
        }
        return out
    }

    /// A file an agent wrote, read the way a fence of its kind would be;
    /// nil for anything else, or too big to be a page.
    static func fromFile(_ path: String) -> What? {
        let ext = (path as NSString).pathExtension.lowercased()
        if ["png", "jpg", "jpeg", "gif", "webp", "heic"].contains(ext) {
            return .picture(Picture(source: path, alt: (path as NSString).lastPathComponent))
        }
        let language: String
        switch ext {
        case "html", "htm": language = "html"
        case "svg": language = "svg"
        case "mmd", "mermaid": language = "mermaid"
        default: return nil
        }
        let url = URL(fileURLWithPath: path)
        guard let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize, size < 2_000_000,
              let text = try? String(contentsOf: url, encoding: .utf8),
              let artifact = Artifact(code: text, language: language) else { return nil }
        return .artifact(artifact)
    }
}

enum MadeFilter: String, CaseIterable, Identifiable {
    case all = "All", pages = "Pages", drawings = "Drawings", diagrams = "Diagrams", images = "Images"

    var id: String { rawValue }

    func keeps(_ item: MadeItem) -> Bool {
        switch (self, item.what) {
        case (.all, _): return true
        case (.pages, .artifact(let a)): return a.kind == .html
        case (.drawings, .artifact(let a)): return a.kind == .svg
        case (.diagrams, .artifact(let a)): return a.kind == .mermaid
        case (.images, .picture): return true
        default: return false
        }
    }
}

// MARK: - the pane

struct GalleryPane: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject private var stage = Stage.shared
    @AppStorage("eki.artifactWidth") private var panelWidth: Double = 480
    @Environment(\.zoom) private var zoom
    @State private var items: [MadeItem] = []
    @State private var loaded = false
    @State private var failed = ""
    @State private var filter: MadeFilter = .all
    @State private var query = ""

    private static let gridMinimum: Double = 300

    private var shown: [MadeItem] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return items.filter { item in
            guard filter.keeps(item), !stage.hidden.contains(item.mark) else { return false }
            if q.isEmpty { return true }
            return item.title.lowercased().contains(q) || item.chat.lowercased().contains(q)
                || item.kindLabel.lowercased().contains(q)
        }
    }

    var body: some View {
        GeometryReader { geo in
            let most = max(300, Double(geo.size.width) - Self.gridMinimum)
            // what the tiles and their header get: the pane, less an open artifact
            let room = Double(geo.size.width) - (stage.artifact != nil ? min(panelWidth, most) + 6 : 0)
            HStack(spacing: 0) {
                grid(room)
                if let artifact = stage.artifact {
                    PanelHandle(width: $panelWidth, limit: 300...most)
                    ArtifactPanel(artifact: artifact, walks: true)
                        .frame(width: min(panelWidth, most))
                        .transition(.move(edge: .trailing))
                }
            }
        }
        .task { await load() }
        // the list being walked is the list on screen, filter and search included
        .onChange(of: shown.map(\.id)) { if stage.live != nil { stage.deck = shown } }
        .onDisappear {
            stage.artifact = nil
            stage.deck = []
            stage.current = nil
        }
    }

    private func grid(_ room: Double) -> some View {
        VStack(spacing: 0) {
            header(room)
            Rectangle().fill(Palette.hairline).frame(height: 1)
            if shown.isEmpty {
                empty
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 210 * zoom, maximum: 320 * zoom),
                                                 spacing: 16 * zoom)],
                              alignment: .leading, spacing: 18 * zoom) {
                        ForEach(shown) { item in
                            MadeTile(item: item, open: { open(item) }, goToChat: { goToChat(item) },
                                     remove: { stage.deck = shown; stage.remove(item) })
                        }
                    }
                    .padding(.all, Metric.gutter)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// As the room narrows — a smaller window, or an artifact open beside the
    /// tiles — the filter folds into a menu, then the search steps aside; the
    /// title never wraps and the summary is cut short rather than the tiles.
    private func header(_ room: Double) -> some View {
        let compact = stage.artifact != nil || room < 820 + 60 * zoom
        let searching = room >= 480 + 60 * zoom
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Artifacts").font(.hubTitle).lineLimit(1).fixedSize()
                HStack(spacing: 6) {
                    Text(loaded ? summary : "Looking…")
                        .foregroundStyle(Palette.inkFaint)
                        .lineLimit(1)
                    if removedCount > 0 {
                        Button("· \(removedCount) removed, bring back") { stage.restoreAll() }
                            .buttonStyle(.plain)
                            .foregroundStyle(Palette.inkMuted)
                            .lineLimit(1)
                            .help("Show everything removed from the gallery again. Files already in the Trash stay there.")
                    }
                }
                .font(.zoomed(size: 11.5))
            }
            .frame(minWidth: 0, alignment: .leading)
            Spacer(minLength: 12)
            if compact {
                Picker("", selection: $filter) {
                    ForEach(MadeFilter.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .fixedSize()
                .controlSize(.small)
                if (searching || !query.isEmpty) && stage.artifact == nil {
                    SearchField(text: $query).frame(width: 150)
                }
            } else {
                Picker("", selection: $filter) {
                    ForEach(MadeFilter.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .controlSize(.small)
                SearchField(text: $query).frame(width: 170)
            }
            Button {
                if let first = shown.first(where: { if case .picture = $0.what { return true } else { return false } }) {
                    stage.deck = shown
                    stage.present(first)
                    stage.sorting = true
                }
            } label: {
                Image(systemName: "hand.draw")
                    .font(.zoomed(size: 11.5, weight: .medium))
                    .frame(width: 24, height: 22)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(Palette.inkMuted)
            .help("Edit images: go through the pictures, swiping to keep, trash or recover")
            Button { Task { await load() } } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.zoomed(size: 11.5, weight: .medium))
                    .frame(width: 24, height: 22)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(Palette.inkMuted)
            .help("Look again")
        }
        .padding(.horizontal, Metric.gutter)
        .padding(.vertical, 12)
    }

    private var removedCount: Int {
        stage.hidden.isEmpty ? 0 : items.filter { stage.hidden.contains($0.mark) }.count
    }

    private var summary: String {
        let n = shown.count
        let chats = Set(shown.map(\.conversation)).count
        if n == 0 { return "Nothing here" }
        return "\(n) \(n == 1 ? "thing" : "things") made, in \(chats) \(chats == 1 ? "chat" : "chats")"
    }

    private var empty: some View {
        VStack(spacing: 8) {
            Image(systemName: "square.on.square.dashed")
                .font(.zoomed(size: 26))
                .foregroundStyle(Palette.inkFaint)
            Text(!failed.isEmpty ? "Couldn't ask the engine"
                 : !loaded ? "Looking…"
                 : items.isEmpty ? "Nothing made yet" : "Nothing matches")
                .font(.zoomed(size: 13.5, weight: .medium))
            Text(!failed.isEmpty ? failed
                 : items.isEmpty ? "Ask for a page, a diagram or a picture — it will be kept here."
                 : "Try another filter, or fewer words.")
                .font(.zoomed(size: 12))
                .foregroundStyle(Palette.inkFaint)
                .multilineTextAlignment(.center)
        }
        .padding(.all, 40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func load() async {
        do {
            let turns = try await model.client.made()
            let found = await Task.detached(priority: .userInitiated) { MadeItem.all(in: turns) }.value
            items = found
            failed = ""
        } catch {
            failed = error.localizedDescription
        }
        loaded = true
    }

    private func open(_ item: MadeItem) {
        if case .artifact(let artifact) = item.what, stage.artifact?.id == artifact.id {
            withAnimation(.easeOut(duration: 0.18)) { stage.artifact = nil }
            return
        }
        stage.deck = shown
        stage.present(item)
    }

    private func goToChat(_ item: MadeItem) {
        stage.artifact = nil
        model.open(item.conversation)
        model.paneRequest = .chat(item.conversation)
    }
}

// MARK: - one tile

struct MadeTile: View {
    @ObservedObject private var stage = Stage.shared
    @Environment(\.colorScheme) private var scheme
    let item: MadeItem
    let open: () -> Void
    let goToChat: () -> Void
    var remove: () -> Void = {}

    @State private var thumb: NSImage?
    @State private var missing = false
    @State private var hovering = false

    private var isOpen: Bool {
        if case .artifact(let a) = item.what { return stage.artifact?.id == a.id }
        return false
    }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: Metric.radius)
        Button(action: open) { face(shape) }
            .buttonStyle(.plain)
            .onHover { hovering = $0 }
            .contextMenu { menu }
            .help("\(item.title) — from “\(item.chat)”")
            .accessibilityLabel("\(item.title), \(item.kindLabel)")
            .task(id: "\(item.id)-\(scheme == .dark)") { await draw() }
    }

    private func face(_ shape: RoundedRectangle) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            preview
                .frame(maxWidth: .infinity)
                .frame(height: 150)
                .clipped()
            Rectangle().fill(Palette.hairline).frame(height: 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(.zoomed(size: 12.5, weight: .medium))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                Text(caption)
                    .font(.zoomed(size: 11))
                    .foregroundStyle(Palette.inkFaint)
                    .lineLimit(1)
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Palette.surface, in: shape)
        .clipShape(shape)
        .overlay(shape.strokeBorder(isOpen ? Palette.accent.opacity(0.6)
                                    : hovering ? Palette.inkFaint.opacity(0.5) : Palette.hairline,
                                    lineWidth: 1))
        .contentShape(shape)
    }

    private var caption: String {
        var parts = [item.kindLabel, item.chat]
        if let when = item.when {
            parts.append(Self.ago.localizedString(for: when, relativeTo: Date()))
        }
        return parts.joined(separator: " · ")
    }

    private static let ago: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()

    @ViewBuilder private var preview: some View {
        if let thumb {
            if case .picture = item.what {
                Image(nsImage: thumb).resizable().interpolation(.high).scaledToFill()
            } else {
                Image(nsImage: thumb).resizable().interpolation(.high)
                    .scaledToFill()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }
        } else {
            ZStack {
                Palette.fill.opacity(0.55)
                Image(systemName: missing ? "photo.badge.exclamationmark" : symbol)
                    .font(.zoomed(size: 24))
                    .foregroundStyle(Palette.inkFaint)
            }
        }
    }

    private var symbol: String {
        switch item.what {
        case .artifact(let a): return a.symbol
        case .picture: return "photo"
        }
    }

    @ViewBuilder private var menu: some View {
        Button("Open") { open() }
        Button("Go to Chat") { goToChat() }
        Divider()
        switch item.what {
        case .artifact(let artifact):
            Button("Copy Source") { ArtifactActions.copy(artifact) }
            Button("Save As…") { ArtifactActions.save(artifact) }
            Button("Open in Browser") { ArtifactActions.openInBrowser(artifact) }
        case .picture(let picture):
            if let thumb {
                Button("Copy Image") { PictureActions.copy(picture, thumb) }
                Button("Save As…") { PictureActions.save(picture, thumb) }
            }
            if picture.isLocal {
                Button("Show in Finder") { PictureActions.reveal(picture) }
            }
        }
        Divider()
        Button("Remove from Gallery") { remove() }
    }

    private func draw() async {
        switch item.what {
        case .picture(let picture):
            thumb = PictureLoader.cached(picture)
            if thumb == nil {
                thumb = await PictureLoader.load(picture)
                missing = thumb == nil
            }
        case .artifact(let artifact):
            thumb = await ArtifactThumbs.shared.thumb(for: artifact, dark: scheme == .dark)
        }
    }
}

// MARK: - what a page looks like, without opening it

/// One web view, off screen, drawing one artifact at a time and keeping a
/// picture of each. Same rules as the panel: no storage, no files, nowhere to
/// navigate. A page that never settles is given up on, and its tile keeps
/// the plain symbol.
@MainActor
final class ArtifactThumbs: NSObject, WKNavigationDelegate {
    static let shared = ArtifactThumbs()

    private struct Job {
        let key: String
        let html: String
        let settle: Double
        let done: (NSImage?) -> Void
    }

    private let cache: NSCache<NSString, NSImage> = {
        let c = NSCache<NSString, NSImage>()
        c.countLimit = 300
        return c
    }()
    private var web: WKWebView?
    private var queue: [Job] = []
    private var current: Job?
    private var ticket = 0

    func thumb(for artifact: Artifact, dark: Bool) async -> NSImage? {
        let key = "\(artifact.id)-\(dark)"
        if let hit = cache.object(forKey: key as NSString) { return hit }
        return await withCheckedContinuation { waiting in
            queue.append(Job(key: key, html: artifact.document(dark: dark),
                             settle: artifact.kind == .mermaid ? 0.9 : 0.35,
                             done: { waiting.resume(returning: $0) }))
            pump()
        }
    }

    private func pump() {
        guard current == nil, !queue.isEmpty else { return }
        let job = queue.removeFirst()
        if let hit = cache.object(forKey: job.key as NSString) {     // drawn while it waited
            job.done(hit)
            return pump()
        }
        current = job
        ticket += 1
        let mine = ticket
        let view = web ?? make()
        web = view
        view.loadHTMLString(job.html, baseURL: nil)
        Task { [weak self] in                                        // never wait forever
            try? await Task.sleep(for: .seconds(8))
            self?.finish(mine, with: nil)
        }
    }

    private func make() -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 960, height: 720), configuration: config)
        view.navigationDelegate = self
        return view
    }

    private func finish(_ which: Int, with image: NSImage?) {
        guard which == ticket, let job = current else { return }
        current = nil
        if let image { cache.setObject(image, forKey: job.key as NSString) }
        job.done(image)
        pump()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let mine = ticket
        guard let job = current else { return }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(job.settle))
            guard let self, mine == self.ticket, self.current != nil else { return }
            let shot = WKSnapshotConfiguration()
            shot.snapshotWidth = 480
            webView.takeSnapshot(with: shot) { image, _ in
                Task { @MainActor in self.finish(mine, with: image) }
            }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        finish(ticket, with: nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        finish(ticket, with: nil)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        // a thumbnail goes nowhere: only the page it was handed
        if let scheme = action.request.url?.scheme?.lowercased(),
           action.targetFrame?.isMainFrame ?? true, scheme == "http" || scheme == "https" {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }
}
