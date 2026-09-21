// One place for every colour, radius and type size in the app.
//
// The palette is warm rather than blue-grey — paper, not chrome — because the
// window is mostly long-form text and a cool grey background makes prose look
// like a log file. Every colour is defined for both appearances and resolves
// at draw time, so nothing has to be recomputed when the system flips.
import AppKit
import SwiftUI

extension NSColor {
    /// #RRGGBB, because hex is how the palette was picked.
    convenience init(hex: UInt32) {
        self.init(srgbRed: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  alpha: 1)
    }
}

extension Color {
    static func adaptive(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
                ? NSColor(hex: dark) : NSColor(hex: light)
        })
    }
}

enum Palette {
    /// the page itself
    static let canvas      = Color.adaptive(0xFAF9F5, 0x232322)
    /// the rail beside it, one step back from the page
    static let rail        = Color.adaptive(0xF2F0E9, 0x1C1C1B)
    /// a raised thing on the page: a card, a bubble, the composer
    static let surface     = Color.adaptive(0xFFFFFF, 0x2D2D2B)
    /// a quiet fill that isn't quite a card
    static let fill        = Color.adaptive(0xEFEDE4, 0x333331)
    static let hairline    = Color.adaptive(0xE3E0D6, 0x3A3A37)

    static let ink         = Color.adaptive(0x1F1E1B, 0xF3F2EC)
    static let inkMuted    = Color.adaptive(0x6C6A61, 0xA9A79E)
    static let inkFaint    = Color.adaptive(0x9B9991, 0x7A7973)

    /// The app's one accent — terracotta unless you picked another in
    /// Settings. Read at draw time, so a change applies without a restart.
    static var accent: Color {
        let (light, dark) = Pref.accentChoice.hex
        return .adaptive(light, dark)
    }
    static var accentSoft: Color {
        // the accent at low strength over the canvas, per appearance
        Color(nsColor: NSColor(name: nil) { appearance in
            let dark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            let (l, d) = Pref.accentChoice.hex
            let base = NSColor(hex: dark ? d : l)
            let canvas = NSColor(hex: dark ? 0x232322 : 0xFAF9F5)
            return canvas.blended(withFraction: dark ? 0.22 : 0.16, of: base) ?? base
        })
    }

    static let ok          = Color.adaptive(0x3F8F5E, 0x6FBF8B)
    static let warn        = Color.adaptive(0xB5791E, 0xE0A64B)
    static let danger      = Color.adaptive(0xB4432F, 0xE0705A)

    /// Provider marks: Anthropic orange for Claude, a cool blue for Codex,
    /// violet for images, and a quiet sage for anything on this machine.
    static func backend(_ key: String) -> Color {
        switch key {
        // fixed, not the accent: pick a blue accent and Claude would
        // otherwise turn the same colour as Codex
        case "claude": return .adaptive(0xC2603D, 0xD97757)
        case "codex":  return .adaptive(0x2F6FD0, 0x6BA4F8)
        case "flux":   return .adaptive(0x7C57C2, 0xA78BE8)
        default:       return .adaptive(0x3F8F5E, 0x6FBF8B)
        }
    }
}

enum Metric {
    /// A reading measure, not a window width: long lines are hard to track.
    static let column: CGFloat = 720
    static let gutter: CGFloat = 26
    static let radius: CGFloat = 12
    static let smallRadius: CGFloat = 8
}

extension Font {
    static let hubBody = Font.system(size: 14.5)
    static let hubMessage = Font.system(size: 14.5)
    static let hubLabel = Font.system(size: 11, weight: .medium)
    static let hubTitle = Font.system(size: 15, weight: .semibold)
    static let hubMono = Font.system(size: 13, design: .monospaced)
    static let hubMonoSmall = Font.system(size: 11.5, design: .monospaced)
}

// MARK: - small shared pieces

/// A section heading: small, spaced-out, and quiet enough to ignore.
struct SectionLabel: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10.5, weight: .semibold))
            .tracking(0.9)
            .foregroundStyle(Palette.inkFaint)
    }
}

/// A card. Everything raised off the canvas uses this one, so the app has a
/// single idea of what "a thing on the page" looks like.
struct Card<Content: View>: View {
    var padding: CGFloat = 14
    @ViewBuilder let content: () -> Content

    var body: some View {
        content()
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.surface, in: RoundedRectangle(cornerRadius: Metric.radius))
            .overlay(RoundedRectangle(cornerRadius: Metric.radius)
                .strokeBorder(Palette.hairline, lineWidth: 1))
    }
}

/// A status dot, with an optional slow pulse for "this is happening now".
struct Dot: View {
    let color: Color
    var size: CGFloat = 7
    var pulsing: Bool = false
    @State private var on = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: size, height: size)
            .opacity(pulsing && on ? 0.3 : 1)
            .animation(pulsing ? .easeInOut(duration: 0.9).repeatForever() : .default,
                       value: on)
            .onAppear { on = true }
    }
}

/// A small pill of metadata — backend name, state, a count.
struct Tag: View {
    let text: String
    var color: Color = Palette.inkMuted

    var body: some View {
        Text(text)
            .font(.system(size: 10.5, weight: .medium))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 2.5)
            .background(color.opacity(0.11),
                        in: Capsule())
    }
}

/// A quiet button that looks like text until you point at it.
struct GhostButton: ButtonStyle {
    @State private var hovering = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(configuration.isPressed ? Palette.ink : Palette.inkMuted)
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(hovering ? Palette.fill : Color.clear,
                        in: RoundedRectangle(cornerRadius: 6))
            .onHover { hovering = $0 }
    }
}

/// A switch whose "on" is unmistakable. The system switch on macOS ignores
/// `.tint` and draws a grey track in both states, so on and off differ only
/// by which side the knob sits — too subtle for a setting that matters.
struct AccentSwitch: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack {
            configuration.label
            Spacer(minLength: 8)
            ZStack(alignment: configuration.isOn ? .trailing : .leading) {
                Capsule()
                    .fill(configuration.isOn ? Palette.accent : Palette.fill)
                    .overlay(Capsule().strokeBorder(Palette.hairline,
                                                    lineWidth: configuration.isOn ? 0 : 1))
                    .frame(width: 32, height: 18)
                Circle()
                    .fill(.white)
                    .shadow(color: .black.opacity(0.25), radius: 1, y: 0.5)
                    .frame(width: 14, height: 14)
                    .padding(2)
            }
            .animation(.easeOut(duration: 0.15), value: configuration.isOn)
            .onTapGesture { configuration.isOn.toggle() }
            .accessibilityElement()
            .accessibilityAddTraits(.isButton)
            .accessibilityValue(configuration.isOn ? "on" : "off")
            .accessibilityAction { configuration.isOn.toggle() }
        }
    }
}

/// The one solid button: used where an action is the point of the view.
struct AccentButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        AccentLabel(configuration: configuration)
    }

    private struct AccentLabel: View {
        let configuration: Configuration
        @Environment(\.isEnabled) private var enabled

        var body: some View {
            configuration.label
            .font(.system(size: 12.5, weight: .medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(Palette.accent.opacity(configuration.isPressed ? 0.8 : 1),
                        in: RoundedRectangle(cornerRadius: 7))
            .opacity(enabled ? 1 : 0.4)
        }
    }
}
