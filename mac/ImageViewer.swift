// SPDX-License-Identifier: Apache-2.0
// One picture, properly: the whole window, pinch or scroll to zoom, drag to
// pan, arrows for the other pictures, Esc to leave. Edit mode (E) turns two
// fingers into sorting: sideways to browse, up to the Trash, down to recover.
//
// Zooming is an NSScrollView's, not a SwiftUI gesture's: it already knows how
// to magnify about the cursor, rubber-band and pan with momentum, and every
// hand-rolled version of that is worse.
import AppKit
import SwiftUI

struct PictureViewer: View {
    @ObservedObject var stage = Stage.shared
    let picture: Picture

    @State private var image: NSImage?
    @State private var missing = false
    @State private var copied = false
    @StateObject private var zoom = ZoomController()

    // the reel: the pictures either side ride along with this one
    @State private var before: NSImage?
    @State private var after: NSImage?
    @State private var slide: CGSize = .zero
    @State private var fade: Double = 1
    @State private var cover: NSImage?          // holds the frame while the new picture settles
    @State private var busy = false

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            let at = CGSize(width: stage.lean.width + slide.width, height: stage.lean.height + slide.height)
            ZStack {
                Color.black.opacity(0.88).ignoresSafeArea()
                    .onTapGesture { close() }
                SwipeCatcher(active: { Stage.shared.sorting },
                             act: { perform($0, size: geo.size) },
                             pulling: { pull in
                                 if pull == nil {
                                     withAnimation(.spring(response: 0.3, dampingFraction: 0.86)) { stage.pull = nil }
                                 } else { stage.pull = pull }
                             })
                    .allowsHitTesting(false)

                if let before { still(before).offset(x: at.width - width) }
                if let after { still(after).offset(x: at.width + width) }

                if let image {
                    ZoomableImage(image: image, controller: zoom)
                        .id(picture.id)
                        .padding(.top, 46)
                        .offset(at)
                        .opacity(fade)
                } else if missing {
                    Text("This picture isn't where it was\n\(picture.source)")
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.white.opacity(0.7))
                } else {
                    ProgressView().controlSize(.large)
                }
                if let cover { still(cover) }

                VStack(spacing: 8) {
                    bar
                    Spacer()
                    if !picture.alt.isEmpty {
                        Text(picture.alt)
                            .font(.zoomed(size: 12))
                            .foregroundStyle(.white.opacity(0.8))
                            .lineLimit(2)
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .background(.black.opacity(0.45), in: Capsule())
                    }
                    if stage.sorting { legend }
                }
                .padding(.bottom, 16)

                if stage.canStep {
                    HStack {
                        arrow("chevron.left") { perform(.right, size: geo.size) }
                        Spacer()
                        arrow("chevron.right") { perform(.left, size: geo.size) }
                    }
                    .padding(.horizontal, 14)
                }

                PullPrompt().padding(.top, 46)

                shortcuts(geo.size)
            }
            .clipped()
        }
        .environment(\.colorScheme, .dark)
        .task(id: picture.id) {
            missing = false
            image = PictureLoader.cached(picture)
            if image == nil {
                image = await PictureLoader.load(picture)
                missing = image == nil
            }
        }
        .task(id: "\(picture.id)|\(stage.position?.1 ?? 0)") {
            before = nil
            after = nil
            if let p = stage.neighbor(-1) { before = await PictureLoader.load(p) }
            if let p = stage.neighbor(1) { after = await PictureLoader.load(p) }
        }
        .transition(.opacity)
    }

    /// A picture as the viewer would fit it, without the machinery: what
    /// slides in from the side.
    private func still(_ image: NSImage) -> some View {
        Image(nsImage: image)
            .resizable()
            .interpolation(.high)
            .scaledToFit()
            .frame(maxWidth: image.size.width, maxHeight: image.size.height)
            .padding(.all, 28)
            .padding(.top, 46)
            .allowsHitTesting(false)
    }

    private var legend: some View {
        HStack(spacing: 14) {
            key("←  →", "browse")
            key("↑", "move to Trash")
            key("↓", "recover")
            Text("two fingers, or the arrow keys")
                .foregroundStyle(.white.opacity(0.45))
        }
        .font(.zoomed(size: 11.5))
        .padding(.horizontal, 14).padding(.vertical, 7)
        .background(.black.opacity(0.55), in: Capsule())
        .transition(.opacity)
    }

    private func key(_ keys: String, _ does: String) -> some View {
        HStack(spacing: 6) {
            Text(keys)
                .font(.zoomed(size: 11, weight: .medium).monospaced())
                .padding(.horizontal, 5).padding(.vertical, 1)
                .background(.white.opacity(0.14), in: RoundedRectangle(cornerRadius: 4))
            Text(does).foregroundStyle(.white.opacity(0.75))
        }
        .foregroundStyle(.white)
    }

    // MARK: moving

    /// Every way of moving on — fingers, keys, the round buttons — goes
    /// through here, so they all look the same.
    private func perform(_ swipe: Swipe, size: CGSize) {
        guard !busy else { return }
        switch swipe {
        case .left, .right:
            guard stage.canStep else { return }
            let forward = swipe == .left
            let arriving = forward ? after : before
            busy = true
            withAnimation(.easeOut(duration: 0.24)) {
                slide = CGSize(width: forward ? -size.width : size.width, height: 0)
            }
            later(0.24) {
                cover = arriving
                slide = .zero
                stage.step(forward ? 1 : -1)
                later(0.2) { cover = nil; busy = false }
            }
        case .up:
            guard stage.sorting else { return }
            busy = true
            withAnimation(.easeIn(duration: 0.2)) {
                slide = CGSize(width: 0, height: -size.height * 0.6)
                fade = 0
            }
            later(0.2) {
                stage.remove()
                slide = .zero
                withAnimation(.easeOut(duration: 0.18)) { fade = 1 }
                busy = false
            }
        case .down:
            guard stage.sorting else { return }
            guard !stage.undone.isEmpty else { stage.restore(); return }
            busy = true
            stage.restore()
            slide = CGSize(width: 0, height: -size.height * 0.6)
            fade = 0
            later(0.03) {
                withAnimation(.spring(response: 0.34, dampingFraction: 0.82)) { slide = .zero; fade = 1 }
                busy = false
            }
        }
    }

    private func later(_ seconds: Double, _ work: @escaping () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    private var bar: some View {
        HStack(spacing: 4) {
            VStack(alignment: .leading, spacing: 1) {
                Text(picture.name)
                    .font(.zoomed(size: 12.5, weight: .medium))
                    .lineLimit(1).truncationMode(.middle)
                Text(detail)
                    .font(.zoomed(size: 10.5))
                    .foregroundStyle(.white.opacity(0.55))
            }
            .foregroundStyle(.white)
            Spacer(minLength: 12)
            Button { toggleSorting() } label: {
                HStack(spacing: 5) {
                    Image(systemName: "hand.draw")
                    Text(stage.sorting ? "Done" : "Edit")
                }
                .font(.zoomed(size: 11.5, weight: .semibold))
                .padding(.horizontal, 10)
                .frame(height: 24)
                .background(stage.sorting ? Palette.accent : Color.white.opacity(0.12), in: Capsule())
                .contentShape(Capsule())
            }
            .buttonStyle(.plain).foregroundStyle(.white)
            .help("Edit mode: swipe or use the arrows to browse, move to Trash and recover  (E)")
            Divider().frame(height: 16).padding(.horizontal, 6)
            tool("minus.magnifyingglass", "Zoom out  (−)") { zoom.scale(by: 1 / 1.4) }
            Button { zoom.toggle() } label: {
                Text("\(Int((zoom.magnification * 100).rounded()))%")
                    .font(.zoomed(size: 11.5, weight: .medium).monospacedDigit())
                    .frame(width: 46, height: 24)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain).foregroundStyle(.white.opacity(0.85))
            .help("Fit / actual size  (double-click)")
            tool("plus.magnifyingglass", "Zoom in  (+)") { zoom.scale(by: 1.4) }
            Divider().frame(height: 16).padding(.horizontal, 6)
            tool(copied ? "checkmark" : "doc.on.doc", "Copy  (⌘C)") { copy() }
            tool("square.and.arrow.down", "Save As…  (⌘S)") { save() }
            if picture.isLocal {
                tool("folder", "Show in Finder") { PictureActions.reveal(picture) }
            }
            Divider().frame(height: 16).padding(.horizontal, 6)
            tool("xmark", "Close  (Esc)") { close() }
        }
        .padding(.horizontal, 14)
        .frame(height: 46)
        .background(.black.opacity(0.55))
    }

    private var detail: String {
        var parts: [String] = []
        if let image { parts.append("\(Int(image.size.width)) × \(Int(image.size.height))") }
        if let (at, of) = stage.position { parts.append("\(at) of \(of)") }
        return parts.joined(separator: "  ·  ")
    }

    /// Keys, as invisible buttons: the one way to get shortcuts that work
    /// wherever focus happens to be.
    private func shortcuts(_ size: CGSize) -> some View {
        Group {
            Button("") { close() }.keyboardShortcut(.cancelAction)
            Button("") { perform(.right, size: size) }.keyboardShortcut(.leftArrow, modifiers: [])
            Button("") { perform(.left, size: size) }.keyboardShortcut(.rightArrow, modifiers: [])
            Button("") { perform(.up, size: size) }.keyboardShortcut(.upArrow, modifiers: [])
            Button("") { perform(.down, size: size) }.keyboardShortcut(.downArrow, modifiers: [])
            Button("") { toggleSorting() }.keyboardShortcut("e", modifiers: [])
            Button("") { zoom.scale(by: 1.4) }.keyboardShortcut("=", modifiers: [])
            Button("") { zoom.scale(by: 1.4) }.keyboardShortcut("+", modifiers: [])
            Button("") { zoom.scale(by: 1 / 1.4) }.keyboardShortcut("-", modifiers: [])
            Button("") { zoom.fit() }.keyboardShortcut("0", modifiers: [])
            Button("") { zoom.actual() }.keyboardShortcut("1", modifiers: [])
            Button("") { copy() }.keyboardShortcut("c", modifiers: .command)
            Button("") { save() }.keyboardShortcut("s", modifiers: .command)
        }
        .opacity(0)
        .frame(width: 0, height: 0)
        .accessibilityHidden(true)
    }

    private func toggleSorting() {
        withAnimation(.easeOut(duration: 0.15)) { stage.sorting.toggle() }
        if stage.sorting { zoom.fit() }             // swipes belong to a picture that fits
    }

    private func tool(_ symbol: String, _ help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.zoomed(size: 13, weight: .medium))
                .frame(width: 30, height: 26)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(.white.opacity(0.85))
        .help(help)
    }

    private func arrow(_ symbol: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.zoomed(size: 15, weight: .semibold))
                .frame(width: 36, height: 36)
                .background(.black.opacity(0.5), in: Circle())
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(.white.opacity(0.9))
    }

    private func copy() {
        guard let image else { return }
        PictureActions.copy(picture, image)
        copied = true
        Task { try? await Task.sleep(for: .seconds(1.2)); copied = false }
    }

    private func save() {
        if let image { PictureActions.save(picture, image) }
    }

    private func close() {
        stage.sorting = false
        withAnimation(.easeOut(duration: 0.15)) { stage.picture = nil }
    }
}

// MARK: - zoom

@MainActor
final class ZoomController: ObservableObject {
    @Published var magnification: CGFloat = 1
    fileprivate weak var scroll: NSScrollView?

    private var fitScale: CGFloat {
        guard let scroll, let doc = scroll.documentView, doc.frame.width > 0 else { return 1 }
        let room = scroll.bounds.size
        // never blow a small picture up to fill the window
        let margin: CGFloat = 28                   // air around it, and room for the caption
        return min((room.width - margin * 2) / doc.frame.width,
                   (room.height - margin * 2) / doc.frame.height, 1)
    }

    func fit() { set(fitScale) }
    func settle() { set(fitScale, animated: false) }     // a picture arriving, not a person zooming
    func actual() { set(1) }
    func toggle() { abs(magnification - fitScale) < 0.01 ? actual() : fit() }
    func scale(by factor: CGFloat) { set(magnification * factor) }

    private func set(_ value: CGFloat, animated: Bool = true) {
        guard let scroll else { return }
        let clamped = max(scroll.minMagnification, min(scroll.maxMagnification, value))
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = animated ? 0.16 : 0
            let mid = NSPoint(x: scroll.contentView.bounds.midX, y: scroll.contentView.bounds.midY)
            scroll.animator().setMagnification(clamped, centeredAt: mid)
        }
        magnification = clamped
    }

    fileprivate func noticed(_ value: CGFloat) {
        if abs(value - magnification) > 0.001 { magnification = value }
    }
}

/// Keeps a document smaller than the viewport in the middle of it; NSClipView
/// on its own pins it to a corner.
private final class CenteringClipView: NSClipView {
    override func constrainBoundsRect(_ proposed: NSRect) -> NSRect {
        var rect = super.constrainBoundsRect(proposed)
        guard let doc = documentView else { return rect }
        if rect.width > doc.frame.width { rect.origin.x = (doc.frame.width - rect.width) / 2 }
        if rect.height > doc.frame.height { rect.origin.y = (doc.frame.height - rect.height) / 2 }
        return rect
    }
}

private final class DoubleClickImageView: NSImageView {
    var onDoubleClick: (() -> Void)?
    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 { onDoubleClick?() } else { super.mouseDown(with: event) }
    }
    // dragging pans, like Preview
    override func mouseDragged(with event: NSEvent) {
        guard let clip = superview as? NSClipView else { return }
        var origin = clip.bounds.origin
        origin.x -= event.deltaX / (enclosingScrollView?.magnification ?? 1)
        origin.y -= (isFlipped ? event.deltaY : -event.deltaY) / (enclosingScrollView?.magnification ?? 1)
        clip.setBoundsOrigin(clip.constrainBoundsRect(NSRect(origin: origin, size: clip.bounds.size)).origin)
        enclosingScrollView?.reflectScrolledClipView(clip)
    }
}

struct ZoomableImage: NSViewRepresentable {
    let image: NSImage
    let controller: ZoomController

    func makeCoordinator() -> Coordinator { Coordinator(controller) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.contentView = CenteringClipView()
        scroll.drawsBackground = false
        scroll.contentView.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        scroll.allowsMagnification = true
        scroll.minMagnification = 0.05
        scroll.maxMagnification = 8

        let view = DoubleClickImageView()
        view.imageScaling = .scaleNone
        view.image = image
        view.frame = NSRect(origin: .zero, size: image.size)
        view.onDoubleClick = { [weak controller] in controller?.toggle() }
        scroll.documentView = view

        controller.scroll = scroll
        context.coordinator.watch(scroll)
        // the view has no size until it's in the window; fit once it does
        DispatchQueue.main.async { [weak controller] in controller?.settle() }
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? NSImageView, view.image !== image else { return }
        view.image = image
        view.frame = NSRect(origin: .zero, size: image.size)
        DispatchQueue.main.async { [weak controller] in controller?.settle() }
    }

    @MainActor
    final class Coordinator: NSObject {
        let controller: ZoomController
        private var firstLayout = true
        init(_ controller: ZoomController) { self.controller = controller }

        func watch(_ scroll: NSScrollView) {
            NotificationCenter.default.addObserver(
                self, selector: #selector(magnified(_:)),
                name: NSScrollView.didEndLiveMagnifyNotification, object: scroll)
            scroll.postsFrameChangedNotifications = true
            NotificationCenter.default.addObserver(
                self, selector: #selector(resized(_:)),
                name: NSView.frameDidChangeNotification, object: scroll)
        }

        @objc private func magnified(_ note: Notification) {
            if let scroll = note.object as? NSScrollView { controller.noticed(scroll.magnification) }
        }

        @objc private func resized(_ note: Notification) {
            // the first real frame is when "fit" finally means something
            if firstLayout, let scroll = note.object as? NSScrollView, scroll.bounds.width > 0 {
                firstLayout = false
                controller.settle()
            }
        }
    }
}
