// SPDX-License-Identifier: Apache-2.0
// One picture, properly: the whole window, pinch or scroll to zoom, drag to
// pan, arrows for the thread's other pictures, Esc to go back.
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

    var body: some View {
        ZStack {
            Color.black.opacity(0.88).ignoresSafeArea()
                .onTapGesture { close() }

            if let image {
                ZoomableImage(image: image, controller: zoom)
                    .id(picture.id)
                    .padding(.top, 46)
            } else if missing {
                Text("This picture isn't where it was\n\(picture.source)")
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.white.opacity(0.7))
            } else {
                ProgressView().controlSize(.large)
            }

            VStack(spacing: 0) {
                bar
                Spacer()
                if !picture.alt.isEmpty {
                    Text(picture.alt)
                        .font(.system(size: 12))
                        .foregroundStyle(.white.opacity(0.8))
                        .lineLimit(2)
                        .padding(.horizontal, 12).padding(.vertical, 6)
                        .background(.black.opacity(0.45), in: Capsule())
                        .padding(.bottom, 16)
                }
            }

            if stage.gallery.count > 1 {
                HStack {
                    arrow("chevron.left") { stage.step(-1) }
                    Spacer()
                    arrow("chevron.right") { stage.step(1) }
                }
                .padding(.horizontal, 14)
            }

            shortcuts
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
        .transition(.opacity)
    }

    private var bar: some View {
        HStack(spacing: 4) {
            VStack(alignment: .leading, spacing: 1) {
                Text(picture.name)
                    .font(.system(size: 12.5, weight: .medium))
                    .lineLimit(1).truncationMode(.middle)
                Text(detail)
                    .font(.system(size: 10.5))
                    .foregroundStyle(.white.opacity(0.55))
            }
            .foregroundStyle(.white)
            Spacer(minLength: 12)
            tool("minus.magnifyingglass", "Zoom out  (−)") { zoom.scale(by: 1 / 1.4) }
            Button { zoom.toggle() } label: {
                Text("\(Int((zoom.magnification * 100).rounded()))%")
                    .font(.system(size: 11.5, weight: .medium).monospacedDigit())
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
        if stage.gallery.count > 1, let at = stage.gallery.firstIndex(of: picture) {
            parts.append("\(at + 1) of \(stage.gallery.count)")
        }
        return parts.joined(separator: "  ·  ")
    }

    /// Keys, as invisible buttons: the one way to get shortcuts that work
    /// wherever focus happens to be.
    private var shortcuts: some View {
        Group {
            Button("") { close() }.keyboardShortcut(.cancelAction)
            Button("") { stage.step(-1) }.keyboardShortcut(.leftArrow, modifiers: [])
            Button("") { stage.step(1) }.keyboardShortcut(.rightArrow, modifiers: [])
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

    private func tool(_ symbol: String, _ help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .medium))
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
                .font(.system(size: 15, weight: .semibold))
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
    func actual() { set(1) }
    func toggle() { abs(magnification - fitScale) < 0.01 ? actual() : fit() }
    func scale(by factor: CGFloat) { set(magnification * factor) }

    private func set(_ value: CGFloat) {
        guard let scroll else { return }
        let clamped = max(scroll.minMagnification, min(scroll.maxMagnification, value))
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.16
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
        DispatchQueue.main.async { [weak controller] in controller?.fit() }
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? NSImageView, view.image !== image else { return }
        view.image = image
        view.frame = NSRect(origin: .zero, size: image.size)
        DispatchQueue.main.async { [weak controller] in controller?.fit() }
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
                controller.fit()
            }
        }
    }
}
