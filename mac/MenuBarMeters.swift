// The menu bar item: your usage, at a glance, in the style you picked.
//
// Drawn as one NSImage rather than a row of SwiftUI views because that is
// what a menu bar item is. In "match the menu bar" mode it's a template
// image, so macOS tints it like every other item, light or dark, focused or
// not. There is no warning colour anywhere: a meter fills, and that's all.
import AppKit
import SwiftUI

struct MeterInput {
    let key: String
    let initial: String
    let short: Double?          // nil = unknown, never drawn as zero
    let long: Double?
    let colour: NSColor
}

enum MenuBarMeters {
    static let height: CGFloat = 18

    /// One letter per provider, unique among those shown ("C" and "X" for
    /// Claude and Codex rather than two C's).
    static func initials(for keys: [String], labels: [String: String]) -> [String: String] {
        var used = Set<String>()
        var out: [String: String] = [:]
        for key in keys {
            let name = (labels[key] ?? key).uppercased()
            let candidates = Array(name).map(String.init).filter { $0.rangeOfCharacter(from: .letters) != nil }
            // first letter, else the last one (codeX), else anything unused
            let pick = [candidates.first, candidates.last].compactMap { $0 }
                .first { !used.contains($0) } ?? candidates.first { !used.contains($0) } ?? "?"
            used.insert(pick)
            out[key] = pick
        }
        return out
    }

    static func image(_ inputs: [MeterInput], style: MeterStyle, colour: MeterColour) -> NSImage {
        let template = colour == .mono
        if style == .icon || inputs.isEmpty {
            let symbol = NSImage(systemSymbolName: "circle.hexagongrid",
                                 accessibilityDescription: "hub") ?? NSImage()
            symbol.isTemplate = true
            return symbol
        }
        let block: CGFloat
        switch style {
        case .stacked: block = 34
        case .rings: block = 28
        case .percent: block = 46                // room for "100%"
        case .icon: block = 0
        }
        let gap: CGFloat = 7
        let width = CGFloat(inputs.count) * block + CGFloat(max(0, inputs.count - 1)) * gap
        let image = NSImage(size: NSSize(width: width, height: height), flipped: false) { _ in
            for (i, input) in inputs.enumerated() {
                let x = CGFloat(i) * (block + gap)
                let ink = template ? NSColor.black : input.colour
                switch style {
                case .stacked: drawStacked(input, at: x, ink: ink)
                case .rings: drawRings(input, at: x, ink: ink)
                case .percent: drawPercent(input, at: x, ink: ink, template: template)
                case .icon: break
                }
            }
            return true
        }
        image.isTemplate = template
        image.accessibilityDescription = inputs.map { input in
            let pct = input.short.map { "\(Int(($0 * 100).rounded()))%" } ?? "unknown"
            return "\(input.key) \(pct)"
        }.joined(separator: ", ")
        return image
    }

    // ---- the styles ------------------------------------------------------

    private static func letter(_ s: String, at point: NSPoint, ink: NSColor, size: CGFloat = 10) {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: size, weight: .bold),
            .foregroundColor: ink,
        ]
        NSAttributedString(string: s, attributes: attrs).draw(at: point)
    }

    private static func bar(_ rect: NSRect, fraction: Double?, ink: NSColor) {
        let radius = rect.height / 2
        ink.withAlphaComponent(0.25).setFill()
        NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
        guard let fraction else { return }            // unknown: track only
        let f = max(0, min(1, fraction))
        guard f > 0 else { return }
        var fill = rect
        fill.size.width = max(rect.height, rect.width * CGFloat(f))
        ink.setFill()
        NSBezierPath(roundedRect: fill, xRadius: radius, yRadius: radius).fill()
    }

    /// The design you picked in tokenbar: the soonest window as a bar, the
    /// longest as a hairline under it.
    private static func drawStacked(_ m: MeterInput, at x: CGFloat, ink: NSColor) {
        letter(m.initial, at: NSPoint(x: x, y: 2.5), ink: ink)
        let meterX = x + 12
        let w: CGFloat = 22
        if m.long != nil {
            bar(NSRect(x: meterX, y: 9, width: w, height: 4), fraction: m.short, ink: ink)
            bar(NSRect(x: meterX, y: 4, width: w, height: 2), fraction: m.long, ink: ink)
        } else {
            bar(NSRect(x: meterX, y: 7, width: w, height: 4), fraction: m.short, ink: ink)
        }
    }

    private static func arc(center: NSPoint, radius: CGFloat, fraction: Double?,
                            ink: NSColor, width: CGFloat) {
        let track = NSBezierPath()
        track.appendArc(withCenter: center, radius: radius, startAngle: 0, endAngle: 360)
        track.lineWidth = width
        ink.withAlphaComponent(0.25).setStroke()
        track.stroke()
        guard let fraction, fraction > 0 else { return }
        let path = NSBezierPath()
        path.appendArc(withCenter: center, radius: radius, startAngle: 90,
                       endAngle: 90 - 360 * CGFloat(min(1, fraction)), clockwise: true)
        path.lineWidth = width
        path.lineCapStyle = .round
        ink.setStroke()
        path.stroke()
    }

    /// The letter sits beside the rings, not inside: inside, a C in two
    /// circles reads as ©.
    private static func drawRings(_ m: MeterInput, at x: CGFloat, ink: NSColor) {
        letter(m.initial, at: NSPoint(x: x, y: 2.5), ink: ink)
        let c = NSPoint(x: x + 20, y: 9)
        arc(center: c, radius: 7, fraction: m.long ?? m.short, ink: ink, width: 2)
        if m.long != nil { arc(center: c, radius: 3.6, fraction: m.short, ink: ink, width: 2) }
    }

    private static func drawPercent(_ m: MeterInput, at x: CGFloat, ink: NSColor, template: Bool) {
        letter(m.initial, at: NSPoint(x: x, y: 3.5), ink: ink)
        let text = m.short.map { "\(Int(($0 * 100).rounded()))%" } ?? "–"
        // red only at exactly full, and only where colour is allowed at all
        let full = (m.short ?? 0) >= 1.0
        let textInk = (!template && full) ? NSColor.systemRed : ink
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium),
            .foregroundColor: textInk,
        ]
        NSAttributedString(string: text, attributes: attrs).draw(at: NSPoint(x: x + 12, y: 4))
        if m.long != nil {
            bar(NSRect(x: x + 12, y: 1.5, width: 30, height: 2), fraction: m.long, ink: ink)
        }
    }
}
