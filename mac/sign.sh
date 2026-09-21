#!/usr/bin/env bash
# Sign Hub.app — ad-hoc by default, properly when a Developer ID is set.
#
#   ./sign.sh Hub.app
#   DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)" ./sign.sh Hub.app
#   … plus NOTARY_PROFILE=hub to notarise and staple.
#
# Without a Developer ID the app still runs: macOS asks once, and the user
# opens it from System Settings → Privacy & Security → Open Anyway. With one,
# it opens like anything else. Nothing else about the build changes, so the
# switch is one environment variable on release day.
set -euo pipefail
APP="${1:-Hub.app}"
ID="${DEVELOPER_ID:--}"
ENTITLEMENTS="$(dirname "$0")/hub.entitlements"

# Inside out: every Mach-O inside the bundle with the same identity, then the
# bundle itself. Mixing identities — or ad-hoc with signed — makes dyld refuse
# to load Python's extension modules: "different Team IDs". --deep is not used
# anywhere: it walks the bundled interpreter as if its directories were nested
# bundles and fails the whole app.
RUNTIME=(--options runtime --entitlements "$ENTITLEMENTS")
[ "$ID" = "-" ] && RUNTIME=()

# Clear quarantine and any stray attributes *before* signing: codesign stores a
# script's signature in an extended attribute, and wiping attributes afterwards
# breaks the seal it just made.
xattr -cr "$APP" 2>/dev/null || true

# Every Mach-O in the bundle, plus the launcher scripts in Helpers — codesign
# treats those as nested code. Scripts under Resources are data and are sealed
# by hash; signing them one by one would mean thousands of pointless calls.
find "$APP/Contents/Helpers" "$APP/Contents/Frameworks" "$APP/Contents/MacOS" \
     "$APP/Contents/Resources/python" -type f 2>/dev/null \
  | while read -r f; do
      case "$f" in
        "$APP/Contents/Helpers/"*) ;;               # a launcher: always sign
        *) case "$(file -b "$f")" in
             Mach-O*) ;;
             *) continue ;;
           esac ;;
      esac
      codesign --force ${RUNTIME[@]+"${RUNTIME[@]}"} --sign "$ID" "$f" >/dev/null 2>&1 || true
    done || true      # the loop ends when `read` runs out of input, which is not an error

for attempt in 1 2 3; do
  xattr -cr "$APP/Contents/Resources/python" 2>/dev/null || true
  codesign --force ${RUNTIME[@]+"${RUNTIME[@]}"} --sign "$ID" "$APP" >/dev/null 2>&1
  if codesign --verify --strict "$APP" 2>/dev/null; then
    echo "signature verified (pass $attempt)"
    break
  fi
  if [ "$attempt" = "3" ]; then
    codesign --verify --strict --verbose=2 "$APP" || true
    echo "warning: the signature did not verify"
  fi
done

if [ "$ID" = "-" ]; then
  echo "signed: ad-hoc (no Developer ID set) — macOS will ask once before it opens"
  exit 0
fi
echo "signed: $ID"

if [ -n "${NOTARY_PROFILE:-}" ]; then
  ZIP="$(dirname "$APP")/$(basename "$APP" .app)-notarise.zip"
  /usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
  echo "notarised and stapled"
fi
