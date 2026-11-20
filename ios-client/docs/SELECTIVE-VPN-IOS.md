# Selective VPN on iPhone: what Apple allows

Researched 2026-11-20 from Apple's own documentation, App Review Guidelines and answers by Apple DTS engineers on the Developer Forums. Re-check before submitting: Apple changes these pages.

## Conclusion
**A consumer App Store VPN app on an ordinary, personal iPhone can't choose which other apps use its tunnel.** Per-app VPN on iOS exists, and only through MDM: an organisation's device management installs a `com.apple.vpn.managed.applayer` profile and links it to apps that the MDM itself installed. The app can read those rules, and can't create them.

So **Kiwi VPN for iPhone routes every app**, says so before sign-in, and shows no app list. Android's per-app feature has no iPhone equivalent for our customers.

**What iOS does allow a consumer app:** routing by **destination address** (`includedRoutes` / `excludedRoutes`), split DNS for name lookups only (`matchDomains`), and **On-Demand** rules that decide *when* the tunnel runs (Wi-Fi, mobile data, SSID, domains), not *which app* uses it. The one "messaging only" candidate, a Telegram-only mode built from Telegram's published address ranges, was evaluated and **not built**: see below.

## Evidence

### 1. Apple's NETunnelProviderManager documentation
<https://developer.apple.com/documentation/networkextension/netunnelprovidermanager>, section "Routing Network Data to the VPN":

> There are two ways or methods by which network data is routed to the VPN: By destination IP address; By source application (Per-App VPN)

> **The only way to configure Per-App VPN is by enrolling the device in a Mobile Device Management (MDM) system, and then linking apps that are managed by the MDM system with a VPN configuration created from a `com.apple.vpn.managed.applayer` configuration profile payload.**

> The MDM client running on the device creates one app rule in the VPN configuration for each managed app that is linked to the VPN configuration via the `VPNUUID` app attribute.

> It is not possible to create app rules for Apple system apps. The one exception to this rule is Safari.

On testing without MDM: the `NETestAppMapping` Info.plist key "can only be used to create app rules in apps that are signed with a Development provisioning profile. In apps that are signed with Distribution provisioning profiles the `NETestAppMapping Info.plist` key has no effect." So it can't ship.

### 2. TN3134, Network Extension provider deployment (revised 2025-08-19)
<https://developer.apple.com/documentation/technotes/tn3134-network-extension-provider-deployment>

| Provider, iOS | Restriction in Apple's table |
|---|---|
| Packet tunnel (what Kiwi VPN uses) | "per-app mode requires managed device" |
| App proxy | "managed devices only" |
| DNS proxy | "supervised devices only", or "per-app on managed devices" (iOS 16) |
| Content filter | "supervised devices only", Screen Time child devices, or per-app on managed devices |
| Transparent proxy | macOS only |

And: "When a provider's per-app configuration is restricted to managed devices, the targeted apps must be installed using MDM."

### 3. The per-app APIs themselves
- `NETunnelProviderManager.forPerAppVPN()`: Apple's availability metadata lists **macOS 10.15.4 only**. <https://developer.apple.com/documentation/networkextension/netunnelprovidermanager/forperappvpn()>
- Apple's article "Routing your VPN network traffic": "For macOS apps, use the `appRules` property to associate MDM-managed apps with the per-app VPN configuration ... **For iOS apps, create rules using the MDM system and access the rules in your code using the `copyAppRules()` method.**" <https://developer.apple.com/documentation/networkextension/routing-your-vpn-network-traffic>
- `NEAppRule` exists on iOS, but on iOS it's how the MDM-made rules are represented, not a way for an app to make them.

### 4. Apple DTS engineers on the Developer Forums
- Quinn "The Eskimo!", DTS, March 2017, <https://developer.apple.com/forums/thread/73657>: "per-app VPN configurations can't be created programmatically, they must be set up by a configuration profile (in addition, on iOS at least, they require MDM)." And on app rules: "the MDM client is a subsystem of iOS that creates these app rules internally. These are not things that you can configure directly."
- Quinn, August 2023, <https://developer.apple.com/forums/thread/736623>: testing without MDM "only works for development-signed code. For distribution-signed code you must use MDM."
- Matt Eaton, DTS, February 2021, <https://developer.apple.com/forums/thread/674187>, asked how to let only WhatsApp use a VPN: "forPerAppVPN() is only available on macOS. A more real-world case for iOS would be to create this process through MDM."
- Matt Eaton, February 2022, <https://developer.apple.com/forums/thread/700075>, asked about excluding apps by bundle ID: "Not exactly". His options: per-app VPN with MDM; "a split tunnel for only the traffic you want to secure ... via destination IP instead of process. This option also does not require MDM"; and a transparent proxy, which is macOS only.

### 5. App Review Guidelines
<https://developer.apple.com/app-store/review/guidelines/>, 5.4: "Apps offering VPN services must utilize the NEVPNManager API and may only be offered by developers enrolled as an organization." Nothing in the guidelines offers consumer apps per-app routing.

## What a consumer app can do, and whether Kiwi VPN does it

| Mechanism | Supported for an App Store app? | What it really does | In Kiwi VPN 0.2.0 |
|---|---|---|---|
| Full tunnel (`0.0.0.0/0`, `::/0`) | Yes | All apps, except what iOS keeps out | **Yes, the only mode** |
| `includedRoutes` / `excludedRoutes` | Yes | By destination address, for every app alike | No: see Telegram below |
| `enforceRoutes` (iOS 14.2) | Yes | Makes the route lists win over the routing table and apps' own scoping. Ignored when `includeAllNetworks` is on | No |
| `includeAllNetworks` (iOS 14) | Yes | More traffic forced in. Apple still excludes DHCP, captive portals, cellular-only services such as VoLTE, and companion-device traffic | Yes: "Send all traffic", not called a kill switch |
| `excludeLocalNetworks`, `excludeCellularServices`, `excludeAPNs` | Yes | Exceptions, only with `includeAllNetworks` | Local network only |
| `NEDNSSettings.matchDomains` | Yes | Split **DNS**: which names are *resolved* by the tunnel's DNS server. It moves no traffic. Apple: if the tunnel is the default route, the list is ignored | No |
| `NEOnDemandRule` (interface type, SSID, DNS domain or server, probe URL) | Yes | *When* the tunnel runs. Once up, routing is the same for every app | Yes: Off, Always, Wi-Fi, mobile data |
| `NEAppRule`, per-app VPN | **Only with MDM and MDM-installed apps** | By source app | **No** |
| `NEDNSProxyProvider` | **Supervised devices, or per-app on managed devices** | Sees all DNS | No |
| `NEAppProxyProvider` | **Managed devices only** | Per-app proxying | No |
| Shortcuts "App is opened" automation | Yes (iOS itself) | Connects when an app opens. Every app uses the tunnel while it's on | Yes: Settings > Auto-Call VPN on iPhone |

### Domain-based routing: no supported way
A packet tunnel has no per-domain routing. The only route to it would be a trick: the tunnel answers DNS itself, then adds each resolved address to `includedRoutes` by applying new network settings while running. It's not reliable: apps reuse cached addresses or use their own encrypted DNS, which the tunnel never sees; one address often serves many unrelated sites on a CDN; and every change rebuilds the tunnel's settings while traffic flows. The DNS proxy provider that could do this properly needs a supervised device. **Not built, and not to be promised.**

## Destination-based "messaging only": evaluated, not built

### Telegram
Telegram publishes <https://core.telegram.org/resources/cidr.txt>. Fetched 2026-11-20: 9 IPv4 and 5 IPv6 ranges, served with **`Last-Modified: Fri, 01 Oct 2021`**:
```
91.108.56.0/22  91.108.4.0/22  91.108.8.0/22  91.108.16.0/22  91.108.12.0/22  149.154.160.0/20
91.105.192.0/23  91.108.20.0/22  185.76.151.0/24
2001:b28:f23d::/48  2001:b28:f23f::/48  2001:67c:4e8::/48  2001:b28:f23c::/48  2a0a:f280::/32
```
A "Telegram only" mode routing just these would be possible with public APIs. **It's not built, because it would mislead people about the very thing they'd use it for:**
1. **Calls between contacts leave the tunnel.** Telegram connects calls with contacts peer to peer by default, straight to the other person's address, which isn't in these ranges. In this mode those calls would go outside the VPN, showing the other person your real IP address, and on a network that blocks calls, being blocked. Telegram said in October 2023 that calls with contacts are peer to peer and that the setting Settings > Privacy and Security > Calls > Peer-to-Peer can be turned off (reported by TechCrunch, <https://techcrunch.com/2023/10/19/telegram-is-still-leaking-user-ip-addresses-to-contacts/>). Kiwi VPN can't change another app's settings.
2. **Media can come from elsewhere.** Telegram's own documentation says its CDN data centres "are not a part of the Telegram cloud" and are placed "in regions with significant Telegram traffic where we wouldn't want to place Telegram servers" (<https://core.telegram.org/cdn>). Nothing says they're inside the published ranges.
3. **The list has no stated guarantee.** The file has no version, no "complete" statement and no change notice, and hasn't changed since 2021.
4. **Links, previews and DNS stay outside.** `t.me`, web previews and Telegram's name lookups use the normal connection.
5. **Our own safety rule would have to bend.** `PeerRules` refuses any server answer that isn't exactly `0.0.0.0/0` and `::/0`, so a bad answer can't quietly route traffic around the tunnel. This mode would need a second, narrower rule set on the client.

If the owner still wants it, it would have to be labelled something like "Telegram messages only (by Telegram's published network ranges)", with the call warning on the same screen, and tested on a real iPhone before release.

### WhatsApp and Signal
- **WhatsApp / Meta:** no official WhatsApp address list was found on Meta's developer or help sites. A search result quoted Meta's Workplace networking page as saying its address list "is subject to change" and "should not be considered exhaustive", and that allowing by IP "is not supported", but that page now redirects (Workplace is being closed), so I couldn't confirm the wording myself. Third-party lists of Meta's network (AS32934) are unofficial, and cover every Meta service, not WhatsApp alone.
- **Signal:** Signal's firewall help (updated 2026-10-19) says "Allow traffic to and from *.signal.org on TCP port 443 and all UDP ports", plus `signal.art`, `signal.group`, `signal.link`, `signal.me` and `signal.tube`, and for calls `turn3.voip.signal.org` and `sfu.voip.signal.org`, which "are subject to change at anytime". It's all by **domain**, with no address list (<https://support.signal.org/hc/en-us/articles/360007320291-Firewall-and-Internet-settings>, read through the help centre's API because the page refuses automated fetches). Its one-to-one call recommendation, "all UDP" ports, suggests direct connections too, which no address list could cover.

**So no WhatsApp-only or Signal-only mode is offered,** and the app doesn't claim one.

## Recommendation
1. Keep the full tunnel as the only mode. Done in 0.2.0, with the "What uses Kiwi VPN on iPhone" screen before sign-in, and again in Settings.
2. Keep `app_routing_enabled` off for iOS in `backend/app/vpn/unified.py`. It is: `backend/tests/test_ios_contract.py` and `test_vpn_unified.py` check it.
3. Don't market per-app or "messaging only" for iPhone. The App Store description and screenshots must say every app uses the VPN.
4. Possible later, all supported, none built: an On-Demand "except on these Wi-Fi networks" rule (`ssidMatch`, typed by the person, since reading the current SSID needs location permission and an entitlement); `enforceRoutes` for the full tunnel, after testing.
5. Per-app VPN for organisations is possible if Price Chat ever sells to companies with MDM. The app would need `com.apple.vpn.managed.applayer` support and testing with `NETestAppMapping`. Not a consumer feature.
