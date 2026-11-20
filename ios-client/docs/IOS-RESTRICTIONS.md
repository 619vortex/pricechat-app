# What iOS allows, and what the app therefore does

| Feature | Result | Why |
|---|---|---|
| Per-app VPN | **Not built, and no toggles shown.** The "What uses Kiwi VPN on iPhone" screen says every app uses it, before sign-in and in Settings | Apple's documentation: "The only way to configure Per-App VPN is by enrolling the device in a Mobile Device Management (MDM) system". A consumer app can't set it up. Sources and the alternatives considered: `SELECTIVE-VPN-IOS.md` |
| Telegram-only, WhatsApp-only, Signal-only | **Not built** | Telegram's published ranges miss peer-to-peer calls and CDN media; WhatsApp and Signal publish no address lists. `SELECTIVE-VPN-IOS.md` |
| Auto-Call VPN | **Not built, not promised, and Settings says why** | No public iOS API tells an app that WhatsApp or Telegram is in a call, and apps can't read another app's notifications. `CXCallObserver` is the nearest API: it's for the app while it's running, it doesn't say which app a call belongs to, and it doesn't wake a suspended app, so it can't start a VPN for a call that begins while Kiwi VPN isn't on screen |
| Kill switch | **Not claimed** | See "Send all traffic" below |
| Quantum resistance, obfuscation | **Not built, not shown** | Need server support that doesn't exist |
| Gaming Mode | **Models only, hidden** | Nothing can measure latency to the servers yet. The screen shows only if the server's capability turns on, and it shows no figures |
| Missions | **Shown only if the server sends them** | It sends none. XP is only ever awarded by the server |

## What's offered instead
- **On-Demand VPN** (`NEOnDemandRule`): Off, Always, On Wi-Fi, On mobile data. iOS starts the tunnel by itself using saved details. Apple evaluates rules in order, and the first match wins. **Pressing Disconnect turns On-Demand off,** or iOS would reconnect immediately. The person is told this, and pressing Connect restores it.
- **Shortcuts:** "Connect Kiwi VPN" and "Disconnect Kiwi VPN", for Siri and for a person's own automations, such as "when I open this app". **This is the closest honest answer to Auto-Call,** and Settings > Auto-Call VPN on iPhone gives the steps: a person can build an automation for opening WhatsApp, but that isn't call detection. The result only says the request was made, and the app shows the truth.
- **Manual connection and accurate status in the app.**

## Advanced settings: only ones with a real effect
| Setting | Effect | Tested? |
|---|---|---|
| DNS | `InterfaceConfiguration.dns`. Empty means the server's resolver. IP addresses only | No |
| MTU | `InterfaceConfiguration.mtu`, 1280 to 1420. Empty lets WireGuardKit choose | No |
| Send all traffic | `NEVPNProtocol.includeAllNetworks` | No |
| Allow local network | `excludeLocalNetworks`. Shown only with Send all traffic | No |
| Server | The Locations tab | No |

**"Send all traffic" isn't a kill switch, and isn't called one.** Apple says the system always leaves some traffic outside the tunnel: DHCP, captive portal negotiation, some cellular services such as VoLTE, and traffic to a companion device such as an Apple Watch. Other developers report side effects with this option, so it's off by default.

**IPv6 isn't a setting.** The server's routes always include `::/0`, and the app refuses a server answer without it, so IPv6 goes into the tunnel. On a server with no IPv6 address for the device, it should fail rather than leak. **That's what should happen. Test 10 is what will show it.**

**If the tunnel stops unexpectedly,** traffic goes outside the VPN, unless Send all traffic or On-Demand changes that. The app reports Connection failed once it's next on screen. It can't alert someone while closed.
