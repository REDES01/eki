// SPDX-License-Identifier: Apache-2.0
// What the user chose about how the app looks, kept in UserDefaults.
//
// These are display choices, so they live with the app, not the engine: the
// engine doesn't care how a meter is drawn, and a second Mac viewing the same
// engine may well want its menu bar arranged differently.
import AppKit
import SwiftUI

enum MeterStyle: String, CaseIterable, Identifiable {
    case stacked, rings, percent, icon
    var id: String { rawValue }
    var title: String {
        switch self {
        case .stacked: return "Stacked meters"
        case .rings: return "Rings"
        case .percent: return "Percent + hairline"
        case .icon: return "Icon only"
        }
    }
}

enum MeterColour: String, CaseIterable, Identifiable {
    case mono, provider
    var id: String { rawValue }
    var title: String { self == .mono ? "Match the menu bar" : "Provider colours" }
}

enum Theme: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var title: String { rawValue.capitalized }
    var scheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}

enum Accent: String, CaseIterable, Identifiable {
    case terracotta, blue, sage, violet, graphite
    var id: String { rawValue }
    var title: String { rawValue.capitalized }
    /// light, dark
    var hex: (UInt32, UInt32) {
        switch self {
        case .terracotta: return (0xC2603D, 0xD97757)
        case .blue: return (0x2F6FD0, 0x6BA4F8)
        case .sage: return (0x3F8F5E, 0x6FBF8B)
        case .violet: return (0x7C57C2, 0xA78BE8)
        case .graphite: return (0x55544F, 0xB9B7AE)
        }
    }
}

enum Pref {
    static let menuBarProviders = "menuBarProviders"     // comma-separated, in order
    static let meterStyle = "meterStyle"
    static let meterColour = "meterColour"
    static let theme = "theme"
    static let accent = "accent"
    static let onboarded = "eki.onboarded"
    static let importedTokenbar = "importedTokenbar"

    static var defaults: UserDefaults { .standard }

    static var accentChoice: Accent {
        Accent(rawValue: defaults.string(forKey: Pref.accent) ?? "") ?? .terracotta
    }

    /// The providers the menu bar shows, in the order it shows them.
    static func shown(from available: [String]) -> [String] {
        guard let stored = defaults.string(forKey: menuBarProviders) else {
            return available                       // never configured: show everything
        }
        let wanted = stored.split(separator: ",").map(String.init).filter { !$0.isEmpty }
        return wanted.filter(available.contains)
    }

    static func setShown(_ keys: [String]) {
        defaults.set(keys.joined(separator: ","), forKey: menuBarProviders)
    }

    /// First launch after tokenbar: carry over which providers it showed, so
    /// the menu bar looks like what you were used to.
    static func importTokenbarIfNeeded() {
        guard !defaults.bool(forKey: importedTokenbar) else { return }
        defaults.set(true, forKey: importedTokenbar)
        let config = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("tokenbar/config.yaml")
        guard defaults.string(forKey: menuBarProviders) == nil,
              let text = try? String(contentsOf: config, encoding: .utf8) else { return }
        // only uncommented provider entries; tokenbar's keyed ones were
        // commented out by default and shouldn't come back to life
        let keys = text.split(separator: "\n").compactMap { line -> String? in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard trimmed.hasPrefix("- key:") else { return nil }
            return trimmed.dropFirst("- key:".count).trimmingCharacters(in: .whitespaces)
        }
        if !keys.isEmpty { setShown(keys) }
    }
}


extension Pref {
    /// Settings made before the app was renamed live under the old bundle's
    /// domain. Copy them across once, so nobody has to set up their menu bar
    /// twice over a name change.
    static func adoptOldDomain() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: "eki.adoptedFromHub"),
              let old = UserDefaults(suiteName: "local.hub.app") else { return }
        for (oldKey, newKey) in [
            (menuBarProviders, menuBarProviders), (meterStyle, meterStyle),
            (meterColour, meterColour), (theme, theme), (accent, accent),
            ("hub.onboarded", onboarded), (importedTokenbar, importedTokenbar),
        ] {
            if defaults.object(forKey: newKey) == nil,
               let value = old.object(forKey: oldKey) {
                defaults.set(value, forKey: newKey)
            }
        }
        defaults.set(true, forKey: "eki.adoptedFromHub")
    }
}
