# App Store submission pack, Kiwi VPN for iPhone 0.2.0

Prepared 2026-11-20. **Nothing here has been submitted or uploaded, and no Apple account action has been taken.** Every answer below comes from the code on `feat/kiwi-ios-selective` and the backend it talks to. Re-check each one against the build you actually submit, and against Apple's pages on the day.

## 1. Before anything else: what only the owner can do
1. **Enrol in the Apple Developer Program as an organization.** Guideline 5.4: VPN apps "may only be offered by developers enrolled as an organization". That needs a registered legal entity, a **D-U-N-S number** for it (free from Dun & Bradstreet through Apple's lookup, and it can take days), a website on the entity's domain, and someone with legal authority to bind it. An individual account can test on your own iPhone and can't publish this app.
2. **Fill in the privacy policy's owner facts.** `/vpn/privacy` still shows notices for the legal entity, address, business email and transfer safeguard (`OWNER` in `frontend/src/website/community.tsx`). Guideline 5.1.1(i) needs a complete policy, linked in the app and in App Store Connect. The iPhone section added on this branch also needs deploying.
3. **A review account that works in the app** (see section 4). Today's reviewer account can't make a WireGuard device.
4. **App Store Connect API key for Codemagic**, created by the Account Holder or an Admin (Users and Access > Integrations > App Store Connect API), role App Manager, stored in Codemagic as the integration named `kiwi`. Only after enrolment.
5. **A real iPhone test through TestFlight internal testing,** before any review: `docs/IPHONE-TEST-PLAN.md`. Uploading to TestFlight needs your approval, since it sends the build to Apple.
6. **Legal advice on storefronts** (section 6) and the **encryption answers** (section 7).

## 2. App Privacy, the "nutrition label"
App Store Connect > App Privacy. Apple's definitions: <https://developer.apple.com/app-store/app-privacy-details/>. "Collect" means data sent off the iPhone and kept longer than it takes to answer the request.

**Tracking: No.** The app has no advertising, analytics or third-party SDK, and nothing is linked with other companies' data for advertising or sold to a data broker.

**Data Linked to You,** all for the purpose **App Functionality** only (Apple's App Functionality includes authenticating, preventing fraud and security). Nothing is used for advertising, marketing, analytics or personalisation.

| Apple's data type | What it really is | Where it comes from in the code |
|---|---|---|
| **Identifiers > User ID** | Passport member number and username | `members`; `GET /api/members/me` |
| **Identifiers > Device ID** | Each device's WireGuard public key, and the app's random install ID inside the device name (`iPhone abcd1234 (2)`) | `vpn_devices.public_key`, `vpn_devices.name`; `Provisioner.swift` `deviceName` |
| **Usage Data > Other Usage Data** | Bytes each device used this month, and its last handshake time (to 5 minutes) | `vpn_devices.rx_bytes`, `tx_bytes`, `last_handshake` |
| **Contact Info > Email Address** | Declare it to be safe. The Passport may hold an email, entered on pricechat.live during sign-in, and starting a trial stores a salted hash of it, which Apple's definition ("including ... a hashed email address") covers | `members.email`; `vpn_trial_claims` (`app/vpn/trial_guard.py`) |
| **Contact Info > Name** | Optional to declare, safer to: the Passport's display name, shown in the app | `members.display_name` |
| **Other Data** | Declare to be safe: the device's private tunnel address; free-trial fingerprints of linked Google, X or Telegram accounts; a salted network fingerprint if a trial check fires; IP addresses in our hosting providers' request logs and the servers' security logs, kept for security | `vpn_devices.ip`; `vpn_trial_signals.net_hash`; privacy policy "What we don't keep" |

**Not collected, and why:**
| Data type | Why not |
|---|---|
| Browsing History | No destinations or DNS queries are stored. The server sees destinations only in memory while a connection is open |
| Location | Nothing derives location. The exit-address check (`GET /api/members/vpn/ip`) answers and stores nothing |
| Purchases | The app sells nothing. **If paid plans are sold on the website and the app shows the plan, revisit this:** payment records exist for website payments |
| Diagnostics | The iPhone app sends no crash, performance or diagnostic data. It has no support-report screen |
| User Content, Contacts, Health, Financial, Sensitive, Search History | None |

## 3. Data declaration screen (Guideline 5.4)
5.4 requires "a clear declaration of what user data will be collected and how it will be used on an app screen prior to any user action to purchase or otherwise use the service". `DeclarationView` in `KiwiVPN/Views/RootView.swift` is the first screen, before sign-in, and was checked line by line on 2026-11-20 against the backend models and `/vpn/privacy`:

| Line | Backed by |
|---|---|
| Passport username and member number | `members` |
| Device name, public key, tunnel address | `vpn_devices.name`, `public_key`, `ip` |
| Last connection, data used this month | `last_handshake`, `rx_bytes`/`tx_bytes`, `usage_month` |
| Trial fingerprints, kept after deletion | `vpn_trial_claims`, which survive `delete_member` |
| IP address in server memory, not the database; security logs | WireGuard endpoint; policy "What we don't keep" |
| Hosting request logs (Railway, Vercel) | policy, audit L5 |
| DNS by the server's resolver or Cloudflare | policy "Your DNS lookups" |
| No history of sites; private key never leaves the iPhone | `Provisioner` sends only the public key; tests check it |
| Doesn't sell, advertise with, or disclose data | **Your commitment.** The policy must say it too (5.4) |
| Delete your account in Settings | `DeleteAccountView` |

**Gaps fixed in 0.2.0:** trial fingerprints, hosting logs, DNS resolvers, server security logs, and account deletion weren't on the screen. "Doesn't sell or share with third parties" was reworded, because hosting providers do process the data on our behalf.

## 4. App Review notes, template
Fill the gaps and paste into App Review Information > Notes.

> Kiwi VPN is a WireGuard VPN for members of Price Chat (pricechat.live), provided by [LEGAL ENTITY], enrolled as an organization.
>
> **Sign in:** tap Sign in. A secure browser sheet (ASWebAuthenticationSession) opens pricechat.live. Use the review account below, then tap Allow. The app never sees the password.
> Review account: [EMAIL] / [PASSWORD]. [HOW THE REVIEWER REACHES PASSWORD SIGN-IN IN THE SHEET]
>
> **Connect:** choose a country on Locations, then tap Connect on Home. iOS asks to add a VPN configuration: tap Allow. The status reads Protected once the server has answered.
>
> **What the app collects** is shown on the first screen, before sign-in, as Guideline 5.4 requires.
>
> **Every app on the iPhone uses the VPN.** The app doesn't offer per-app selection, which iOS reserves for MDM-managed devices; the screen "What uses Kiwi VPN on iPhone" explains this before sign-in.
>
> **Account deletion:** Settings > Delete account. Type the username shown to confirm. This deletes the Price Chat account on our server, removes the VPN configuration and the keys from the iPhone, and returns to the sign-in screen.
>
> **Purchases:** the app sells nothing and has no purchase screen.
>
> **Licences:** [LICENCE INFORMATION FOR ANY TERRITORY THAT REQUIRES ONE, OR "NONE"]

**The review account is a blocker.** `backend/app/vpn/reviewer.py` makes one account for the **browser extension's** store reviewers: email and password through `/vpn/reviewer`, Helsinki 1 only, one device, and `device_precheck` refuses WireGuard devices with "This review account is for the browser extension only." The iPhone app signs in through `/vpn/desktop`, which offers the normal Passport sign-in (email code, Google, and so on), and its sign-in makes WireGuard devices. So an App Reviewer today can't sign in with a password, and couldn't connect if they did. Options, each needing your decision and a deploy:
1. Let the review account make **one** WireGuard device from an app session on Helsinki 1, and give `/vpn/desktop` a way to reach the password sign-in. Smallest change, and it keeps the time limit and the single server.
2. A separate tester account (Admin's tester access) with an email-code sign-in to a mailbox Apple can reach. Apple asks for credentials that work without extra steps, so this is weaker.

## 5. Guideline 5.4, the rest of it
- **NEVPNManager API:** the app uses `NETunnelProviderManager`, a subclass of `NEVPNManager`, with a Packet Tunnel Provider. Correct for a custom protocol such as WireGuard.
- **"May not sell, use, or disclose to third parties any data for any purpose, and must commit to this in their privacy policy."** The policy says "we don't sell your data or use it for advertising". **Add the 5.4 commitment in so many words** before submitting, and have someone check the processors section reads as processing on your behalf, not disclosure.
- **"Must not violate local laws"** and licence information for territories that need one: section 6.

## 6. Storefronts
Exclude these until a lawyer says otherwise (App Store Connect > Pricing and Availability):
| Storefront | Why |
|---|---|
| **China mainland** | VPN services need government approval there. Apple removed VPN apps from the China storefront in 2017, and 5.4 requires a licence number in the notes for territories that need one |
| **United Arab Emirates** | Laws restrict VPN use in the UAE. Price Chat's own operating location makes this one particularly important to get advice on |

Also ask the lawyer about Russia (its regulator has had VPN apps removed from the Russian storefront), Iran, Belarus, Turkmenistan, Oman, Qatar and others, and about any country where Price Chat operates. **This list is not legal advice:** confirm each one.

## 7. Export compliance (encryption)
Apple: "If your app uses, accesses, contains, implements, or incorporates encryption, this is considered an export of encryption software", subject to U.S. export rules wherever your company is (<https://developer.apple.com/documentation/security/complying-with-encryption-export-regulations>).

**What the app does:** besides HTTPS through Apple's URLSession, the tunnel extension contains **WireGuard's own implementation** of standard, published algorithms (Curve25519, ChaCha20-Poly1305, BLAKE2s, in wireguard-go compiled into the extension). That's encryption **implemented in the app, not provided by the operating system.** It isn't proprietary.

**Apple's documentation table** (<https://developer.apple.com/help/app-store-connect/reference/export-compliance-documentation-for-encryption/>):
| Encryption in use | Documentation Apple asks for |
|---|---|
| Only within the Apple operating system | None |
| **An industry standard algorithm, not provided within the Apple operating system** (this app) | **French encryption declaration, only if distributed in France** |
| Proprietary algorithms not accepted by standards bodies | CCATS, and the French declaration if in France |

So, from Apple's table: **no CCATS is needed**, and **either exclude France or file the French declaration** (with ANSSI) and upload it.

**`ITSAppUsesNonExemptEncryption`:** deliberately **not set** in `Info.plist` yet. Apple says to set `NO` only if the app uses no encryption "or if it only uses forms of encryption that are exempt from export compliance documentation requirements", and `YES` otherwise. Which applies depends on the France decision and on the U.S. classification below. Without the key, App Store Connect asks the questions at each upload, which is the safe choice until those are settled. Once answered, set the key so later builds don't ask again.

**U.S. classification, for your adviser:** a consumer VPN using standard encryption is generally treated as a "mass market" item under ECCN 5D992.c, eligible for License Exception ENC under EAR §740.17(b)(1). Apple's page notes some apps must file an **annual self-classification report** with BIS, and that you're "responsible for all liabilities associated with misinterpretation of export regulations". BIS changed the reporting rules in 2021, and I haven't confirmed from BIS's own text whether a report applies here. **Ask an export adviser,** and record the answer in this file. ERN (Encryption Registration Number) registration was abolished by BIS in 2016, so it no longer applies.

## 8. Other checklist items, status in 0.2.0
| Item | Status |
|---|---|
| Account deletion in the app (5.1.1(v)) | **Built:** Settings > Delete account, `DeleteAccountView` and `KiwiCore/AccountDeletion.swift`, with unit tests. Calls `DELETE /api/members/me` with `{"confirm_username"}`; the app's `kd.` session may, and `backend/tests/test_ios_contract.py` checks it. **Not run on an iPhone** |
| Data declaration (5.4) | Built and updated, section 3 |
| Privacy policy link in the app | Declaration screen and Settings |
| Per-app claims | None made: `docs/SELECTIVE-VPN-IOS.md` |
| Open-source licences | **Built:** Settings > Licences (WireGuard MIT, Go BSD). Check the texts against the exact versions shipped |
| In-app purchase | None. Read Guideline 3.1 before ever selling access in the app |
| Screenshots | Need a real iPhone, after the test plan |
| Version | 0.2.0, build 2 |
