// SPDX-License-Identifier: Apache-2.0
// Artifacts: the things an answer *made*, opened beside the chat.
//
// A model asked for a page, a drawing or a diagram answers with a fence of
// source. Reading four hundred lines of HTML in the transcript is not what
// anyone wanted; seeing the page is. So a fence that is html, svg or mermaid
// collapses to a card, and the card opens a panel on the right that runs it —
// with the source one click away, because sometimes the source is the point.
//
// The page runs in a web view with no storage, no access to files, and no way
// to navigate the panel somewhere else: a link opens in your browser.
import AppKit
import SwiftUI
import WebKit

struct Artifact: Identifiable, Hashable {
    enum Kind: String { case html, svg, mermaid }

    let kind: Kind
    let source: String

    var id: String { "\(kind.rawValue)-\(source.count)-\(source.hashValue)" }

    /// nil for a fence that is just code: most are.
    init?(code: String, language: String) {
        let lang = language.lowercased().split(separator: " ").first.map(String.init) ?? ""
        let head = code.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300).lowercased()
        let drawn = head.hasPrefix("<svg") || (head.hasPrefix("<?xml") && head.contains("<svg"))
        let kind: Kind
        if lang == "mermaid" {
            kind = .mermaid
        } else if lang == "svg" || (["", "xml", "html"].contains(lang) && drawn) {
            kind = .svg
        } else if ["html", "htm", ""].contains(lang) {
            // a whole page, not a three-line snippet in an explanation of <div>
            let page = head.hasPrefix("<!doctype html") || head.hasPrefix("<html")
                || code.range(of: "<body", options: .caseInsensitive) != nil
            let lines = code.split(separator: "\n", omittingEmptySubsequences: false).count
            let built = lang != "" && lines >= 12 && (code.contains("<style") || code.contains("<script"))
            guard page || built else { return nil }
            kind = .html
        } else {
            return nil
        }
        guard code.trimmingCharacters(in: .whitespacesAndNewlines).count > 10 else { return nil }
        self.kind = kind
        self.source = code
    }

    var title: String {
        if kind != .mermaid, let t = Self.between(source, "<title>", "</title>"), !t.isEmpty {
            return t
        }
        switch kind {
        case .html: return Self.between(source, "<h1", "</h1>").flatMap(Self.stripTag) ?? "Page"
        case .svg: return "Drawing"
        case .mermaid:
            let first = source.split(whereSeparator: \.isNewline)
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .first { !$0.isEmpty && !$0.hasPrefix("%%") && !$0.hasPrefix("---") } ?? ""
            let word = first.split(separator: " ").first.map(String.init) ?? "diagram"
            let names = ["graph": "Flowchart", "flowchart": "Flowchart", "sequencediagram": "Sequence diagram",
                         "classdiagram": "Class diagram", "statediagram": "State diagram",
                         "statediagram-v2": "State diagram", "erdiagram": "ER diagram",
                         "gantt": "Gantt chart", "pie": "Pie chart", "journey": "Journey",
                         "mindmap": "Mind map", "timeline": "Timeline", "gitgraph": "Git graph"]
            return names[word.lowercased()] ?? "Diagram"
        }
    }

    var kindLabel: String {
        switch kind {
        case .html: return "HTML"
        case .svg: return "SVG"
        case .mermaid: return "Mermaid"
        }
    }

    var symbol: String {
        switch kind {
        case .html: return "macwindow"
        case .svg: return "scribble.variable"
        case .mermaid: return "point.3.connected.trianglepath.dotted"
        }
    }

    var fileExtension: String {
        switch kind {
        case .html: return "html"
        case .svg: return "svg"
        case .mermaid: return "mmd"
        }
    }

    var lineCount: Int { source.split(separator: "\n", omittingEmptySubsequences: false).count }

    /// What the web view loads.
    func document(dark: Bool) -> String {
        let ink = dark ? "#F3F2EC" : "#1F1E1B"
        let canvas = dark ? "#232322" : "#FAF9F5"
        let frame = """
        <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          html, body { margin: 0; height: 100%; background: \(canvas); color: \(ink);
                       font: 13px -apple-system, system-ui, sans-serif; }
          .stage { min-height: 100%; box-sizing: border-box; padding: 24px;
                   display: flex; align-items: center; justify-content: center; }
          .stage > svg { max-width: 100%; height: auto; }
          .stage > pre.mermaid { width: 100%; margin: 0; text-align: center; }
          .stage > pre.mermaid svg { max-height: calc(100vh - 48px); }
          .oops { white-space: pre-wrap; font: 12px ui-monospace, monospace; opacity: .75; }
        </style>
        """
        switch kind {
        case .html:
            return source
        case .svg:
            return "<!doctype html><html><head>\(frame)</head><body><div class=\"stage\">\(source)</div></body></html>"
        case .mermaid:
            let escaped = source.replacingOccurrences(of: "&", with: "&amp;")
                .replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;")
            return """
            <!doctype html><html><head>\(frame)</head><body>
            <div class="stage"><pre class="mermaid" id="d">\(escaped)</pre></div>
            \(Mermaid.scriptTag)
            <script>
              const box = document.getElementById('d'), src = box.textContent;
              function fail(why) { box.className = 'oops'; box.textContent = why + '\\n\\n' + src; }
              if (!window.mermaid) { fail('Mermaid could not be loaded (offline?). The source:'); }
              else {
                mermaid.initialize({ startOnLoad: false, theme: '\(dark ? "dark" : "neutral")',
                                     securityLevel: 'strict', fontFamily: '-apple-system, system-ui',
                                     themeVariables: { edgeLabelBackground: '\(canvas)' } });
                mermaid.run({ nodes: [box] }).catch(e => fail(String(e && e.message || e)));
              }
            </script></body></html>
            """
        }
    }

    private static func between(_ text: String, _ open: String, _ close: String) -> String? {
        guard let a = text.range(of: open, options: .caseInsensitive),
              let b = text.range(of: close, options: .caseInsensitive, range: a.upperBound..<text.endIndex)
        else { return nil }
        return String(text[a.upperBound..<b.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// "<h1 class=x>Hello <b>you</b>" arrives as " class=x>Hello <b>you</b>"
    private static func stripTag(_ inner: String) -> String? {
        guard let gt = inner.firstIndex(of: ">") else { return nil }
        let text = String(inner[inner.index(after: gt)...])
            .replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : String(text.prefix(60))
    }
}

/// Mermaid ships inside the app when the build could fetch it (build_app.sh);
/// otherwise the panel asks the CDN, and says so plainly when it can't.
enum Mermaid {
    static let scriptTag: String = {
        if let url = Bundle.main.url(forResource: "mermaid.min", withExtension: "js"),
           let js = try? String(contentsOf: url, encoding: .utf8) {
            return "<script>\(js.replacingOccurrences(of: "</script", with: "<\\/script"))</script>"
        }
        return "<script src=\"https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js\"></script>"
    }()
}

// MARK: - the card in the transcript

struct ArtifactCard: View {
    @ObservedObject var stage = Stage.shared
    let artifact: Artifact
    @State private var hovering = false

    private var isOpen: Bool { stage.artifact?.id == artifact.id }

    var body: some View {
        Button {
            withAnimation(.easeOut(duration: 0.18)) {
                stage.artifact = isOpen ? nil : artifact
            }
        } label: {
            HStack(spacing: 11) {
                Image(systemName: artifact.symbol)
                    .font(.system(size: 15))
                    .foregroundStyle(isOpen ? Palette.accent : Palette.inkMuted)
                    .frame(width: 34, height: 34)
                    .background(Palette.fill, in: RoundedRectangle(cornerRadius: 7))
                VStack(alignment: .leading, spacing: 2) {
                    Text(artifact.title)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Palette.ink)
                        .lineLimit(1)
                    Text("\(artifact.kindLabel) · \(artifact.lineCount) lines")
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.inkFaint)
                }
                Spacer(minLength: 12)
                Text(isOpen ? "Close" : "Open")
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundStyle(Palette.inkMuted)
                Image(systemName: "sidebar.right")
                    .font(.system(size: 11.5))
                    .foregroundStyle(Palette.inkFaint)
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 9)
            .frame(maxWidth: 420, alignment: .leading)
            .background(hovering || isOpen ? Palette.fill.opacity(0.75) : Palette.surface,
                        in: RoundedRectangle(cornerRadius: Metric.radius))
            .overlay(RoundedRectangle(cornerRadius: Metric.radius)
                .strokeBorder(isOpen ? Palette.accent.opacity(0.55) : Palette.hairline, lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: Metric.radius))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .contextMenu {
            Button("Copy Source") { ArtifactActions.copy(artifact) }
            Button("Save As…") { ArtifactActions.save(artifact) }
            Button("Open in Browser") { ArtifactActions.openInBrowser(artifact) }
        }
    }
}

enum ArtifactActions {
    static func copy(_ artifact: Artifact) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(artifact.source, forType: .string)
    }

    static func save(_ artifact: Artifact) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = fileName(artifact)
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        try? artifact.source.write(to: url, atomically: true, encoding: .utf8)
    }

    /// The real browser, for devtools or a bigger window. Mermaid goes as the
    /// page that draws it, since no browser opens a .mmd.
    static func openInBrowser(_ artifact: Artifact) {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("eki-artifacts")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let asPage = artifact.kind == .mermaid
        let name = asPage ? (fileName(artifact) as NSString).deletingPathExtension + ".html"
                          : fileName(artifact)
        let url = dir.appendingPathComponent(name)
        let text = asPage ? artifact.document(dark: false) : artifact.source
        guard (try? text.write(to: url, atomically: true, encoding: .utf8)) != nil else { return }
        NSWorkspace.shared.open(url)
    }

    private static func fileName(_ artifact: Artifact) -> String {
        let safe = artifact.title.lowercased()
            .replacingOccurrences(of: "[^a-z0-9]+", with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        return (safe.isEmpty ? "artifact" : String(safe.prefix(40))) + "." + artifact.fileExtension
    }
}

// MARK: - the panel

struct ArtifactPanel: View {
    @ObservedObject var stage = Stage.shared
    @Environment(\.colorScheme) private var scheme
    let artifact: Artifact

    @State private var showingCode = false
    @State private var reloads = 0
    @State private var copied = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(Palette.hairline).frame(height: 1)
            if showingCode {
                ScrollView([.vertical, .horizontal]) {
                    Text(artifact.source)
                        .font(.hubMono)
                        .textSelection(.enabled)
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .background(Palette.surface)
            } else {
                ArtifactWeb(html: artifact.document(dark: scheme == .dark),
                            key: "\(artifact.id)-\(scheme == .dark)-\(reloads)")
                    .background(artifact.kind == .html ? Color.white : Palette.canvas)
            }
        }
        .background(Palette.canvas)
    }

    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: artifact.symbol)
                .font(.system(size: 12))
                .foregroundStyle(Palette.inkMuted)
            Text(artifact.title)
                .font(.system(size: 12.5, weight: .semibold))
                .lineLimit(1)
            Spacer(minLength: 8)
            Picker("", selection: $showingCode) {
                Text("Preview").tag(false)
                Text("Code").tag(true)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .controlSize(.small)

            icon("arrow.clockwise", "Run again") { reloads += 1; showingCode = false }
            icon(copied ? "checkmark" : "doc.on.doc", "Copy source") {
                ArtifactActions.copy(artifact)
                copied = true
                Task { try? await Task.sleep(for: .seconds(1.2)); copied = false }
            }
            icon("square.and.arrow.down", "Save As…") { ArtifactActions.save(artifact) }
            icon("safari", "Open in browser") { ArtifactActions.openInBrowser(artifact) }
            icon("xmark", "Close") {
                withAnimation(.easeOut(duration: 0.18)) { stage.artifact = nil }
            }
        }
        .padding(.leading, 14)
        .padding(.trailing, 8)
        .frame(height: 40)
    }

    private func icon(_ symbol: String, _ help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 11.5, weight: .medium))
                .frame(width: 24, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Palette.inkMuted)
        .help(help)
    }
}

/// The handle between the chat and the panel.
struct PanelHandle: View {
    @Binding var width: Double
    let limit: ClosedRange<Double>
    @State private var start: Double?

    var body: some View {
        Rectangle().fill(Palette.hairline).frame(width: 1)
            .overlay(Color.clear.frame(width: 9).contentShape(Rectangle())
                .onHover { over in
                    if over { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
                }
                .gesture(DragGesture(minimumDistance: 1, coordinateSpace: .global)
                    .onChanged { drag in
                        let from = start ?? width
                        start = from
                        width = min(limit.upperBound, max(limit.lowerBound, from - drag.translation.width))
                    }
                    .onEnded { _ in start = nil }))
    }
}

struct ArtifactWeb: NSViewRepresentable {
    let html: String
    let key: String                 // changes when the page should be loaded afresh

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()      // nothing a page stores outlives it
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.allowsMagnification = true
        web.setValue(false, forKey: "drawsBackground")
        return web
    }

    func updateNSView(_ web: WKWebView, context: Context) {
        guard context.coordinator.loaded != key else { return }
        context.coordinator.loaded = key
        // no base URL: the page's origin is nothing, so it can reach no file
        web.loadHTMLString(html, baseURL: nil)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        var loaded = ""

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            // the panel shows the artifact and only the artifact; a link the
            // person clicks belongs in their browser
            if action.navigationType == .linkActivated, let url = action.request.url,
               ["http", "https", "mailto"].contains(url.scheme?.lowercased() ?? "") {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            if action.targetFrame?.isMainFrame ?? true, let url = action.request.url,
               let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" {
                decisionHandler(.cancel)                // scripted top-level navigation
                return
            }
            decisionHandler(.allow)
        }

        /// target=_blank and window.open
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if action.navigationType == .linkActivated, let url = action.request.url,
               ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                NSWorkspace.shared.open(url)
            }
            return nil
        }
    }
}
