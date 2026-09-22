// SPDX-License-Identifier: Apache-2.0
// eki-hid — the input half of eki's screen tools (see eki/mcpbridge.py).
//
//   eki-hid check                        → "ax=1 screen=0": what macOS has let this helper do
//   eki-hid ask ax|screen                → put up macOS's own prompt for that permission
//   eki-hid screen                       → "W H" of the main display, in points
//   eki-hid click X Y [left|right] [single|double]
//   eki-hid move X Y
//   eki-hid type TEXT
//   eki-hid key COMBO                    → e.g. return, escape, cmd+s, cmd+shift+t
//   eki-hid scroll X Y DX DY
//
// Coordinates are logical points with the origin at the top left of the
// main display, the same frame `screencapture` scaled to points gives.
// Posting events needs Accessibility permission for the process that owns
// this helper (Eki.app); macOS asks once.
import AppKit
import CoreGraphics
import Foundation

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let cmd = args.first else { fail("usage: eki-hid check|ask|screen|click|move|type|key|scroll …") }

/// Input needs Accessibility; without it CGEvent posting is silently dropped,
/// so say so instead — and put up the system prompt the first time.
func needAccessibility() {
    if AXIsProcessTrusted() { return }
    let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    _ = AXIsProcessTrustedWithOptions(opts)
    fail("Accessibility permission not granted for eki-hid (eki's input helper): System Settings › "
         + "Privacy & Security › Accessibility")
}

func point(_ i: Int) -> CGPoint {
    guard args.count > i + 1, let x = Double(args[i]), let y = Double(args[i + 1]) else { fail("need X Y") }
    return CGPoint(x: x, y: y)
}

func post(_ e: CGEvent?) {
    e?.post(tap: .cghidEventTap)
    usleep(20_000)
}

switch cmd {
case "check":
    print("ax=\(AXIsProcessTrusted() ? 1 : 0) screen=\(CGPreflightScreenCaptureAccess() ? 1 : 0)")

case "ask":
    if args.count > 1, args[1] == "screen" {
        print(CGRequestScreenCaptureAccess() ? "granted" : "asked")
    } else {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        print(AXIsProcessTrustedWithOptions(opts) ? "granted" : "asked")
    }

case "screen":
    let f = CGDisplayBounds(CGMainDisplayID())
    print("\(Int(f.width)) \(Int(f.height))")

case "move":
    needAccessibility()
    post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point(1), mouseButton: .left))

case "click":
    needAccessibility()
    let p = point(1)
    let right = args.count > 3 && args[3] == "right"
    let double = args.count > 4 && args[4] == "double"
    let down: CGEventType = right ? .rightMouseDown : .leftMouseDown
    let up: CGEventType = right ? .rightMouseUp : .leftMouseUp
    let button: CGMouseButton = right ? .right : .left
    post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: button))
    for n in 1...(double ? 2 : 1) {
        let d = CGEvent(mouseEventSource: nil, mouseType: down, mouseCursorPosition: p, mouseButton: button)
        let u = CGEvent(mouseEventSource: nil, mouseType: up, mouseCursorPosition: p, mouseButton: button)
        d?.setIntegerValueField(.mouseEventClickState, value: Int64(n))
        u?.setIntegerValueField(.mouseEventClickState, value: Int64(n))
        post(d); post(u)
    }

case "scroll":
    needAccessibility()
    let p = point(1)
    guard args.count > 4, let dx = Int32(args[3]), let dy = Int32(args[4]) else { fail("need X Y DX DY") }
    post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left))
    post(CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 2, wheel1: dy, wheel2: dx, wheel3: 0))

case "type":
    needAccessibility()
    let text = args.dropFirst().joined(separator: " ")
    for scalar in text.unicodeScalars {
        var chars = Array(String(scalar).utf16)
        let d = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
        let u = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
        d?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
        u?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars)
        post(d); post(u)
    }

case "key":
    needAccessibility()
    guard args.count > 1 else { fail("need a key") }
    let codes: [String: CGKeyCode] = [
        "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
        "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126,
        "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "forwarddelete": 117,
        "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
        "f9": 101, "f10": 109, "f11": 103, "f12": 111,
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
        "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
        "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
        "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
        ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    ]
    var flags = CGEventFlags()
    var key: CGKeyCode? = nil
    for part in args[1].lowercased().split(separator: "+").map(String.init) {
        switch part {
        case "cmd", "command": flags.insert(.maskCommand)
        case "shift": flags.insert(.maskShift)
        case "alt", "option": flags.insert(.maskAlternate)
        case "ctrl", "control": flags.insert(.maskControl)
        case "fn": flags.insert(.maskSecondaryFn)
        default:
            guard let c = codes[part] else { fail("unknown key \(part)") }
            key = c
        }
    }
    guard let code = key else { fail("no key in \(args[1])") }
    let d = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
    let u = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
    d?.flags = flags; u?.flags = flags
    post(d); post(u)

default:
    fail("unknown command \(cmd)")
}
