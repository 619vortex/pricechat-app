#!/bin/bash
# Run by the cloud build after signing. Fails the build unless the IPA can really be used on the registered iPhone. It says nothing about whether the VPN works: only a real iPhone can show that.
set -euo pipefail
IPA="$1"
: "${KIWI_IPHONE_UDID:?KIWI_IPHONE_UDID is missing}"
WORK="$(mktemp -d)"
unzip -q "$IPA" -d "$WORK"
APP="$WORK/Payload/KiwiVPN.app"
EXT="$APP/PlugIns/KiwiTunnel.appex"
failed=0
ok() { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; failed=1; }

[ -d "$APP" ] && ok "the app is in the IPA" || bad "no KiwiVPN.app"
[ -d "$EXT" ] && ok "the tunnel extension is in the app" || bad "no KiwiTunnel.appex"
[ "$failed" = 0 ] || exit 1

for bundle in "$APP" "$EXT"; do
  label="$(basename "$bundle")"
  codesign --verify --strict "$bundle" && ok "$label: the signature verifies" || bad "$label: the signature doesn't verify"
  ent="$(codesign -d --entitlements :- "$bundle" 2>/dev/null)"
  grep -q "packet-tunnel-provider" <<<"$ent" && ok "$label: packet tunnel entitlement" || bad "$label: no packet tunnel entitlement"
  grep -q 'AppIdentifierPrefix' <<<"$ent" && bad "$label: the keychain group wasn't filled in" || ok "$label: keychain groups filled in"
  profile="$(security cms -D -i "$bundle/embedded.mobileprovision")"
  grep -q "$KIWI_IPHONE_UDID" <<<"$profile" && ok "$label: the profile includes this iPhone" || bad "$label: the profile doesn't include this iPhone"
  grep -q "<key>ProvisionsAllDevices</key>" <<<"$profile" && bad "$label: not an Ad Hoc profile" || ok "$label: the profile is limited to registered devices"
  grep -A1 "get-task-allow" <<<"$profile" | grep -q "<false/>" && ok "$label: not a debug signature" || bad "$label: a debug signature"
done

for plist in "$APP/Info.plist" "$EXT/Info.plist"; do
  label="$(basename "$(dirname "$plist")")"
  /usr/bin/plutil -convert xml1 -o - "$plist" | grep -q 'AppIdentifierPrefix' && bad "$label: Info.plist's keychain group wasn't filled in" || ok "$label: Info.plist's keychain group filled in"
done

/usr/bin/plutil -extract CFBundleShortVersionString raw "$APP/Info.plist" | sed 's/^/Version: /'
/usr/bin/plutil -extract CFBundleVersion raw "$APP/Info.plist" | sed 's/^/Build: /'
shasum -a 256 "$IPA"
rm -rf "$WORK"
exit "$failed"
