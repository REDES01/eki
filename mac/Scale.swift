// SPDX-License-Identifier: Apache-2.0
// The zoom reaches the layout, not only the type.
//
// ⌘+ is meant to make the window bigger the way a browser does: the gaps, the
// icons and the cards along with the words. Scaling the window's content view
// would be the short way, and it doesn't work — SwiftUI goes on hit-testing at
// the unscaled positions, so a click lands on the row above the one drawn. So
// the sizes themselves are multiplied, here, once:
//
//   VStack(spacing: 6)          these two names are ours in this module, and
//   HStack(spacing: 6)          hand a scaled spacing to SwiftUI's
//   .padding(.horizontal, 12)   \
//   .frame(width: 18)            } overloads that take a `Pt`
//   .frame(width: 18, height: 18)/
//
// Only a number *written in the source* becomes a `Pt`. A width worked out
// from a GeometryReader, or remembered from a drag, is a CGFloat, takes
// SwiftUI's own modifier, and isn't scaled a second time.
//
// One thing to know when writing a view: a bare `.padding(` + `8)` is SwiftUI's
// and stays fixed (an overload for it would be ambiguous). Give it its edges:
// `.padding(.all, 8)`.
import SwiftUI

/// A length written as a literal, in points at rest.
struct Pt: ExpressibleByIntegerLiteral, ExpressibleByFloatLiteral {
    let value: CGFloat
    init(integerLiteral value: Int) { self.value = CGFloat(value) }
    init(floatLiteral value: Double) { self.value = CGFloat(value) }
}

struct VStack<Content: View>: View {
    @Environment(\.zoom) private var zoom
    private let alignment: HorizontalAlignment
    private let spacing: CGFloat?
    private let scaled: Bool
    private let content: Content

    init(alignment: HorizontalAlignment = .center, spacing: CGFloat? = nil,
         @ViewBuilder content: () -> Content) {
        self.alignment = alignment; self.spacing = spacing; scaled = false
        self.content = content()
    }

    init(alignment: HorizontalAlignment = .center, spacing: Pt,
         @ViewBuilder content: () -> Content) {
        self.alignment = alignment; self.spacing = spacing.value; scaled = true
        self.content = content()
    }

    var body: some View {
        SwiftUI.VStack(alignment: alignment,
                       spacing: scaled ? spacing.map { $0 * zoom } : spacing) { content }
    }
}

struct HStack<Content: View>: View {
    @Environment(\.zoom) private var zoom
    private let alignment: VerticalAlignment
    private let spacing: CGFloat?
    private let scaled: Bool
    private let content: Content

    init(alignment: VerticalAlignment = .center, spacing: CGFloat? = nil,
         @ViewBuilder content: () -> Content) {
        self.alignment = alignment; self.spacing = spacing; scaled = false
        self.content = content()
    }

    init(alignment: VerticalAlignment = .center, spacing: Pt,
         @ViewBuilder content: () -> Content) {
        self.alignment = alignment; self.spacing = spacing.value; scaled = true
        self.content = content()
    }

    var body: some View {
        SwiftUI.HStack(alignment: alignment,
                       spacing: scaled ? spacing.map { $0 * zoom } : spacing) { content }
    }
}

/// Lazy, so its content has to stay a closure: built on demand, not up front.
struct LazyVStack<Content: View>: View {
    @Environment(\.zoom) private var zoom
    private let alignment: HorizontalAlignment
    private let spacing: Pt
    private let content: () -> Content

    init(alignment: HorizontalAlignment = .center, spacing: Pt,
         @ViewBuilder content: @escaping () -> Content) {
        self.alignment = alignment; self.spacing = spacing; self.content = content
    }

    var body: some View {
        SwiftUI.LazyVStack(alignment: alignment, spacing: spacing.value * zoom, content: content)
    }
}

private struct ZoomedPadding: ViewModifier {
    let edges: Edge.Set
    let length: CGFloat
    @Environment(\.zoom) private var zoom

    func body(content: Content) -> some View { content.padding(edges, length * zoom) }
}

private struct ZoomedFrame: ViewModifier {
    let width: CGFloat?
    let height: CGFloat?
    let alignment: Alignment
    @Environment(\.zoom) private var zoom

    func body(content: Content) -> some View {
        content.frame(width: width.map { $0 * zoom }, height: height.map { $0 * zoom },
                      alignment: alignment)
    }
}

extension View {
    func padding(_ edges: Edge.Set, _ length: Pt) -> some View {
        modifier(ZoomedPadding(edges: edges, length: length.value))
    }

    func frame(width: Pt, height: Pt, alignment: Alignment = .center) -> some View {
        modifier(ZoomedFrame(width: width.value, height: height.value, alignment: alignment))
    }

    func frame(width: Pt, alignment: Alignment = .center) -> some View {
        modifier(ZoomedFrame(width: width.value, height: nil, alignment: alignment))
    }

    func frame(height: Pt, alignment: Alignment = .center) -> some View {
        modifier(ZoomedFrame(width: nil, height: height.value, alignment: alignment))
    }
}
