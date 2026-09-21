# Releasing eki

## What a release is

`mac/package.sh` produces `dist/Eki-<version>.zip` and its SHA-256. The zip
contains a self-contained `Eki.app`: the SwiftUI app, a CPython built by
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
with eki's dependencies, eki's own source, and two launcher scripts. There is
no installer and nothing to set up afterwards.

```
./mac/package.sh                       # ad-hoc signed, ~70 MB
DEVELOPER_ID="Developer ID Application: NAME (TEAMID)" \
NOTARY_PROFILE=eki ./mac/package.sh    # signed, notarised, stapled
```

Bump `VERSION` first; it becomes `CFBundleShortVersionString` and the zip name.

## Signing, and whether you need an Apple Developer account

You do not need one to build, run, or give someone the app. You need one
(currently $99/year) for two things:

1. **Opening without a detour.** Unsigned and ad-hoc-signed apps make the
   person open System Settings → Privacy & Security → *Open Anyway* the first
   time. With a Developer ID and notarisation, the app just opens.
2. **Homebrew.** `brew install --cask` is removing unsigned casks, so a cask
   needs a Developer ID.

Everything else already works ad-hoc, including the login agent: the app
registers it through `SMAppService`, which accepts a valid ad-hoc signature.

When an account exists:

```
xcrun notarytool store-credentials eki --apple-id you@example.com \
  --team-id TEAMID --password <app-specific-password>
```

then set `DEVELOPER_ID` and `NOTARY_PROFILE` as above. Nothing in the build
changes — `mac/sign.sh` signs every Mach-O inside the bundle with that identity
and applies `mac/eki.entitlements` (hardened runtime, library validation off,
because the bundled Python loads its own extension modules).

## Gotchas worth remembering

- **Never `codesign --deep`.** It walks the bundled interpreter, decides
  `lib/python3.12` is a malformed bundle, and refuses the whole app.
- **The interpreter lives in `Contents/Resources`, not `Contents/Helpers`.**
  codesign scans Helpers for nested code; sealed as resources it signs
  cleanly. The launcher scripts in Helpers are what launchd starts.
- **Clear extended attributes before sealing, never after.** codesign stores a
  script's signature in an xattr, and caches file hashes in them; a stale cache
  produces "a sealed resource is missing or invalid" at verify time.
- **Sign one identity throughout.** Mixing ad-hoc and signed binaries makes
  dyld refuse Python's extension modules with "different Team IDs".
- Verify with `codesign --verify --strict Eki.app` — no `--deep`.

## Automatic updates

Not wired up yet. When there is a Developer ID and somewhere to host an
appcast, [Sparkle 2](https://sparkle-project.org) is the fit: add the framework
to `Contents/Frameworks`, sign it with the same identity (`sign.sh` already
walks that folder), publish `appcast.xml` beside the zips, and sign each zip
with an EdDSA key. Until then, a release is a zip people download.

## Checklist

1. `.venv/bin/python -m pytest -q`
2. Bump `VERSION`, note what changed
3. `./mac/package.sh` (with signing variables on a real release)
4. Open the packaged app from a clean account or a fresh `HOME`: onboarding,
   engine at login, one question answered
5. Publish the zip and its `.sha256`

## Running the engine from the checkout instead

The packaged app registers a launch agent that runs *its own* copy of the
engine, so edits to a checkout have no effect until the app is rebuilt. To
develop against the checkout again:

```
launchctl bootout gui/$UID/local.eki.engine
./eki.sh agent install
```

and to go back, open the packaged app and turn "keep it running" on again.
