#!/bin/bash
# Run on the Mac, from ios-client. Stops at the first failure.
#   ./tools/build-mac.sh                  checks tools, runs the logic tests
#   ./tools/build-mac.sh TEAMID           also builds the app for a real iPhone (a Simulator can't run a Network Extension)
#   ./tools/build-mac.sh TEAMID archive   also makes an archive. It uploads nothing
set -euo pipefail
cd "$(dirname "$0")/.."
TEAM="${1:-}"
ACTION="${2:-}"
COMMIT="$(cat BUILD-INFO.txt 2>/dev/null | awk '{print $2}' | cut -c1-12 || true)"

command -v xcodebuild >/dev/null || { echo "Install Xcode from the App Store first."; exit 1; }
command -v go >/dev/null || { echo "WireGuardKit builds with Go. Run: brew install go"; exit 1; }
xcodebuild -version | head -1
go version

echo "=== Source and project checks"
python3 tools/check_sources.py

echo "=== Logic tests: no iPhone or signing needed"
(cd Packages/KiwiCore && swift test)

if [ -z "$TEAM" ]; then
  echo "Logic tests passed. To build the app, run again with your 10-character Apple Team ID."
  exit 0
fi

echo "=== Building for a real iPhone"
xcodebuild -project KiwiVPN.xcodeproj -scheme KiwiVPN -configuration Debug -destination 'generic/platform=iOS' \
  -allowProvisioningUpdates DEVELOPMENT_TEAM="$TEAM" KIWI_COMMIT="$COMMIT" build

if [ "$ACTION" = "archive" ]; then
  xcodebuild -project KiwiVPN.xcodeproj -scheme KiwiVPN -configuration Release -destination 'generic/platform=iOS' \
    -allowProvisioningUpdates DEVELOPMENT_TEAM="$TEAM" KIWI_COMMIT="$COMMIT" -archivePath build/KiwiVPN.xcarchive archive
  echo "Archive: build/KiwiVPN.xcarchive. Nothing was uploaded."
fi
