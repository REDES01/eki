// hub — one window for every model you can already talk to, and a menu bar
// item for how much of each you have left.
//
// A real app rather than a background agent: it gets a Dock icon, an app
// switcher entry and windows that take focus. (TokenBar learned that the hard
// way — an LSUIElement can't reliably become key.)
import SwiftUI

@main
struct HubApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    // read here so a change in Settings redraws the menu bar and theme at once
    @AppStorage(Pref.theme) private var theme: String = Theme.system.rawValue
    @AppStorage(Pref.accent) private var accent: String = Accent.terracotta.rawValue
    @AppStorage(Pref.meterStyle) private var meterStyle: String = MeterStyle.stacked.rawValue
    @AppStorage(Pref.meterColour) private var meterColour: String = MeterColour.mono.rawValue
    @AppStorage(Pref.menuBarProviders) private var shownRaw: String = ""

    private var scheme: ColorScheme? { Theme(rawValue: theme)?.scheme }

    var body: some Scene {
        WindowGroup("hub", id: "main") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 760, minHeight: 480)
                .preferredColorScheme(scheme)
                // one accent for the whole app, so switches, pickers and
                // selections follow what you picked rather than system blue
                .tint(Palette.accent)
                .id(accent)                  // re-read the accent everywhere on change
        }
        .defaultSize(width: 1040, height: 680)
        .commands {
            CommandGroup(after: .newItem) {
                Button("New Chat") { model.newConversation() }
                    .keyboardShortcut("n", modifiers: .command)
                Button("Refresh") { Task { await model.refreshAll() } }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }

        // Usage at a glance, drawn in the style you picked in Settings.
        MenuBarExtra {
            MenuPanel()
                .environmentObject(model)
                .preferredColorScheme(scheme)
                .tint(Palette.accent)
        } label: {
            // the stored preferences are named here only so SwiftUI redraws
            // the item when any of them changes
            let _ = (meterStyle, meterColour, shownRaw)
            Image(nsImage: model.menuBarImage())
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(model)
                .preferredColorScheme(scheme)
                .tint(Palette.accent)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Closing the last window keeps the menu bar item. hub is still there.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
