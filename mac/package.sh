#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Make the thing people download: Eki-<version>.zip, notarised if you have an
# identity, ad-hoc if you don't.
#
#   ./package.sh
#   DEVELOPER_ID="Developer ID Application: …" NOTARY_PROFILE=eki ./package.sh
#
# ditto (not zip) keeps the signature intact, which is what Gatekeeper and
# Sparkle both check on the other side.
set -euo pipefail
cd "$(dirname "$0")"
VERSION="$(cat ../VERSION)"
OUT="../dist"
APP="../Eki.app"

./build_app.sh --full
mkdir -p "$OUT"
ZIP="$OUT/Eki-$VERSION.zip"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
shasum -a 256 "$ZIP" | tee "$ZIP.sha256"
echo "packaged: $ZIP ($(du -sh "$ZIP" | cut -f1))"
echo
echo "Unsigned builds: the person downloading opens it once from"
echo "System Settings -> Privacy & Security -> Open Anyway. With a Developer ID"
echo "in DEVELOPER_ID (and NOTARY_PROFILE for notarisation) it just opens."
