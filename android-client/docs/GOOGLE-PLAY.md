# Google Play readiness

**Not submitted, and not compliant yet.** This is what the rules ask for as read on 2026-11-05. Google's VpnService page carries no date, and policy changes, so re-read each page just before submitting. Sources are listed at the end.

## VpnService declaration
The rule, from Google's page: "Only apps that use the VpnService and have VPN as their core functionality can create a secure device-level tunnel". Kiwi VPN answers yes to "Is providing a VPN the core functionality".

Also required:
- A declaration form in Play Console. Changing how the API is used means submitting it again.
- Documenting the use of VpnService in the store listing.
- Encrypting traffic from the device to the tunnel endpoint. WireGuard does this.
- A video of 90 seconds or less showing the app opening and the VPN in use.

Forbidden:
- Redirecting or manipulating other apps' traffic for monetization, including manipulating ads.
- Collecting personal or sensitive data without prominent disclosure and consent.

**One to check:** the server's `adblock` option. Google's page says "Any manipulation of ads that can impact monetization will result in rejection", and news reports describe a tightening effective around 1 November 2026. If any server blocks ads through its resolver, read this carefully before submitting. **It could cause rejection.**

## Foreground service
The app uses `systemExempted`, because Android's documentation lists "VPN apps" among those allowed. Declare the type under Policy > App content. If Google questions it, `specialUse` with a written explanation is the alternative, and both need reviewing at submission time.

## Notification access (Auto-Call VPN)
- Reading notifications needs the in-app prominent disclosure Google describes: inside the app, shown during normal use, separate from other disclosures, and needing an action to accept. The consent dialog is written to that shape, but it appears when turning the feature on, so **a reviewer must confirm it's enough**.
- Google's VPN page says nothing about notification listeners. The general permissions and user data policies apply.
- **Risk:** a VPN asking to read notifications may draw scrutiny. Consider leaving it out of the first Play release.

## Data safety form: a draft to check, not an answer
| Data | Collected? | Purpose |
|---|---|---|
| Account identifier (session, username) | Yes, by the existing account | App function |
| Device name (phone model) | Yes, on device registration | Showing it under My Devices |
| WireGuard public key, tunnel IP | Yes | Running the VPN |
| Last handshake time, bytes used | Yes: `sync_server` records them for each device | Showing use, monthly caps |
| IP address | The VPN server necessarily sees it; `/members/vpn/ip` returns it unstored | App function |
| Browsing history, DNS queries | **Not collected by the app.** What the server's resolver logs must be confirmed: the code comment says "no logs" and this has not been audited | |
| Notification content, caller identity | **Never collected:** read on the phone, and only call signals | |

Encrypted in transit: yes. Deleting data: check that deleting a member removes VPN devices before saying so.

## Installed apps (App-Based VPN)
Listing apps uses a `<queries>` MAIN/LAUNCHER intent, **not** `QUERY_ALL_PACKAGES`, the permission Google Play restricts. The list and the choices stay encrypted on the phone and are never sent anywhere. Say so in the Data safety form.

## Billing
Payments are off, and the app shows no purchase. If paid access is sold inside an Android app distributed through Play, Google Play's billing rules apply to digital goods. Whether existing website payments (card, USDT) may be linked from the app depends on the program and region. **Check it and take advice before turning on payments.** Nothing's implemented.

## Privacy policy
`/vpn/privacy` exists, but it needs checking against the Android app and needs a section on Auto-Call VPN.

## App signing
Play App Signing is required for new apps: you upload with an upload key, and Google holds the signing key. See `SIGNING.md`. Play distributes App Bundles, which needs `bundleRelease` added to the build script.

## Store assets still needed
Icon at 512 px, feature graphic at 1024 x 500, phone screenshots, short and full descriptions (with no claims about kill switches or leak protection until tested), support contact, and content rating.

## Licences
Show the Apache-2.0 notice for the WireGuard library and other dependencies in the app. Not done yet.

## Sources
- [VpnService policy](https://support.google.com/googleplay/android-developer/answer/12564964)
- [Foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types)
- [Foreground service requirements in Play Console](https://support.google.com/googleplay/android-developer/answer/13392821)
- [Report on the 2026 VPN and ads change (secondary)](https://blog.desdelinux.net/en/google-will-limit-the-capacity-of-vpns-that-filter-traffic-and-ads-in-the-play-store/)
- [Restricted settings on Android 15 (secondary)](https://androidauthority.com/android-15-restricted-settings-sideloading-3481098)
