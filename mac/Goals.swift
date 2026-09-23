// SPDX-License-Identifier: Apache-2.0
// Goals: the review board the engine serves at /goals, embedded.
//
// The board is a page the engine draws (eki/web/goals.html), so the app, a
// browser and the command line all see the same thing — the app just gives it
// a place in the rail. Links that leave the engine open in the browser.
import SwiftUI
import WebKit

struct GoalsPane: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Group {
            if model.engine == .up {
                EngineWeb(path: "/goals")
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "square.grid.2x2").font(.system(size: 28)).foregroundStyle(.secondary)
                    Text("The engine isn't running").font(.headline)
                    Text("The board is drawn by the engine — start it and this fills in.")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
}

/// A page the engine serves on loopback, shown in place.
struct EngineWeb: NSViewRepresentable {
    let path: String
    var port: Int = 8787

    func makeCoordinator() -> Coordinator { Coordinator(port: port) }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // the board's Choose… button: AppKit's own folder panel, at once, as a
        // sheet on this window (a browser gets the engine's dialog instead)
        config.userContentController.addScriptMessageHandler(context.coordinator, contentWorld: .page,
                                                             name: "ekiPickFolder")
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.allowsMagnification = true
        web.setValue(false, forKey: "drawsBackground")
        if let url = URL(string: "http://127.0.0.1:\(port)\(path)") {
            web.load(URLRequest(url: url))
        }
        return web
    }

    func updateNSView(_ web: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
        let port: Int
        init(port: Int) { self.port = port }

        private func isEngine(_ url: URL) -> Bool {
            url.host == "127.0.0.1" && url.port == port
        }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { return decisionHandler(.cancel) }
            if isEngine(url) || url.scheme == "about" { return decisionHandler(.allow) }
            // anything else belongs in the browser, not in the pane
            if ["http", "https", "mailto"].contains(url.scheme?.lowercased() ?? "") {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url { NSWorkspace.shared.open(url) }
            return nil
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage,
                                   replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void) {
            let from = ((message.body as? [String: Any])?["from"] as? String) ?? ""
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.allowsMultipleSelection = false
            panel.canCreateDirectories = true
            panel.prompt = "Choose"
            panel.message = "Choose a folder for eki to work in"
            // from what's typed in, if it's a folder; else home
            let start = (from as NSString).expandingTildeInPath
            var isDir: ObjCBool = false
            let given = !from.isEmpty && FileManager.default.fileExists(atPath: start, isDirectory: &isDir)
                && isDir.boolValue
            panel.directoryURL = URL(fileURLWithPath: given ? start : NSHomeDirectory())
            if let window = message.webView?.window {
                panel.beginSheetModal(for: window) { answer in
                    replyHandler(answer == .OK ? (panel.url?.path ?? "") : "", nil)
                }
            } else {
                replyHandler(panel.runModal() == .OK ? (panel.url?.path ?? "") : "", nil)
            }
        }

        /// the engine restarting under the page: try again shortly
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { webView.reload() }
        }
    }
}
