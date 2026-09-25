// eki's Mac shell: a window and a menu bar item around the engine's web UI.
// Everything else — the UI itself included — lives in the engine, so a UI
// change is live on reload and this file rarely changes.
import Cocoa
import WebKit

let root = Bundle.main.object(forInfoDictionaryKey: "EkiRoot") as? String ?? NSString("~/eki-next").expandingTildeInPath
let port = ProcessInfo.processInfo.environment["EKI_PORT"] ?? (Bundle.main.object(forInfoDictionaryKey: "EkiPort") as? String ?? "7788")
let home = URL(string: "http://127.0.0.1:\(port)/")!

final class Shell: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var status: NSStatusItem!
    var retry: Timer?
    var startedEngine = false
    // Where the page says the window can be dragged from, in page coordinates
    // (top-left origin). A WKWebView swallows every mouse event, so the window
    // can't be moved by its title area unless the shell does it.
    var dragRects: [NSRect] = []
    var noDragRects: [NSRect] = []

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        // tell the page it's in the Mac window, so it leaves room for the window buttons
        config.applicationNameForUserAgent = "EkiMac"
        config.userContentController.add(self, name: "eki")
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = false
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.title = "eki"
        window.contentView = web
        window.setFrameAutosaveName("eki.main")
        if window.frame.origin == .zero { window.center() }
        window.isReleasedWhenClosed = false
        window.makeKeyAndOrderFront(nil)
        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let img = Bundle.main.image(forResource: "MenuIcon") {
            img.isTemplate = true
            img.size = NSSize(width: 18, height: 18)
            status.button?.image = img
        } else {
            status.button?.title = "eki"
        }
        let m = NSMenu()
        m.addItem(withTitle: "Open eki", action: #selector(show), keyEquivalent: "")
        m.addItem(withTitle: "New thread", action: #selector(newThread), keyEquivalent: "")
        m.addItem(.separator())
        m.addItem(withTitle: "Quit eki window", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        status.menu = m
        load()
        NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak self] e in
            guard let self = self else { return e }
            self.debug("mouse down at \(e.locationInWindow) in eki window: \(e.window === self.window) drag: \(self.inDragArea(e))")
            guard e.window === self.window, self.inDragArea(e) else { return e }
            if e.clickCount == 2 {
                self.titleDoubleClick()
            } else {
                self.window.performDrag(with: e)
            }
            return nil
        }
    }

    func debug(_ s: String) {
        guard ProcessInfo.processInfo.environment["EKI_SHELL_DEBUG"] != nil
                || FileManager.default.fileExists(atPath: NSString("~/.eki-next/shell-debug").expandingTildeInPath) else { return }
        let url = URL(fileURLWithPath: NSString("~/.eki-next/logs/shell.log").expandingTildeInPath)
        if let h = try? FileHandle(forWritingTo: url) { h.seekToEndOfFile(); h.write((s + "\n").data(using: .utf8)!); try? h.close() }
        else { try? (s + "\n").write(to: url, atomically: true, encoding: .utf8) }
    }

    func inDragArea(_ e: NSEvent) -> Bool {
        let p = web.convert(e.locationInWindow, from: nil)
        let q = web.isFlipped ? p : NSPoint(x: p.x, y: web.bounds.height - p.y)
        return dragRects.contains { $0.contains(q) } && !noDragRects.contains { $0.contains(q) }
    }

    // What a double-click on a title bar does is the person's choice (System Settings → Desktop & Dock).
    func titleDoubleClick() {
        let action = UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") ?? "Maximize"
        switch action {
        case "Minimize": window.performMiniaturize(nil)
        case "None": break
        default: window.performZoom(nil)
        }
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard m.name == "eki", let body = m.body as? [String: Any] else { return }
        func rects(_ key: String) -> [NSRect] {
            (body[key] as? [[Double]] ?? []).compactMap { r in
                r.count == 4 ? NSRect(x: r[0], y: r[1], width: r[2], height: r[3]) : nil
            }
        }
        dragRects = rects("drag")
        noDragRects = rects("nodrag")
        debug("drag areas: \(dragRects) minus \(noDragRects)")
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        show(); return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    @objc func show() { NSApp.activate(ignoringOtherApps: true); window.makeKeyAndOrderFront(nil) }
    @objc func newThread() { show(); web.load(URLRequest(url: home)) }
    @objc func reload() { web.reload() }
    func load() { web.load(URLRequest(url: home, cachePolicy: .reloadIgnoringLocalCacheData)) }

    // The engine isn't up: start it once, then keep trying.
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        if !startedEngine {
            startedEngine = true
            let p = Process()
            p.executableURL = URL(fileURLWithPath: root + "/bin/eki-next")
            p.arguments = ["engine", "start"]
            try? p.run()
        }
        w.loadHTMLString("""
          <body style="font:15px -apple-system;color:#888;display:grid;place-items:center;height:90vh;background:transparent">
          Waiting for the eki engine…</body>
          """, baseURL: nil)
        retry?.invalidate()
        retry = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: false) { [weak self] _ in self?.load() }
    }

    // Links out of eki open in the browser.
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = a.request.url, url.host != "127.0.0.1", a.navigationType == .linkActivated {
            NSWorkspace.shared.open(url); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration,
                 for a: WKNavigationAction, windowFeatures f: WKWindowFeatures) -> WKWebView? {
        if let url = a.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func buildMenu() {
        let bar = NSMenu()
        func sub(_ title: String, _ items: [NSMenuItem]) {
            let top = NSMenuItem(); let m = NSMenu(title: title)
            items.forEach { m.addItem($0) }; top.submenu = m; bar.addItem(top)
        }
        func item(_ t: String, _ a: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k); i.keyEquivalentModifierMask = mods; return i
        }
        sub("eki", [item("Hide eki", #selector(NSApplication.hide(_:)), "h"), .separator(),
                    item("Quit eki window", #selector(NSApplication.terminate(_:)), "q")])
        sub("Edit", [item("Undo", Selector(("undo:")), "z"), item("Redo", Selector(("redo:")), "z", [.command, .shift]),
                     .separator(), item("Cut", #selector(NSText.cut(_:)), "x"), item("Copy", #selector(NSText.copy(_:)), "c"),
                     item("Paste", #selector(NSText.paste(_:)), "v"), item("Select All", #selector(NSText.selectAll(_:)), "a")])
        sub("View", [item("Reload", #selector(reload), "r"), item("New thread", #selector(newThread), "n")])
        sub("Window", [item("Minimize", #selector(NSWindow.miniaturize(_:)), "m"), item("Close", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = bar
    }
}

let app = NSApplication.shared
let shell = Shell()
app.delegate = shell
app.setActivationPolicy(.regular)
app.run()
