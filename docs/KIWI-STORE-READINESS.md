# Kiwi VPN mobile: store-testing readiness (Android and iPhone)

Prepared 2026-11-20 on `feat/kiwi-mobile`. **Nothing has been uploaded, submitted or published, and no Google or Apple account
has been touched.** The production backend and website that the apps need went live on 2026-11-20 (Railway `e789550e`, Vercel
`pricechat-983dh87bp`).

> **Physical-device testing: NOT DONE. UNVERIFIED. REQUIRED before any public release** (production track or App Store).
> Every app result so far is from the Android emulator or Codemagic's compiler. The kits are ready:
> `android-client/docs/PHONE-TEST-2026-11.md` and `ios-client/docs/IPHONE-TEST-PLAN.md`. Internal testing (Play) and
> TestFlight internal testing are where those runs belong.

## Android: status
| Item | Status | Where |
|---|---|---|
| Signed Play bundle | **Ready:** `C:\AI\pc-wt\kiwi-release\android\0.2.0\KiwiVPN-0.2.0-4c3e829-play.aab`, SHA-256 `FFCCB106...72FCF5` | `BUILD-INFO.txt` beside it |
| Website APK (same build) | `KiwiVPN-0.2.0-4c3e829.apk`, SHA-256 `008AD560...AD6397` | |
| Signing | Release key `CN=Kiwi VPN, O=Price Chat`, RSA 4096, cert SHA-256 `6564545155fb...3a34ebb2`, valid to 2054; `jar verified`; the build script checks both files against this key | `android-client/docs/SIGNING.md` |
| Manifest (the build's own packaged manifest; its AAB hash matched the release file for e4aaef1) | `live.pricechat.kiwivpn`, 0.2.0 (versionCode 4), targetSdk 36, minSdk 26; permissions INTERNET, ACCESS_NETWORK_STATE, POST_NOTIFICATIONS, FOREGROUND_SERVICE(+_SYSTEM_EXEMPTED); **no notification listener, no QUERY_ALL_PACKAGES**; not debuggable; allowBackup false | |
| 16 KB page size (Play requirement since Nov 2025 for apps with native code that target Android 15+) | **Pass:** every 64-bit `.so` (arm64-v8a, x86_64, including `libwg-go.so`) has a 16384-byte LOAD alignment; 32-bit ABIs are exempt | |
| Unit tests | 172/172 on each flavour (direct, play) | |
| New in this build | Settings > **Open-source licences** (Apache-2.0, MIT, BSD notices in the APK; was missing); light status bar icons (they were dark on the dark app when the phone used a light theme) | `fafec4b`, `4c3e829` |
| VpnService declaration, foreground-service declaration, demo-video script | Written | `android-client/docs/PLAY-SUBMISSION.md` sections 2-3 |
| Data safety | Written; **one answer waits on you:** whether the VPN servers' DNS resolver logs queries (Web browsing: No only if it doesn't) | PLAY-SUBMISSION section 4 |
| Account deletion URL | **Live:** `https://pricechat.live/vpn/delete-account` | section 5 |
| Privacy policy | **Live:** `https://pricechat.live/vpn/privacy` (Android section, deletion). **Owner facts still missing** (legal entity, address, business email, transfer safeguard) | section 6 |
| Reviewer access | **Live:** review seats (any number of reviewers at once), Helsinki 1 only, valid to 2026-12-04. App access text in PLAY-SUBMISSION section 9 | `docs/KIWI-STORE-REVIEW-ACCOUNT.md` |
| Store art | `android-client/store-assets/play-icon-512.png`, `play-feature-graphic-1024x500.png` (no other companies' names or logos); `screenshots/1-sign-in.png` | `tools/store-assets.py` |
| Phone screenshots | **1 of the 2+ needed.** The rest need a signed-in session with real WhatsApp/Telegram/Signal: run `android-client\tools\store-screenshots.ps1` (clean demo-mode status bar) with the review account | |
| Physical phone test | **UNVERIFIED, required before production** | PHONE-TEST-2026-11.md |

## iPhone: status
| Item | Status | Where |
|---|---|---|
| Version | 0.2.0; build number set automatically at App Store build time (latest in App Store Connect + 1) | `ios-client/codemagic.yaml` |
| Network Extension | App `live.pricechat.kiwivpn` and tunnel `live.pricechat.kiwivpn.tunnel` both carry `com.apple.developer.networking.networkextension = [packet-tunnel-provider]`; tunnel `NSExtensionPointIdentifier = com.apple.networkextension.packet-tunnel`, principal class `PacketTunnelProvider`; app uses `NETunnelProviderManager`; keychain groups `.app` (app only) and `.shared` (both). `check_sources.py`: OK. After signing, `verify-ipa.sh` checks the entitlement, filled-in keychain groups, the profile type and the privacy manifests in the signed IPA | `docs/APPLE-CHECKLIST.md` |
| **Privacy manifests** (App Store Connect refuses uploads without required-reason declarations) | **Added:** app `PrivacyInfo.xcprivacy` (UserDefaults CA92.1; collected data matching the App Privacy answers; no tracking); tunnel `PrivacyInfo.xcprivacy` (System boot time 35F9.1 and File timestamp C617.1, for wireguard-go's Go runtime). In the Xcode project as resources. **Not yet compiled:** the next Codemagic run proves it. Apple's upload check may still name a reason I missed (email ITMS-91053): fix and re-upload if so | `ios-client/KiwiVPN/`, `ios-client/KiwiTunnel/` |
| Codemagic | `kiwi-compile` (no account), `kiwi-adhoc-ipa` (one iPhone), **new** `kiwi-appstore-ipa` (App Store-signed IPA, uploads nothing), **new** `kiwi-testflight` (the only workflow that uploads: to App Store Connect, internal testers only, never submits for review). No workflow has a trigger | `ios-client/codemagic.yaml`, `docs/CLOUD-BUILD.md` |
| App Privacy answers, 5.4 data declaration, review notes, storefronts, export compliance | Written | `ios-client/docs/APP-STORE-SUBMISSION.md` |
| Export compliance | **Your decision:** France (exclude it, or file the French encryption declaration); then set `ITSAppUsesNonExemptEncryption`. Until then App Store Connect asks at each upload | APP-STORE-SUBMISSION section 7 |
| Reviewer access | Live (same review seats as Android). Review-notes text in section 4 | |
| Screenshots | **None yet.** App Store needs 6.9" iPhone screenshots (1320 x 2868 or 1290 x 2796, at least 1, up to 10). Take them on a TestFlight install on a large iPhone, or from the simulator later | |
| Physical iPhone test | **UNVERIFIED, required before App Store release** | IPHONE-TEST-PLAN.md |

## What you must do: Google Play Console
1. **Organization identity.** Google Play takes VPN apps only from organization accounts.
   1. A registered legal entity.
   2. Its **D-U-N-S number**: free from Dun & Bradstreet, and it can take up to about 30 days.
   3. A website on the entity's domain (pricechat.live) that you can verify in Google Search Console.
2. **Create the developer account** at play.google.com/console, choosing **Organization**:
   1. Pay the $25 fee.
   2. Enter the D-U-N-S number, then verify the organization's identity, contact email and phone, and the website.
   3. Note that the developer name and address are shown publicly on the store page.
3. **Create the app.** Name "Kiwi VPN", type App, Free. Accept the declarations.
4. **Set up Play App Signing before the first upload.** This can't be changed later.
   1. Choose **Use existing app signing key** ("export and upload a key from Java keystore").
   2. Run Google's PEPK tool yourself with `%USERPROFILE%\.kiwi-android-signing\kiwi-release.keystore`. That keeps Play builds and the website APK updatable over each other.
   3. Make a separate **upload key** and register its certificate. `android-client/docs/SIGNING.md` covers the key handling.
   4. Never send me the keystore passwords.
5. **App content** (Policy > App content). The answers are in `PLAY-SUBMISSION.md`:
   - Privacy policy: `https://pricechat.live/vpn/privacy`
   - App access: all functionality needs sign-in. Use the review account, with its text from section 9.
   - Ads: No
   - Content rating questionnaire: section 8
   - Target audience: 18+
   - News: No. Government: No. Financial features: None. Health: No.
   - Data safety: section 4, once you've confirmed DNS logging.
   - **VPN service:** section 2, plus an unlisted demo video recorded on a real phone.
   - **Foreground service permission (systemExempted):** section 3, using the same video.
   - Account deletion URL: `https://pricechat.live/vpn/delete-account`
6. **Decide ad blocking on the servers** (section 2). Ad blocking on a server the Play build can reach is a rejection risk.
7. **Store listing:**
   1. Short and full description. No kill-switch, leak-proof or "military-grade" claims until the phone tests pass.
   2. Icon and feature graphic from `android-client/store-assets/`.
   3. At least 2 phone screenshots.
8. **Internal testing** (Test and release > Testing > Internal testing):
   1. Create an email list of testers (you, plus up to 100).
   2. Tell me to go ahead. **With your approval**, the upload is `KiwiVPN-0.2.0-4c3e829-play.aab`, done by you in the browser or by me with a service account you create.
   3. Roll out to internal testers.
   4. Then run the phone test kit on the Play-installed build.
9. **Production stays off** until the physical tests pass and you approve. Organization accounts are exempt from the 12-tester, 14-day closed test that new personal accounts need. Check this on the day.

## What you must do: Apple Developer and App Store Connect
1. **Enrol in the Apple Developer Program as an organization.** Guideline 5.4 says VPN apps come only from organizations. You need:
   - the legal entity and its D-U-N-S number (the same one as for Google)
   - a website on its domain
   - authority to bind the entity
   - $99 per year
2. **Complete the agreements and EU trader status.** In App Store Connect > Business, accept the agreements. Then declare **trader status** under the EU Digital Services Act. An app sold by a company in the EU shows its address, phone and email publicly.
3. **Create the App Store Connect API key for Codemagic.** Users and Access > Integrations > App Store Connect API, role **App Manager**. In Codemagic, add it as the integration named `kiwi`.
4. **Create the certificate private key and the `kiwi_signing` group in Codemagic.**
   1. On your PC, run `ssh-keygen -t rsa -b 2048 -m PEM -f kiwi_cert_key -q -N ""`.
   2. Add the private key's contents to the `kiwi_signing` group as the secret `CERTIFICATE_PRIVATE_KEY`.
   3. Codemagic then creates the **Apple Distribution certificate** and the **App Store provisioning profiles** for both bundle IDs, with Network Extensions on, through the API (`apple-setup.sh`, `fetch-signing-files --create`).
   4. Keep `kiwi_cert_key` safe. It's the certificate's private key.
5. **Create the App IDs** (`live.pricechat.kiwivpn` and `live.pricechat.kiwivpn.tunnel`, with Network Extensions on). Either:
   - run `kiwi-appstore-ipa` once; it creates them and uploads nothing
   - or create them yourself in the Developer portal: Identifiers > +, tick Network Extensions.
6. **Create the app record in App Store Connect:**
   1. Apps > + > New App: iOS, name "Kiwi VPN" (it must be free on the store), bundle ID `live.pricechat.kiwivpn`, SKU e.g. `kiwivpn-ios`, Full Access.
   2. Copy the app's **Apple ID** number (App Information) into `kiwi_signing` as `KIWI_APP_STORE_APPLE_ID`.
7. **Make the source available to Codemagic** with `ios-client/tools/sync-cloud-repo.ps1`, as for the earlier compile runs. Then run `kiwi-compile` (free, no account) to prove the privacy manifests compile, followed by `kiwi-appstore-ipa`.
8. **Fill in App Privacy** (APP-STORE-SUBMISSION section 2). It must agree with the new `PrivacyInfo.xcprivacy`.
9. **Decide export compliance** (France). Then I set `ITSAppUsesNonExemptEncryption`.
10. **Exclude storefronts** (section 6). Take legal advice on China mainland and the UAE first.
11. **TestFlight, with your approval:**
    1. Start `kiwi-testflight`. It uploads, and after processing the build reaches **internal** testers only.
    2. Add yourself under TestFlight > Internal Testing.
    3. Install TestFlight on your iPhone and run `IPHONE-TEST-PLAN.md`.
    4. Take the 6.9-inch screenshots.
12. **App Review:** only after the physical tests pass and you approve.

## Blockers, in order
1. **Legal entity + D-U-N-S + organization accounts on both stores.** Nothing can be uploaded before this. It's the long pole: days to weeks.
2. Privacy policy owner facts (entity, address, email, transfer safeguard), and the DNS-logging answer.
3. Physical-device tests on Android and iPhone (UNVERIFIED), the Play demo video, and the store screenshots.
4. iOS: the first Codemagic compile with the privacy manifests, and the France / export-compliance decision.
5. Android: the ad-blocking decision for the servers, and the Play App Signing choice (upload the existing key).
6. Review account validity: it ends 2026-12-04. Rerun `reviewer_account.py` before each review, so it covers the whole review.

## Next actions
**Android (once the organization account exists):**
1. App signing setup.
2. App content forms.
3. Screenshots.
4. Your approval, then the internal-testing upload.
5. The phone kit on the Play-installed build.
6. Fixes if needed.
7. Production review, with your approval.

**iPhone:**
1. Organization enrolment.
2. API key, certificate key and App Store Connect record.
3. `kiwi-compile`, then `kiwi-appstore-ipa`.
4. Your approval, then `kiwi-testflight`.
5. The iPhone test plan.
6. Screenshots.
7. App Review, with your approval.
