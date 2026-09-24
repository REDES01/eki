// SPDX-License-Identifier: Apache-2.0
// Going through what was made, by hand.
//
// In the picture viewer's edit mode — and only there — two fingers on the
// trackpad, or the arrow keys:
//
//   left / right    the next picture, the one before
//   up              move this one to the Trash
//   down            recover the last one removed
//
// Nothing happens until the fingers lift, and while they are down a prompt
// says what lifting would do. Removing is hiding: the gallery forgets the
// thing, the chat that made it does not; a generated picture's file also goes
// to the Trash, and recovering means coming back out of it. Either way a line
// at the bottom of the window says what just happened, with Undo on it.
//
// Outside edit mode two fingers pan and zoom as they always did. Pages and
// diagrams have no gestures at all: their scrolling and their keys are theirs.
import AppKit
import CryptoKit
import SwiftUI
import WebKit

enum Swipe { case left, right, up, down }

/// A swipe in progress: fingers still down.
struct Pull: Equatable {
    let swipe: Swipe
    let far: CGFloat
    let armed: Bool
}

// MARK: - what is hidden

enum Hidden {
    private static let store = "eki.hiddenMade"

    static func load() -> Set<String> {
        Set(UserDefaults.standard.stringArray(forKey: store) ?? [])
    }

    static func save(_ marks: Set<String>) {
        UserDefaults.standard.set(marks.sorted(), forKey: store)
    }

    /// The same artifact must be the same artifact tomorrow, and Swift's own
    /// hashValue is reseeded every launch.
    static func mark(_ artifact: Artifact, in conversation: String) -> String {
        let digest = SHA256.hash(data: Data(artifact.source.utf8))
        let hex = digest.prefix(10).map { String(format: "%02x", $0) }.joined()
        return "a:\(conversation):\(artifact.kind.rawValue):\(hex)"
    }

    static func mark(_ picture: Picture) -> String { "p:\(picture.source)" }
}

struct Notice: Identifiable, Equatable {
    let id = UUID()
    let text: String
    var undoable = false
}

/// One removal, with everything needed to take it back.
struct Removed {
    let mark: String
    let title: String
    var item: MadeItem? = nil
    var picture: Picture? = nil
    let index: Int
    var original: URL? = nil
    var trashed: URL? = nil
}

// MARK: - walking, removing, bringing back

extension Stage {
    /// The gallery item on screen — nil when what's open came from a chat.
    var live: MadeItem? {
        guard let current else { return nil }
        switch current.what {
        case .artifact(let a): return artifact?.id == a.id && picture == nil ? current : nil
        case .picture(let p): return picture == p ? current : nil
        }
    }

    /// The pictures among what the gallery is showing: what the viewer walks.
    var reel: [MadeItem] {
        deck.filter { if case .picture = $0.what { return true } else { return false } }
    }

    var canStep: Bool { live != nil ? reel.count > 1 : gallery.count > 1 }

    /// The picture `by` steps away, without going there.
    func neighbor(_ by: Int) -> Picture? {
        if let live {
            let reel = self.reel
            guard reel.count > 1, let at = reel.firstIndex(where: { $0.id == live.id }),
                  case .picture(let p) = reel[(at + by + reel.count) % reel.count].what else { return nil }
            return p
        }
        guard let picture, gallery.count > 1, let at = gallery.firstIndex(of: picture) else { return nil }
        return gallery[(at + by + gallery.count) % gallery.count]
    }

    /// "3 of 12", in whichever list is being walked.
    var position: (Int, Int)? {
        if let live {
            let reel = self.reel
            guard reel.count > 1, let at = reel.firstIndex(where: { $0.id == live.id }) else { return nil }
            return (at + 1, reel.count)
        }
        guard let picture, gallery.count > 1, let at = gallery.firstIndex(of: picture) else { return nil }
        return (at + 1, gallery.count)
    }

    func present(_ item: MadeItem) {
        current = item
        switch item.what {
        case .artifact(let a):
            if picture != nil { withAnimation(.easeOut(duration: 0.15)) { picture = nil } }
            withAnimation(.easeOut(duration: 0.18)) { artifact = a }
        case .picture(let p):
            gallery = deck.compactMap { made -> Picture? in
                if case .picture(let q) = made.what { return q }
                return nil
            }
            if !gallery.contains(p) { gallery = [p] }
            withAnimation(.easeOut(duration: 0.18)) { artifact = nil }
            picture = p
        }
    }

    func handle(_ swipe: Swipe) {
        switch swipe {
        case .left: step(1)
        case .right: step(-1)
        case .up: remove()
        case .down: restore()
        }
    }

    /// Remove `item`, or whatever is open.
    func remove(_ item: MadeItem? = nil) {
        if let item = item ?? live {
            let at = deck.firstIndex(where: { $0.id == item.id })
            var gone = Removed(mark: item.mark, title: item.title, item: item, index: at ?? 0)
            if case .picture(let p) = item.what { trash(p, into: &gone) }
            let showing = live?.id == item.id
            let place = reel.firstIndex(where: { $0.id == item.id })
            if let at { deck.remove(at: at) }
            forget(gone)
            if showing {
                let reel = self.reel
                if let place, !reel.isEmpty { present(reel[min(place, reel.count - 1)]) } else { close() }
            }
        } else if let picture {                       // opened from a chat
            let at = gallery.firstIndex(of: picture) ?? 0
            let title = picture.alt.isEmpty ? picture.name : picture.alt
            var gone = Removed(mark: Hidden.mark(picture), title: title, picture: picture, index: at)
            trash(picture, into: &gone)
            gallery.removeAll { $0 == picture }
            forget(gone)
            if gallery.isEmpty { close() } else { self.picture = gallery[min(at, gallery.count - 1)] }
        }
    }

    func restore() {
        guard let back = undone.popLast() else {
            say("Nothing to recover")
            return
        }
        var stranded = false
        if let from = back.trashed, let to = back.original {
            do { try FileManager.default.moveItem(at: from, to: to) } catch { stranded = true }
        }
        hidden.remove(back.mark)
        Hidden.save(hidden)
        if let item = back.item {
            if !deck.contains(where: { $0.id == item.id }) {
                deck.insert(item, at: min(back.index, deck.count))
            }
            if !stranded { present(item) }
        } else if let picture = back.picture {
            if !gallery.contains(picture) { gallery.insert(picture, at: min(back.index, gallery.count)) }
            if !stranded { self.picture = picture }
        }
        say(stranded ? "“\(back.title)” is back in the gallery, but its file couldn't leave the Trash"
                     : "Brought back “\(back.title)”")
    }

    /// Un-hide everything. Files already in the Trash stay there.
    func restoreAll() {
        let n = hidden.count
        hidden = []
        Hidden.save(hidden)
        undone.removeAll { $0.trashed == nil }
        say("Brought back \(n) \(n == 1 ? "thing" : "things")")
    }

    func say(_ text: String, undoable: Bool = false) {
        let notice = Notice(text: text, undoable: undoable)
        withAnimation(.easeOut(duration: 0.16)) { self.notice = notice }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(undoable ? 6 : 3))
            guard let self, self.notice?.id == notice.id else { return }
            withAnimation(.easeOut(duration: 0.2)) { self.notice = nil }
        }
    }

    private func forget(_ gone: Removed) {
        hidden.insert(gone.mark)
        Hidden.save(hidden)
        undone.append(gone)
        if undone.count > 50 { undone.removeFirst(undone.count - 50) }
        say(gone.trashed != nil ? "Moved “\(gone.title)” to the Trash — swipe down to recover"
                                : "Removed “\(gone.title)” — swipe down to recover", undoable: true)
    }

    private func trash(_ picture: Picture, into gone: inout Removed) {
        guard picture.isLocal, let url = picture.url,
              FileManager.default.fileExists(atPath: url.path) else { return }
        var landed: NSURL?
        if (try? FileManager.default.trashItem(at: url, resultingItemURL: &landed)) != nil {
            gone.original = url
            gone.trashed = landed as URL?
        }
    }

    private func close() {
        withAnimation(.easeOut(duration: 0.15)) { picture = nil; artifact = nil }
        current = nil
        sorting = false
    }
}

// MARK: - the prompt under the fingers

extension Stage {
    /// What is on screen leans the way it is being pulled.
    var lean: CGSize {
        guard let pull else { return .zero }
        switch pull.swipe {
        case .left, .right: if !canStep { return .zero }
        case .up, .down: if promise(pull.swipe) == nil { return .zero }
        }
        let d = min(pull.far, 260) * 0.5
        switch pull.swipe {
        case .left: return CGSize(width: -pull.far, height: 0)
        case .right: return CGSize(width: pull.far, height: 0)
        case .up: return CGSize(width: 0, height: -d)
        case .down: return CGSize(width: 0, height: d)
        }
    }

    /// What letting go would do, in words. nil: nothing, so say nothing.
    func promise(_ swipe: Swipe) -> (symbol: String, armed: String, idle: String)? {
        switch swipe {
        case .left:
            return nil                              // the next picture sliding in says it
        case .right:
            return nil
        case .up:
            var file = false
            if let shown = picture, live != nil || artifact == nil, shown.isLocal, let url = shown.url {
                file = FileManager.default.fileExists(atPath: url.path)
            }
            guard live != nil || picture != nil else { return nil }
            return file ? ("trash", "Release to move to Trash", "Pull up to move to Trash")
                        : ("trash", "Release to remove from gallery", "Pull up to remove")
        case .down:
            guard let last = undone.last else {
                return ("arrow.uturn.backward", "Nothing to recover", "Nothing to recover")
            }
            return ("arrow.uturn.backward", "Release to recover “\(last.title)”", "Pull down to recover “\(last.title)”")
        }
    }
}

struct PullPrompt: View {
    @ObservedObject private var stage = Stage.shared

    var body: some View {
        if let pull = stage.pull, let words = stage.promise(pull.swipe) {
            let hot = pull.armed && pull.swipe == .up
            HStack(spacing: Space.s) {
                Image(systemName: words.symbol).font(.hubHeading.weighted(.semibold))
                Text(pull.armed ? words.armed : words.idle)
                    .font(.hubRow.weighted(pull.armed ? .semibold : .regular))
                    .lineLimit(1)
            }
            .foregroundStyle(Palette.onScrim.opacity(pull.armed ? 1 : 0.75))
            .padding(.horizontal, Space.l)
            .padding(.vertical, Space.m)
            .background(hot ? Color(red: 0.80, green: 0.22, blue: 0.18).opacity(0.95)
                            : Palette.scrim.opacity(pull.armed ? 0.85 : 0.6), in: Capsule())
            .overlay(Capsule().strokeBorder(Palette.onScrim.opacity(0.14), lineWidth: 1))
            .scaleEffect(pull.armed ? 1.06 : 1)
            .animation(.easeOut(duration: 0.12), value: pull.armed)
            .padding(.all, Space.xl)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: place(pull.swipe))
            .allowsHitTesting(false)
            .transition(.opacity)
        }
    }

    /// The prompt waits where the content is heading.
    private func place(_ swipe: Swipe) -> Alignment {
        switch swipe {
        case .up: return .top
        case .down: return .bottom
        case .left: return .trailing
        case .right: return .leading
        }
    }
}

// MARK: - the line at the bottom of the window

struct NoticeView: View {
    @ObservedObject private var stage = Stage.shared

    var body: some View {
        if let notice = stage.notice {
            HStack(spacing: Space.m) {
                Text(notice.text)
                    .font(.hubCallout)
                    .lineLimit(2)
                if notice.undoable {
                    Button("Undo") { stage.restore() }
                        .buttonStyle(.plain)
                        .font(.hubCallout.weighted(.semibold))
                        .foregroundStyle(Palette.accent)
                    Text("↓")
                        .font(.hubCaption.weighted(.medium).monospaced())
                        .padding(.horizontal, Space.xs).padding(.vertical, 1)
                        .background(Palette.onScrim.opacity(0.14), in: RoundedRectangle(cornerRadius: 4))
                        .help("Swipe down with two fingers, or press ↓")
                }
            }
            .foregroundStyle(Palette.onScrim)
            .padding(.horizontal, Space.l)
            .padding(.vertical, Space.s)
            .background(Palette.scrim.opacity(0.82), in: Capsule())
            .overlay(Capsule().strokeBorder(Palette.onScrim.opacity(0.12), lineWidth: 1))
            .raised()
            .padding(.bottom, 54)
            .padding(.horizontal, Space.xl)
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .id(notice.id)
            .accessibilityLabel(notice.text)
        }
    }
}

// MARK: - reading the fingers

/// Sits behind a view and listens, over that view's area, for two-finger
/// swipes — and, when asked, for bare arrow keys.
struct SwipeCatcher: NSViewRepresentable {
    var keys = false
    /// Changes when the thing on screen does; the catcher takes the keyboard
    /// back then, so arrows keep walking after a click into a page.
    var focus = ""
    var active: () -> Bool = { true }
    let act: (Swipe) -> Void
    var pulling: (Pull?) -> Void = { Stage.shared.pull = $0 }

    func makeNSView(context: Context) -> SwipeView { SwipeView() }

    func updateNSView(_ view: SwipeView, context: Context) {
        view.keys = keys
        view.active = active
        view.act = act
        view.pulling = pulling
        if keys, view.focus != focus {
            view.focus = focus
            DispatchQueue.main.async { [weak view] in
                guard let view, let window = view.window, view.active() else { return }
                window.makeFirstResponder(view)
            }
        }
    }

    static func dismantleNSView(_ view: SwipeView, coordinator: ()) { view.stopListening() }
}

final class SwipeView: NSView {
    var keys = false
    var focus = "\u{0}"
    var active: () -> Bool = { true }
    var act: (Swipe) -> Void = { _ in }
    var pulling: (Pull?) -> Void = { _ in }

    private var monitors: [Any] = []
    private var tracking = false
    private var armed: Swipe?
    private var lapse: DispatchWorkItem?
    private var x: CGFloat = 0
    private var y: CGFloat = 0
    private var gesture = 0
    /// Can the content under the fingers scroll that way? nil: still asking.
    private var scrollsAcross: Bool? = false
    private var scrollsAlong: Bool? = false
    /// A scroll view that starts scrolling takes the rest of the gesture into
    /// a loop of its own, where no monitor sees it — it rubber-bands, and we
    /// never learn how far the fingers went. So when the content can't scroll
    /// the way the fingers set off, the gesture is kept from it entirely.
    private var keeping = false
    private var asked = 0
    private var probedAt: TimeInterval = 0
    private weak var probedView: NSView?

    private static let across: CGFloat = 110       // points of travel that make a swipe
    private static let along: CGFloat = 130        // removing takes a little more meaning it

    override var acceptsFirstResponder: Bool { keys }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        stopListening()
        guard window != nil else { return }
        if let m = NSEvent.addLocalMonitorForEvents(matching: .scrollWheel, handler: { [weak self] event in
            (self?.scrolled(event) ?? false) ? nil : event
        }) { monitors.append(m) }
        if let m = NSEvent.addLocalMonitorForEvents(matching: .keyDown, handler: { [weak self] event in
            (self?.pressed(event) ?? false) ? nil : event
        }) { monitors.append(m) }
    }

    func stopListening() {
        letGo(acting: false)
        monitors.forEach(NSEvent.removeMonitor)
        monitors = []
    }

    // MARK: keys

    private func pressed(_ event: NSEvent) -> Bool {
        guard keys, let window, event.window === window, window.attachedSheet == nil, active(),
              event.modifierFlags.intersection([.command, .option, .control, .shift]).isEmpty
        else { return false }
        // arrows typed into a field, or into a page that was clicked, are theirs
        if let responder = window.firstResponder {
            if responder is NSText { return false }
            var view = responder as? NSView
            while let v = view {
                if v is WKWebView { return false }
                view = v.superview
            }
        }
        switch event.keyCode {
        case 123: act(.right)                       // ← the one before
        case 124: act(.left)                        // → the next
        case 126: act(.up)
        case 125: act(.down)
        default: return false
        }
        return true
    }

    // MARK: fingers

    /// `defaults write local.eki.app eki.swipeDebug -bool YES` writes what the
    /// catcher saw to ~/.eki/swipe.log.
    private static let debugging = UserDefaults.standard.bool(forKey: "eki.swipeDebug")
    private func note(_ text: @autoclosure () -> String) {
        guard Self.debugging else { return }
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".eki/swipe.log")
        let line = "\(Date()) [\(keys ? "panel" : "viewer")] \(text())\n"
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile(); handle.write(Data(line.utf8)); try? handle.close()
        } else { try? line.write(to: url, atomically: true, encoding: .utf8) }
    }

    /// true: the event stops here.
    private func scrolled(_ event: NSEvent) -> Bool {
        guard let window, event.window === window else { return false }
        let inside = { [self] in
            bounds.contains(convert(event.locationInWindow, from: nil))
                && window.attachedSheet == nil && active()
        }
        if event.phase.contains(.mayBegin) {        // fingers down, not moving yet: ask early
            if inside() { probe(event.locationInWindow) }
            return false
        }
        if !event.momentumPhase.isEmpty {           // the coast after a swipe we kept
            let mine = keeping
            if event.momentumPhase.contains(.ended) || event.momentumPhase.contains(.cancelled) {
                keeping = false
            }
            return mine
        }
        if event.phase.contains(.began) {
            tracking = inside()
            keeping = false
            armed = nil
            x = 0
            y = 0
            gesture += 1
            guard tracking else { return false }
            probe(event.locationInWindow)
            let ax = abs(event.scrollingDeltaX), ay = abs(event.scrollingDeltaY)
            if scrollsAcross == false, scrollsAlong == false {
                keeping = true
            } else if ax > ay {
                keeping = scrollsAcross == false
            } else if ay > ax {
                keeping = scrollsAlong == false
            }
            note("began keeping=\(keeping) across=\(String(describing: scrollsAcross)) along=\(String(describing: scrollsAlong)) d=(\(event.scrollingDeltaX), \(event.scrollingDeltaY)) under=\(probedView.map { String(describing: type(of: $0)) } ?? "-")")
        }
        guard tracking else { return false }
        if event.phase.contains(.changed) || event.phase.contains(.began) {
            // in the direction the fingers went, whatever "natural" is set to
            let sign: CGFloat = event.isDirectionInvertedFromDevice ? 1 : -1
            x += event.scrollingDeltaX * sign
            y += event.scrollingDeltaY * sign
            decide()
        }
        if event.phase.contains(.ended) { letGo(acting: true) }
        else if event.phase.contains(.cancelled) { letGo(acting: false) }
        return keeping
    }

    /// Where the fingers have got to: which way, how far, and whether
    /// letting go now would do it. Nothing happens until they let go.
    private func decide() {
        let ax = abs(x), ay = abs(y)
        var want: Swipe?
        var far: CGFloat = 0
        var need: CGFloat = 1
        if ax > 10, ax > ay * 1.5, scrollsAcross == false {
            want = x < 0 ? .left : .right
            far = ax
            need = Self.across
        } else if ay > 10, ay > ax * 1.5, scrollsAlong == false {
            want = y < 0 ? .up : .down
            far = ay
            need = Self.along
        }
        armed = want != nil && far >= need ? want : nil
        pulling(want.map { Pull(swipe: $0, far: far, armed: far >= need) })
        // a gesture whose end we never get to see must not leave the prompt up
        // (a kept gesture always shows its end, and fingers may rest as long as they like)
        lapse?.cancel()
        lapse = nil
        guard !keeping else { return }
        let work = DispatchWorkItem { [weak self] in self?.letGo(acting: false) }
        lapse = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 8, execute: work)
    }

    private func letGo(acting: Bool) {
        lapse?.cancel()
        lapse = nil
        let swipe = armed
        if tracking { note("let go acting=\(acting) armed=\(String(describing: swipe)) travelled=(\(x), \(y))") }
        armed = nil
        tracking = false
        pulling(nil)
        if acting, let swipe { act(swipe) }
    }

    /// What is under the fingers, and which ways can it scroll?
    private func probe(_ point: NSPoint) {
        let under = window?.contentView?.hitTest(point)
        // asked a moment ago, about the same spot: the answer stands
        if let under, under === probedView, scrollsAcross != nil, scrollsAlong != nil,
           ProcessInfo.processInfo.systemUptime - probedAt < 1.5 { return }
        probedView = under
        probedAt = ProcessInfo.processInfo.systemUptime
        scrollsAcross = false
        scrollsAlong = false
        let mine = convert(bounds, to: nil).insetBy(dx: -2, dy: -2)
        var view = under
        while let v = view {
            if let web = v as? WKWebView {
                ask(web, at: web.convert(point, from: nil))
                return
            }
            if let scroll = v as? NSScrollView, let doc = scroll.documentView,
               mine.contains(scroll.convert(scroll.bounds, to: nil)) {
                let room = scroll.contentView.bounds.size
                scrollsAcross = doc.frame.width > room.width + 1
                scrollsAlong = doc.frame.height > room.height + 1
                return
            }
            view = v.superview
        }
    }

    private func ask(_ web: WKWebView, at point: NSPoint) {
        scrollsAcross = nil
        scrollsAlong = nil
        let zoom = max(web.magnification, 0.01)
        let px = point.x / zoom
        let py = (web.isFlipped ? point.y : web.bounds.height - point.y) / zoom
        let js = """
        (function (x, y) {
          var e = document.elementFromPoint(x, y) || document.body, h = 0, v = 0;
          var root = document.scrollingElement || document.documentElement;
          for (; e && e.nodeType === 1; e = e.parentElement) {
            var s = getComputedStyle(e), top = e === root;
            var ox = s.overflowX, oy = s.overflowY;
            if ((top ? ox !== 'hidden' : /auto|scroll/.test(ox)) && e.scrollWidth > e.clientWidth + 1) h = 1;
            if ((top ? oy !== 'hidden' : /auto|scroll/.test(oy)) && e.scrollHeight > e.clientHeight + 1) v = 1;
          }
          if (root.scrollWidth > root.clientWidth + 1 && getComputedStyle(root).overflowX !== 'hidden') h = 1;
          if (root.scrollHeight > root.clientHeight + 1 && getComputedStyle(root).overflowY !== 'hidden') v = 1;
          return h + 2 * v;
        })(\(px), \(py))
        """
        asked += 1
        let mine = asked
        web.evaluateJavaScript(js) { [weak self] result, _ in
            DispatchQueue.main.async {
                guard let self, mine == self.asked else { return }
                // no answer means a page we can't read: leave its scrolling alone
                let bits = (result as? NSNumber)?.intValue ?? 3
                self.scrollsAcross = bits & 1 != 0
                self.scrollsAlong = bits & 2 != 0
            }
        }
    }
}
