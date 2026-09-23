// SPDX-License-Identifier: Apache-2.0
// eki's engine, under its own name.
//
// macOS asks for permission — to a folder, the network, another app — in the
// name of the process launchd started, for everything below it too. Started as
// a bare python, that's "python3.12": for the engine and all it runs (Claude
// Code, Codex, a goal's agent, the model servers). This small app starts the
// engine as its child and waits for it, so the name in those prompts, and in
// System Settings → Privacy & Security, is "eki". Built by eki/launcher.py.
import ApplicationServices
import CoreGraphics
import Foundation

// `open -n eki.app --args --request-access`: ask macOS for the screen, as eki —
// the prompts put "eki" in Screen Recording and Accessibility, ready to switch on
if CommandLine.arguments.dropFirst().first == "--request-access" {
    let screen = CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess()
    let ask = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    let input = AXIsProcessTrustedWithOptions(ask)
    print("screen=\(screen) accessibility=\(input)")
    exit(0)
}

let args = Array(CommandLine.arguments.dropFirst())
guard !args.isEmpty else {
    FileHandle.standardError.write("usage: eki <program> [arguments…]\n".data(using: .utf8)!)
    exit(64)
}
// the engine watches for this parent going away, and goes with it
setenv("EKI_LAUNCHER", "1", 1)

var pid: pid_t = 0
var argv: [UnsafeMutablePointer<CChar>?] = args.map { strdup($0) }
argv.append(nil)
let started = posix_spawn(&pid, args[0], nil, nil, &argv, environ)
if started != 0 {
    FileHandle.standardError.write("eki: couldn't start \(args[0]): \(String(cString: strerror(started)))\n"
        .data(using: .utf8)!)
    exit(1)
}

// what launchd sends the engine (stop, restart) is passed on to it
var sources: [DispatchSourceSignal] = []
for sig in [SIGTERM, SIGINT, SIGHUP] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { kill(pid, sig) }
    source.resume()
    sources.append(source)
}

// and when the engine ends, so does this, with its status
DispatchQueue.global().async {
    var status: Int32 = 0
    while waitpid(pid, &status, 0) == -1 && errno == EINTR {}
    let signaled = status & 0x7f
    exit(signaled == 0 ? (status >> 8) & 0xff : 128 + signaled)
}
dispatchMain()
