#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Compile Eki.app from the Swift files here. Needs Xcode command line tools.
#
#   ./build_app.sh            the app only, engine from the checkout (dev)
#   ./build_app.sh --full     self-contained: bundled Python + engine inside
#
set -euo pipefail
cd "$(dirname "$0")"
PROJECT="$(cd .. && pwd)"
FULL=0
[ "${1:-}" = "--full" ] && FULL=1
APP="${EKI_APP_PATH:-$PROJECT/Eki.app}"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"
HELPERS="$APP/Contents/Helpers"
AGENTS="$APP/Contents/Library/LaunchAgents"
VERSION="$(cat ../VERSION 2>/dev/null || echo 0.1.0)"

rm -rf "$APP"
mkdir -p "$MACOS" "$RES"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Eki</string>
  <key>CFBundleDisplayName</key><string>Eki</string>
  <key>CFBundleIdentifier</key><string>local.eki.app</string>
  <key>CFBundleExecutable</key><string>Eki</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <!-- the engine, the model servers and the CLIs all live on loopback -->
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

# Mermaid draws the diagram artifacts. Fetched once and carried inside the app
# so diagrams work offline; without it the panel falls back to the CDN.
MERMAID="build/vendor/mermaid.min.js"
if [ ! -s "$MERMAID" ]; then
  mkdir -p build/vendor
  curl -fsSL --max-time 60 -o "$MERMAID" \
    "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js" \
    || { rm -f "$MERMAID"; echo "note: couldn't fetch mermaid; diagrams will load it from the CDN"; }
fi
[ -s "$MERMAID" ] && cp "$MERMAID" "$RES/mermaid.min.js"

# The app icon. The master drawing is assets/AppIcon.svg.
[ -f assets/AppIcon.icns ] && cp assets/AppIcon.icns "$RES/AppIcon.icns"

echo "compiling…"
swiftc -O -target arm64-apple-macos14.0 \
  -framework AppKit -framework SwiftUI -framework ServiceManagement -framework WebKit \
  -o "$MACOS/Eki" \
  EkiApp.swift Client.swift Model.swift Theme.swift Markdown.swift \
  Artifacts.swift Gallery.swift Images.swift ImageViewer.swift \
  Views.swift Downloads.swift Models.swift Live.swift Schedules.swift \
  Preferences.swift MenuBarMeters.swift Usage.swift SettingsView.swift Providers.swift Capability.swift Onboarding.swift

if [ "$FULL" = "1" ]; then
  ./bundle_python.sh
  echo "bundling the engine…"
  mkdir -p "$HELPERS" "$AGENTS"
  # The interpreter lives in Resources, not Helpers: codesign walks Helpers
  # looking for nested bundles, decides lib/python3.12 is a malformed one, and
  # refuses to sign the app. Sealed as resources it signs cleanly, and the
  # launcher scripts in Helpers are what launchd actually starts.
  cp -R build/python "$RES/python"
  mkdir -p "$RES/engine"
  # the engine's source, without the developer's clutter
  /usr/bin/rsync -a --exclude "__pycache__" --exclude "*.pyc" \
    "$PROJECT/eki" "$RES/engine/"
  cp "$PROJECT/config.yaml" "$RES/engine/config.yaml"
  cp "$PROJECT/LICENSE" "$PROJECT/NOTICE" "$PROJECT/THIRD_PARTY.md" "$RES/" 2>/dev/null || true

  cat > "$HELPERS/eki-engine" <<'SH'
#!/bin/bash
# The engine, as launchd starts it: eki's own Python, eki's own copy of the
# source, and nothing from the user's machine on the path but the CLIs it
# drives. Kept as a script so the plist can point at one stable name.
# resolve the symlink ~/.local/bin/eki, so the app can be anywhere
src="$0"
while [ -L "$src" ]; do src="$(readlink "$src")"; done
here="$(cd "$(dirname "$src")" && pwd)"
app="$(cd "$here/.." && pwd)"
export PYTHONPATH="$app/Resources/engine"
export PYTHONUNBUFFERED=1
export EKI_CONFIG="${EKI_CONFIG:-$app/Resources/engine/config.yaml}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# launchd can't expand $HOME in a plist, so the log is opened here instead of
# landing in /tmp where everyone on the Mac can read it.
mkdir -p "$HOME/.eki"
exec >>"$HOME/.eki/engine.log" 2>&1
exec "$app/Resources/python/bin/python3" -m eki.cli serve "$@"
SH
  chmod +x "$HELPERS/eki-engine"

  cat > "$HELPERS/eki-cli" <<'SH'
#!/bin/bash
# The `eki` command, using the app's own Python and engine.
# resolve the symlink ~/.local/bin/eki, so the app can be anywhere
src="$0"
while [ -L "$src" ]; do src="$(readlink "$src")"; done
here="$(cd "$(dirname "$src")" && pwd)"
app="$(cd "$here/.." && pwd)"
export PYTHONPATH="$app/Resources/engine"
export EKI_CONFIG="${EKI_CONFIG:-$app/Resources/engine/config.yaml}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
exec "$app/Resources/python/bin/python3" -m eki.cli "$@"
SH
  chmod +x "$HELPERS/eki-cli"

  cat > "$AGENTS/local.eki.engine.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.eki.engine</string>
  <!-- relative to the app bundle: SMAppService resolves it wherever the app is -->
  <key>BundleProgram</key><string>Contents/Helpers/eki-engine</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLIST
fi

./sign.sh "$APP"

echo "built: $APP ($(du -sh "$APP" | cut -f1))"
