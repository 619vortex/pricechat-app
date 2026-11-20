#!/bin/bash
# Run by the cloud build, with the App Store Connect key from Codemagic's integration. Safe to run again: it only adds what's missing.
# It makes App IDs for the app and its tunnel, turns on Network Extensions for both, and registers the one iPhone. It uploads no app.
set -euo pipefail

: "${KIWI_IPHONE_UDID:?Add KIWI_IPHONE_UDID to the kiwi_signing group}"

resource_id() {
  app-store-connect bundle-ids list --bundle-id-identifier "$1" --strict-match-identifier --platform IOS --json \
    | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["id"] if rows else "")'
}

for pair in "live.pricechat.kiwivpn|Kiwi VPN" "live.pricechat.kiwivpn.tunnel|Kiwi VPN Tunnel"; do
  identifier="${pair%%|*}"
  name="${pair##*|}"
  id="$(resource_id "$identifier")"
  if [ -z "$id" ]; then
    app-store-connect bundle-ids create "$identifier" --name "$name" --platform IOS >/dev/null
    id="$(resource_id "$identifier")"
  fi
  [ -n "$id" ] || { echo "Couldn't find or make the App ID $identifier"; exit 1; }
  # Fails harmlessly if it's on already. verify-ipa.sh is what proves the entitlement reached the signed app.
  app-store-connect bundle-ids enable-capabilities "$id" --capability "Network Extensions" >/dev/null || echo "Network Extensions: not changed on $identifier (it may be on already)"
  echo "App ID ready: $identifier"
done

app-store-connect devices register --name "Kiwi test iPhone" --udid "$KIWI_IPHONE_UDID" --platform IOS --ignore-registration-errors >/dev/null
echo "iPhone registered, or already was"
