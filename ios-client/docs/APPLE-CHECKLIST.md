# Apple signing, entitlements and App Store checklist

Checked 2026-11-05, updated 2026-11-20 for 0.2.0. Re-read Apple's pages just before submitting. **The full submission pack, with App Privacy answers, review notes, storefronts and export compliance, is `APP-STORE-SUBMISSION.md`.**

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
- [ ] Organization enrolment complete, with the entity's D-U-N-S number
- [x] Data declaration before use (5.4): `DeclarationView`, rechecked 2026-11-20 against the backend models and `/vpn/privacy`, with trial fingerprints, hosting logs, DNS resolvers and account deletion added. **You must still confirm every line is true,** and "doesn't sell, use or disclose" is your commitment to make
- [ ] Privacy policy complete: the `[OWNER: ...]` facts filled in, the 5.4 commitment in so many words, and the iPhone section on this branch deployed
- [x] Privacy policy link in the app (declaration screen and Settings). [ ] And in App Store Connect
- [ ] App Privacy details: answers in `APP-STORE-SUBMISSION.md` section 2
- [x] Account deletion in the app (5.1.1(v)): Settings > Delete account, built in 0.2.0, with unit tests and a backend test. **Not yet run on an iPhone:** test plan 32 and 33
- [x] No per-app or "messaging only" claims: `SELECTIVE-VPN-IOS.md`. Keep the App Store description and screenshots consistent with it
- [ ] Export compliance: `APP-STORE-SUBMISSION.md` section 7. Decide France (exclude, or file the French declaration), then set `ITSAppUsesNonExemptEncryption`
- [ ] Storefronts: exclude China mainland and the UAE until legal advice; ask about the others listed in section 6
- [ ] Payments: the app sells nothing and has no purchase button. If paid access is sold in an iOS app later, read the in-app purchase rules first
- [x] Licence notices: Settings > Licences (WireGuard MIT, Go BSD). Check the texts against the shipped versions
- [ ] App Review notes and the **review account**: built and tested on `feat/kiwi-mobile` (one WireGuard device from the app, Helsinki 1, scoped session). Still to do: deploy, set the variables, try it once on a real iPhone (`docs/KIWI-STORE-REVIEW-ACCOUNT.md`)
- [ ] App Store Connect API key for Codemagic (integration `kiwi`), after enrolment
- [ ] Screenshots from a real device
- [ ] Beta testing through TestFlight internal testing, with your approval to upload

## Not done, and not to be done without your approval
Nothing has been submitted or uploaded, and no certificate or App ID has been made.
