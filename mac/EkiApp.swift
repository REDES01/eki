// SPDX-License-Identifier: Apache-2.0
// eki — one window for every model you can already talk to, and a menu bar
// item for how much of each you have left.
//
// A real app rather than a background agent: it gets a Dock icon, an app
// switcher entry and windows that take focus. (TokenBar learned that the hard
// way — an LSUIElement can't reliably become key.)
import SwiftUI

@main
struct EkiApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    init() { Pref.adoptOldDomain() }      // settings made before the rename

    // read here so a change in Settings redraws the menu bar and theme at once
    @AppStorage(Pref.theme) private var theme: String = Theme.system.rawValue
    @AppStorage(Pref.accent) private var accent: String = Accent.terracotta.rawValue
    @AppStorage(Pref.meterStyle) private var meterStyle: String = MeterStyle.stacked.rawValue
    @AppStorage(Pref.meterColour) private var meterColour: String = MeterColour.mono.rawValue
    @AppStorage(Pref.menuBarProviders) private var shownRaw: String = ""
    @AppStorage(Pref.zoom) private var zoom: Double = 1

    private var scheme: ColorScheme? { Theme(rawValue: theme)?.scheme }

    var body: some Scene {
        WindowGroup("eki", id: "main") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 760, minHeight: 480)
                .preferredColorScheme(scheme)
                // one accent for the whole app, so switches, pickers and
                // selections follow what you picked rather than system blue
                .tint(Palette.accent)
                .environment(\.zoom, zoom)
                .id(accent)                  // re-read the accent everywhere on change
        }
        .defaultSize(width: 1040, height: 680)
        .commands {
            // About says which build runs; Restart to Update while a newer one waits
            CommandGroup(replacing: .appInfo) { AppMenuItems() }
            CommandGroup(after: .newItem) {
                Button("New Chat") { model.newConversation() }
                    .keyboardShortcut("n", modifiers: .command)
                Button("Refresh") { Task { await model.refreshAll() } }
                    .keyboardShortcut("r", modifiers: .command)
            }
            // the View menu; `zoom` is read so the items grey out at either end
            CommandGroup(before: .toolbar) {
                Button("Actual Size") { Zoom.reset() }
                    .keyboardShortcut("0", modifiers: .command)
                    .disabled(zoom == 1)
                Button("Zoom In") { Zoom.larger() }
                    .keyboardShortcut("+", modifiers: .command)
                    .disabled(!Zoom.canGrow)
                Button("Zoom Out") { Zoom.smaller() }
                    .keyboardShortcut("-", modifiers: .command)
                    .disabled(!Zoom.canShrink)
                Divider()
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
    /// The menu says ⌘+, but the key under that plus is "=" and nobody holds
    /// shift to zoom. Caught before the menu sees it, so it's one step either way.
    func applicationDidFinishLaunching(_ notification: Notification) {
        AppUpdate.shared.start()
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            let held = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            guard held == .command, event.charactersIgnoringModifiers == "=" else { return event }
            Zoom.larger()
            return nil
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppUpdate.shared.forget()
    }

    /// Closing the last window keeps the menu bar item. eki is still there.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
