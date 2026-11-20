# Google Play submission pack for Kiwi VPN 0.2.0

**Docs only. Nothing has been submitted, uploaded or published.** Answers are derived from the code on `feat/kiwi-android-selective` (Android app and `backend/`) on 2026-11-20. Google's forms and policies change: re-read each linked page on the day you submit. `GOOGLE-PLAY.md` has the earlier readiness notes and sources.

## 0. Blocker: an organization developer account
**Google Play only accepts VPN apps (apps using `VpnService`) from developer accounts registered as an organization**, not a personal account. That needs:
1. A legal entity (company) in the owner's name or control.
2. A **D-U-N-S number** for that entity (free from Dun & Bradstreet; it can take days to weeks).
3. A Play Console organization account ($25 one-time), verified with the D-U-N-S, the organization's website (pricechat.live) and a contact phone and email. Google verifies the website belongs to the organization.
4. New personal accounts also face a 12-tester / 14-day closed test rule. Organization accounts are exempt from that rule as of the last reading; check it on the day.

Nothing below can be submitted until this exists. **Owner action.**

## 1. Which file goes to Play
`tools/build-release.ps1` makes `KiwiVPN-0.2.0-<commit>-play.aab`: the **play** flavor. It is the same app (package `live.pricechat.kiwivpn`, same signing key) as the website APK, with **Auto-Call VPN removed**: its manifest has no notification listener, and the setting never appears (`BuildConfig.AUTO_CALL = false`). See section 7 for why.

**Play App Signing:** at the first upload, Play Console asks how the app is signed. To keep **one key for both the website APK and Play** (so Play updates install over the website build and vice versa), choose to **upload your existing app signing key** (Play's "export and upload a key from Java keystore" with its PEPK tool), using `%USERPROFILE%\.kiwi-android-signing\kiwi-release.keystore`. If Google generates a new key instead, the Play build and the website APK can never update each other: a user switching must uninstall first. Decide before the first upload; it can't be undone. Then create a separate upload key for future uploads.

## 2. VpnService declaration (Play Console > App content > VPN service)
| Question | Answer |
|---|---|
| Does the app use VpnService? | Yes |
| Is VPN the app's core functionality? | Yes. Kiwi VPN is a VPN client and nothing else: it connects the phone, or the apps the person chooses, to Price Chat's own WireGuard servers |
| Is traffic encrypted to the VPN endpoint? | Yes: WireGuard (Curve25519, ChaCha20-Poly1305), via the official `com.wireguard.android:tunnel` library |
| Does the app collect personal or sensitive data through the VPN? | No. The app doesn't inspect, log or modify traffic. It never reads which sites or apps are used. |
| Does the app manipulate ads or redirect traffic for monetization? | **Must be checked first.** The app doesn't. But a server's resolver may have an `adblock` option (`vpn_servers.adblock`). If any server blocks ads, Google's rule ("manipulation of ads that can impact monetization") is a rejection risk. **Recommendation: turn adblock off on every server the Play build can reach, or don't submit.** Owner decision |
| Per-app VPN | "Only apps I choose" uses Android's `addAllowedApplication`; "All apps except" uses `addDisallowedApplication`. The list is chosen by the user, stored only on the phone, never sent anywhere |

**Store listing must say** it's a VPN, and must describe VpnService use, e.g.: *"Kiwi VPN uses Android's VpnService to create an encrypted WireGuard tunnel to Kiwi VPN servers. You choose whether all apps or only the apps you pick (for example WhatsApp, Telegram or Signal) use the tunnel."*

### Demo video (90 seconds or less, unlisted YouTube link)
Record on a real phone, screen recording plus voice-over or captions:
1. (0-10 s) Open Kiwi VPN, press Sign in, finish in the browser (blur the email).
2. (10-25 s) "Choose what uses Kiwi VPN": show the two choices, pick **Only apps I choose**, show the Telegram quick pick with its icon, search "what", switch WhatsApp on. Show Save disabled with nothing chosen, then save.
3. (25-35 s) Press Connect, show Android's VPN consent dialog, accept.
4. (35-50 s) Home: connected to Germany, "only 2 selected". Pull down the notification: the key icon and Kiwi's notification.
5. (50-65 s) Chrome (not selected) on an IP check site: your own address. Then Kiwi Home/Diagnostics: the server's exit address.
6. (65-80 s) Settings > App-Based VPN: the same choice, switch to All Apps, save, it reconnects.
7. (80-90 s) Disconnect. The key icon disappears.

## 3. Foreground service declaration (App content > Foreground service permissions)
| Field | Answer |
|---|---|
| Permission | `FOREGROUND_SERVICE_SYSTEM_EXEMPTED` (type `systemExempted`) |
| Feature | The VPN connection's persistent notification and keeping the tunnel's process alive with the screen off |
| Justification | Android's foreground service documentation lists **VPN apps** (apps whose VPN service is the user's active VPN, configured through Settings > VPN) among those allowed to use `systemExempted`. Kiwi VPN starts this service only while its VpnService is running with the user's VPN consent, and stops it when the VPN disconnects. It's user-initiated (Connect) and user-visible (ongoing notification with a Disconnect button). |
| Video | Use the demo video, steps 3 and 4 |
| Evidence so far | Android 16 emulator: the event log shows the service started with type 1024 (systemExempted) and was allowed (`test-evidence/2026-11-20/`). **Not yet seen on a real phone.** |

Fallback if Google refuses: `specialUse` with the same explanation. That's a manifest change and a new build.

## 4. Data safety form
From what the app and backend really do. "Collected" means sent off the phone to Price Chat.

| Play category | Data type | Collected? | Shared? | Why / notes |
|---|---|---|---|---|
| Personal info | Name (display name), user ID (username) | Yes | No | Account management. From the existing Price Chat account; the app reads the name to show it |
| Personal info | Email address | Yes, **by the account** (email sign-in), not read by the app | No | Account management. Sign-in happens in the browser |
| App activity | Other user-generated content | No | | |
| Device or other IDs | Device ID | **Yes: declare it.** The device name sent on registration includes an 8-character one-way hash of the app's `ANDROID_ID` (`DeviceIdentity`) and the phone model | No | App functionality: telling two identical phones apart under My Devices |
| App info and performance | Diagnostics | Yes, minimal: the WireGuard public key, the tunnel IP, last handshake time, bytes this month (`vpn_devices`) | No | App functionality and monthly caps |
| Location | Approximate location | **No by Play's definition**, but the server necessarily sees the phone's IP address while connected. `/members/vpn/ip` returns it without storing it | | Mention IP handling in the privacy policy |
| Web browsing history | | **No.** The app never sees it. Whether the VPN server's DNS resolver keeps query logs **must be confirmed by the owner** before answering; the code says "no logs" and it hasn't been audited | | |
| Installed apps | | **No.** The app list for "Only apps I choose" is read on the phone (MAIN/LAUNCHER query, no `QUERY_ALL_PACKAGES`) and the choice stays in the encrypted Vault. Never sent | | Say so in the listing and policy |
| Messages, contacts, calls | | **No** (and the Play build has no notification access at all) | | |

- Data encrypted in transit: **Yes** (HTTPS to pricechat.live; WireGuard for the tunnel).
- Users can request deletion: **Yes**: in the app (Settings > Account > Delete account) and on the web (section 5).
- Committed to Play Families policy: **No** (not a children's app).
- Independent security review: No.

## 5. Account deletion
- **In-app path:** Settings > Account > **Delete account** (also Account tab > Delete account). Lists what's deleted and kept, asks for the username typed exactly, calls `DELETE /api/members/me`, and on success erases everything Kiwi VPN stored on the phone and returns to sign-in. Unit-tested with a fake API and the real HTTP client; a backend test proves an app session can delete the account and that its VPN account and devices cascade.
- **Web URL for the Play form: `https://pricechat.live/vpn/delete-account`** (built 2026-11-20 on `feat/kiwi-mobile`, **not deployed**; the app's `ACCOUNT_DELETION_WEB_URL` now points there). It's a public page titled "Delete your Kiwi VPN account" that names the app and Price Chat. It gives the Android and iPhone in-app paths, and lists what's deleted and kept, the same lists as the app's screen. It deletes in place once signed in (the same `DELETE /api/members/me` with the typed username), and has a request path for people who can't sign in (contact page, ownership checked, answer within one month). Tests: `frontend/src/website/vpnDelete.test.tsx`.
- **What's deleted** (from `backend/app/members/service.py` `delete_member` and the database's `ON DELETE CASCADE`): the member, sign-in identities, sessions, XP history, badges, missions, referral code; the VPN account (plan, trial, time), all VPN devices (servers drop their peers at the next sync), browser-extension logins, support reports. **Kept:** salted one-way hashes that a trial or one-time reward was used (`vpn_trial_claims`, `xp_claims`), payment records with the account link set to null, an audit line with no identity.

## 6. Privacy policy: what pricechat.live/vpn/privacy must add or confirm
**Status 2026-11-20 (`feat/kiwi-mobile`, not deployed):** items 1-3, 5 (the Private DNS part), 7 and 8 are now in the policy's new **The Android app (beta)** and **Deleting your account** sections. Item 4 was already in What we keep and How long we keep it. The 5.4 commitment is in The short version. **Still open:** item 5's resolver logging (the policy says honestly that it isn't verified yet), item 6 (rate limiting is in memory only, `security/rate_limit.py`, so nothing to add beyond hosting logs), and item 9 (the owner facts: entity, address, email, transfer safeguard).

1. Name the **Android app** and say it uses Android's VpnService.
2. **App-Based VPN:** the installed-app list is read on the phone only to let you choose; the choice stays encrypted on the phone and is never sent; apps you don't choose use your normal connection and get no protection.
3. **Device identity:** the device name includes the phone model and a short one-way hash of an Android app-scoped ID.
4. What the servers record per device (public key, tunnel IP, last handshake, monthly bytes) and how long.
5. **DNS:** whether the VPN resolver logs queries (owner must confirm), and that Private DNS in Android may use its own resolver.
6. **IP addresses:** seen while connected; `/members/vpn/ip` isn't stored; rate limiting may keep a hashed or short-lived record (check `security/rate_limit.py` before stating).
7. **Deletion:** both paths above, and what's kept.
8. **Auto-Call VPN** (website APK only): reads only WhatsApp/Telegram call notifications on the phone, nothing stored or sent. State that the Play build doesn't include it.
9. Contact for privacy requests, and the legal entity's name (the organization account requires the same entity).

## 7. Auto-Call VPN and the Play review
**Risk: high.** Auto-Call needs `BIND_NOTIFICATION_LISTENER_SERVICE` (reading every notification on the phone). Google's VPN policy forbids collecting personal or sensitive data without prominent disclosure, and a VPN that also reads notifications draws extra scrutiny; it also isn't proven on real phones (DEVICE-TEST-PLAN 27-46).

**Done:** the **play** flavor removes it entirely: no listener in the merged manifest (checked with `aapt2 dump xmltree`: 0 matches in the play APK, 2 in the direct APK), no setting, no warnings; a unit test per flavor checks `BuildConfig.AUTO_CALL`. **Recommendation: ship the Play build without Auto-Call**, keep it on the website APK as experimental, and consider adding it to Play only after real-phone results and with its own prominent-disclosure review.

## 8. Target audience and content rating
- **Target audience:** 18 and over. Not designed for children; don't tick any under-13 age group (that would bring the Families policy). The account system also has XP/leaderboards; no gambling or real-money features are in the app.
- **Content rating questionnaire (IARC):** category "Utility / Productivity / Communication / Other". No violence, sexual content, profanity, drugs, gambling. **User interaction:** the app itself has no chat or user-generated content; Passport shows a username and rank only. Shares location: **No**. Digital purchases: **No** (payments are off and the app shows no purchase). Expected result: Everyone / PEGI 3 or similar.
- **Ads:** the app contains no ads: answer No.
- **News app:** No. **Government app:** No. **Financial features:** No.

## 9. Other Play items
- **Billing:** no purchase in the app. If paid access is ever sold in the Android app, Play Billing rules apply; linking to website payments has regional rules. Decide before turning payments on.
- **Licences:** show Apache-2.0 notices for the WireGuard library and other dependencies in the app (still to do).
- **Target SDK:** 36. Lint notes 37 is available; Play's yearly target-API deadline should be checked on the day.
- **Store assets:** 512 px icon, 1024x500 feature graphic, phone screenshots (onboarding, Home connected, App-Based VPN, Locations, Delete account), short and full descriptions. **No claims about kill switch, leak protection or "military-grade"** until the phone tests (DNS, IPv6, lockdown) pass.
- **Reviewer access (built 2026-11-20, not deployed):** the store-review account now works in the app: every sign-in gets its own seat (simultaneous reviewers never disconnect each other), one WireGuard device per seat from the app session, key made on the phone, Helsinki 1 only, restricted browser session. Setup and limits: `docs/KIWI-STORE-REVIEW-ACCOUNT.md`. **App access** text for Play Console: "All functionality requires sign-in. Tap Sign in; in the browser tab, tap 'App Store or Google Play reviewer? Use the review account', enter [EMAIL] / [PASSWORD], then tap Allow. Tap Connect and accept Android's VPN prompt. The account uses the Helsinki server only. You may delete it (Settings > Account > Delete account); signing in again with the same details creates a fresh one." Never use the owner's own account.

## Owner checklist
1. Create the legal entity, get a D-U-N-S, open an **organization** Play Console account.
2. Decide adblock on servers (section 2).
3. Decide Play App Signing: upload the existing key (recommended) before the first upload.
4. Approve deploying the public deletion page and the privacy-policy items (sections 5 and 6), built on `feat/kiwi-mobile`, and fill in the owner facts.
5. Confirm DNS resolver logging, then fill the Data safety form from section 4.
6. Run `docs/PHONE-TEST-2026-11.md` on at least one real phone (Android 14+), and record the demo video.
7. Set up the reviewer account: deploy, then `docs/KIWI-STORE-REVIEW-ACCOUNT.md` owner setup.
8. Only then upload the play AAB to an internal test track.
