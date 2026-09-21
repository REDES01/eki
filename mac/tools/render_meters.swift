// Renders every menu bar style to one PNG, on light and dark menu bars, using
// the app's real drawing code. For checking the item without a screenshot.
//   swiftc -o /tmp/render ../MenuBarMeters.swift ../Preferences.swift render_meters.swift
import AppKit
import SwiftUI

let claude = NSColor(srgbRed: 0.85, green: 0.47, blue: 0.34, alpha: 1)
let codex = NSColor(srgbRed: 0.42, green: 0.64, blue: 0.97, alpha: 1)
let samples: [[MeterInput]] = [
    [MeterInput(key: "claude", initial: "C", short: 0.62, long: 0.31, colour: claude),
     MeterInput(key: "codex", initial: "X", short: 0.11, long: nil, colour: codex)],
    [MeterInput(key: "claude", initial: "C", short: 1.0, long: 0.97, colour: claude),
     MeterInput(key: "codex", initial: "X", short: nil, long: nil, colour: codex)],
]

let scale: CGFloat = 3
var rows: [(String, NSImage)] = []
for style in MeterStyle.allCases {
    for colour in MeterColour.allCases {
        for (i, sample) in samples.enumerated() {
            rows.append(("\(style.rawValue) · \(colour.rawValue) · sample \(i + 1)",
                         MenuBarMeters.image(sample, style: style, colour: colour)))
        }
    }
}

let rowH: CGFloat = 26, labelW: CGFloat = 230, cellW: CGFloat = 110
let size = NSSize(width: labelW + cellW * 2, height: CGFloat(rows.count) * rowH + 10)
let canvas = NSImage(size: size, flipped: true) { _ in
    NSColor.white.setFill(); NSRect(origin: .zero, size: size).fill()
    for (i, (label, img)) in rows.enumerated() {
        let y = 5 + CGFloat(i) * rowH
        (label as NSString).draw(at: NSPoint(x: 6, y: y + 5),
                                 withAttributes: [.font: NSFont.systemFont(ofSize: 11)])
        for (j, dark) in [false, true].enumerated() {
            let cell = NSRect(x: labelW + CGFloat(j) * cellW, y: y, width: cellW - 6, height: rowH - 4)
            (dark ? NSColor(white: 0.16, alpha: 1) : NSColor(white: 0.93, alpha: 1)).setFill()
            NSBezierPath(roundedRect: cell, xRadius: 4, yRadius: 4).fill()
            // template images get tinted the way the menu bar would tint them
            let drawn: NSImage
            if img.isTemplate {
                drawn = NSImage(size: img.size, flipped: false) { r in
                    img.draw(in: r)
                    (dark ? NSColor.white : NSColor.black).set()
                    r.fill(using: .sourceAtop)
                    return true
                }
            } else { drawn = img }
            drawn.draw(in: NSRect(x: cell.minX + 6, y: cell.minY + 2,
                                  width: img.size.width, height: img.size.height),
                       from: .zero, operation: .sourceOver, fraction: 1,
                       respectFlipped: true, hints: nil)
        }
    }
    return true
}
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * scale),
                           pixelsHigh: Int(size.height * scale), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
rep.size = size
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
canvas.draw(in: NSRect(origin: .zero, size: size))
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!
    .write(to: URL(fileURLWithPath: CommandLine.arguments.count > 1
                   ? CommandLine.arguments[1] : "/tmp/meters.png"))
print("wrote \(rows.count) renderings")
