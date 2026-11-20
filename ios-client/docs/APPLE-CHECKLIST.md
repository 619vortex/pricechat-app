# Apple signing, entitlements and App Store checklist

Checked 2026-11-05. Re-read Apple's pages just before submitting.

## Blockers to decide first
1. **An organization account.** App Store Review Guideline 5.4, as published: VPN apps "may only be offered by developers enrolled as an organization". An individual account can test on your own phones and can't ship this to the App Store. This needs a registered legal entity, with a D-U-N-S number for enrolment. **Is "Price Chat" a registered company?** It's the same question as for the Windows certificate.
2. **Territories.** "VPN apps must not violate local laws, and if you choose to make your VPN app available in a territory that requires a VPN license, you must provide your license information in the App Review Notes field." **Check each country you list in, including where you operate.** Rules on VPNs vary, and this is one for a lawyer, not for me.
3. **No selling or disclosing data.** Apps "may not sell, use, or disclose to third parties any data for any purpose, and must commit to this in their privacy policy". Check `/vpn/privacy` says this. The Finland server shares your personal exit address with `client1`, which should be looked at against this too.

## Identifiers
| Item | Value |
|---|---|
| App bundle ID | `live.pricechat.kiwivpn` |
| Extension bundle ID | `live.pricechat.kiwivpn.tunnel` (must start with the app's) |
| Deployment target | iOS 17.0 |

Enable **Network Extensions** on both App IDs: Xcode's automatic signing does this when you add your Team. Apple staff say any paid team can use the packet tunnel entitlement with no special request. A free Personal Team shouldn't expect to, based on Apple's capability table: confirm in your own Xcode.

## Entitlements, already in the project
| Entitlement | App | Extension |
|---|---|---|
| `com.apple.developer.networking.networkextension`: `packet-tunnel-provider` | Yes | Yes |
| `keychain-access-groups`: `$(AppIdentifierPrefix)live.pricechat.kiwivpn.app` | Yes | **No** |
| `keychain-access-groups`: `$(AppIdentifierPrefix)live.pricechat.kiwivpn.shared` | Yes | Yes |

The extension has only the shared group, so it can read WireGuard keys and can't read the session. No App Group is used, because statistics travel by provider message. `check_sources.py` fails if this changes.

**To confirm on the first device run:** that `$(AppIdentifierPrefix)` expands inside `Info.plist`. If the app can't store its session, this is the first thing to check, and Keychain errors would show it.

## App Store Connect, when you get there
- [ ] Organization enrolment complete
- [ ] Data declaration before use. Guideline 5.4 requires "a clear declaration of what user data will be collected and how it will be used on an app screen prior to any user action". **`DeclarationView` is that screen. You must confirm every line is true.** I checked its use of device name, key, address, last handshake and data use against the code. "Doesn't sell or share" is your commitment to make
- [ ] Privacy policy link, in the app and in App Store Connect
- [ ] App Privacy details, matching the declaration
- [ ] Account deletion in the app. Guideline 5.1.1(v) expects apps with accounts to offer it. **This app doesn't yet:** it can sign out, and a person would go to the website. Check what the backend supports
- [ ] Export compliance: the app uses encryption. Answer it in App Store Connect
- [ ] Payments: the app sells nothing and has no purchase button. If paid access is sold in an iOS app later, read the in-app purchase rules first. Nothing's implemented
- [ ] Licence notices: WireGuardKit is MIT. Show the notice in the app. **Not done yet**
- [ ] App Review notes: a test account, and how to connect
- [ ] Screenshots from a real device
- [ ] Beta testing through TestFlight rather than sending an IPA

## Not done, and not to be done without your approval
Nothing has been submitted or uploaded, and no certificate or App ID has been made.
