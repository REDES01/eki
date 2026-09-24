// SPDX-License-Identifier: Apache-2.0
// Pictures.
//
// An answer from an image model is a file on disk and a line of Markdown
// pointing at it. Here that line becomes the picture itself, in the column with
// everything else, and a click opens it properly: zoom, pan, step through the
// other pictures in the thread, copy, save, find it in Finder.
//
// The file stays where the backend wrote it. eki shows it; it does not adopt it.
import AppKit
import SwiftUI

/// What is open *beside* or *over* the chat. Not part of AppModel because none
/// of it is the engine's business: it is only where you are looking.
@MainActor
final class Stage: ObservableObject {
    static let shared = Stage()
    @Published var artifact: Artifact?
    @Published var picture: Picture?
    @Published var gallery: [Picture] = []
    /// When the gallery opened what's on screen: everything it is showing, to
    /// walk through, and which of them this is. (Swipes.swift)
    @Published var deck: [MadeItem] = []
    @Published var current: MadeItem?
    @Published var hidden: Set<String> = Hidden.load()
    @Published var notice: Notice?
    @Published var pull: Pull?
    /// Sorting: the viewer's edit mode, the only place swipes mean anything.
    @Published var sorting = false
    var undone: [Removed] = []

    func show(_ picture: Picture, among gallery: [Picture]) {
        current = nil
        self.gallery = gallery.contains(picture) ? gallery : [picture]
        self.picture = picture
    }

    func step(_ by: Int) {
        if live != nil {                            // from the gallery: its pictures, in its order
            let reel = self.reel
            if let at = reel.firstIndex(where: { $0.id == live?.id }), reel.count > 1 {
                present(reel[(at + by + reel.count) % reel.count])
            }
            return
        }
        guard let picture, let at = gallery.firstIndex(of: picture), gallery.count > 1 else { return }
        self.picture = gallery[(at + by + gallery.count) % gallery.count]
    }
}

struct Picture: Identifiable, Hashable {
    let source: String              // as written: a path, ~/path, file:// or https://
    var alt: String = ""

    var id: String { source }

    var url: URL? {
        if source.hasPrefix("http://") || source.hasPrefix("https://") || source.hasPrefix("file://") {
            return URL(string: source)
        }
        let path = (source.removingPercentEncoding ?? source) as NSString
        return URL(fileURLWithPath: path.expandingTildeInPath)
    }

    var isLocal: Bool { url?.isFileURL ?? false }
    var name: String { url?.lastPathComponent ?? source }

    /// Every picture in a thread, in order, for stepping through in the viewer.
    static func all(in turns: [Turn]) -> [Picture] {
        var seen = Set<String>()
        var out: [Picture] = []
        for turn in turns where turn.role != "user" {
            for block in MarkdownParser.blocks(turn.content) {
                if case .image(let alt, let source) = block, seen.insert(source).inserted {
                    out.append(Picture(source: source, alt: alt))
                }
            }
        }
        return out
    }
}

// MARK: - loading

enum PictureLoader {
    private static let cache: NSCache<NSString, NSImage> = {
        let c = NSCache<NSString, NSImage>()
        c.countLimit = 40
        return c
    }()

    static func cached(_ picture: Picture) -> NSImage? {
        cache.object(forKey: picture.source as NSString)
    }

    static func load(_ picture: Picture) async -> NSImage? {
        if let hit = cached(picture) { return hit }
        guard let url = picture.url else { return nil }
        let data: Data? = await Task.detached(priority: .userInitiated) {
            if url.isFileURL { return try? Data(contentsOf: url) }
            return try? await URLSession.shared.data(from: url).0
        }.value
        guard let data, let image = NSImage(data: data) else { return nil }
        // NSImage reports points; a 1024px render tagged 144dpi would call
        // itself 512 wide. The pixels are the truth.
        if let rep = image.representations.first, rep.pixelsWide > 0 {
            image.size = NSSize(width: rep.pixelsWide, height: rep.pixelsHigh)
        }
        cache.setObject(image, forKey: picture.source as NSString)
        return image
    }
}

enum PictureActions {
    static func copy(_ picture: Picture, _ image: NSImage) {
        let board = NSPasteboard.general
        board.clearContents()
        var items: [NSPasteboardWriting] = [image]
        if picture.isLocal, let url = picture.url { items.append(url as NSURL) }
        board.writeObjects(items)
    }

    static func reveal(_ picture: Picture) {
        guard picture.isLocal, let url = picture.url else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    static func open(_ picture: Picture) {
        if let url = picture.url { NSWorkspace.shared.open(url) }
    }

    static func save(_ picture: Picture, _ image: NSImage) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = picture.name.isEmpty ? "image.png" : picture.name
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let to = panel.url else { return }
        if picture.isLocal, let from = picture.url {
            try? FileManager.default.removeItem(at: to)
            try? FileManager.default.copyItem(at: from, to: to)
        } else if let tiff = image.tiffRepresentation,
                  let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
            try? png.write(to: to)
        }
    }
}

// MARK: - in the column

struct InlineImage: View {
    @EnvironmentObject var model: AppModel
    let alt: String
    let source: String

    @State private var image: NSImage?
    @State private var missing = false
    @State private var hovering = false

    private var picture: Picture { Picture(source: source, alt: alt) }

    var body: some View {
        Group {
            if let image {
                shown(image)
            } else if missing {
                gone
            } else {
                RoundedRectangle(cornerRadius: Radius.card)
                    .fill(Palette.fill)
                    .frame(width: 240, height: 240)
                    .overlay(ProgressView().controlSize(.small))
            }
        }
        .task(id: source) {
            image = PictureLoader.cached(picture)
            if image == nil {
                image = await PictureLoader.load(picture)
                missing = image == nil
            }
        }
    }

    private func shown(_ image: NSImage) -> some View {
        let shape = RoundedRectangle(cornerRadius: Radius.card)
        return Image(nsImage: image)
            .resizable()
            .interpolation(.high)
            .scaledToFit()
            .frame(maxWidth: min(460, image.size.width), maxHeight: 460, alignment: .leading)
            .clipShape(shape)
            .overlay(shape.strokeBorder(Palette.hairline, lineWidth: 1))
            .overlay(alignment: .bottomTrailing) {
                if hovering {
                    HStack(spacing: Space.xxs) {
                        chip("arrow.up.left.and.arrow.down.right", "Open") { open() }
                        chip("square.and.arrow.down", "Save…") { PictureActions.save(picture, image) }
                        if picture.isLocal {
                            chip("folder", "Show in Finder") { PictureActions.reveal(picture) }
                        }
                    }
                    .padding(.all, Space.xs)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 9))
                    .padding(.all, Space.s)
                    .transition(.opacity)
                }
            }
            .contentShape(shape)
            .onHover { over in withAnimation(.easeOut(duration: 0.12)) { hovering = over } }
            .onTapGesture { open() }
            .onDrag {
                if picture.isLocal, let url = picture.url,
                   let provider = NSItemProvider(contentsOf: url) { return provider }
                return NSItemProvider(object: image)
            }
            .contextMenu {
                Button("Open") { open() }
                Button("Copy Image") { PictureActions.copy(picture, image) }
                Button("Save As…") { PictureActions.save(picture, image) }
                if picture.isLocal {
                    Divider()
                    Button("Show in Finder") { PictureActions.reveal(picture) }
                    Button("Open in Preview") { PictureActions.open(picture) }
                }
            }
            .help(alt.isEmpty ? picture.name : alt)
            .accessibilityLabel(alt.isEmpty ? "Generated image" : alt)
    }

    private var gone: some View {
        HStack(spacing: Space.s) {
            Image(systemName: "photo.badge.exclamationmark")
                .foregroundStyle(Palette.inkFaint)
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text("This picture isn't where it was")
                    .font(.hubCallout.weighted(.medium))
                Text(source)
                    .font(.hubMonoSmall)
                    .foregroundStyle(Palette.inkFaint)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }
        }
        .padding(.horizontal, Space.m)
        .padding(.vertical, Space.s)
        .background(Palette.fill, in: RoundedRectangle(cornerRadius: Radius.small))
    }

    private func chip(_ symbol: String, _ help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.hubCaption.weighted(.medium))
                .frame(width: 24, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(help)
    }

    private func open() {
        Stage.shared.show(picture, among: Picture.all(in: model.turns))
    }
}
