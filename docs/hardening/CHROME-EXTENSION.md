# Kiwi VPN Chrome extension 1.0.5: release readiness

Branch `feat/kiwi-ext-105` (from main a7d2081). Store item `jeadaadhacdikcbahliklcaaeaddeloa`, public, still serving
**1.0.0** (updated 2026-11-06). 1.0.3 and 1.0.4 were prepared and never submitted, so 1.0.5 goes straight over 1.0.0.

Nothing was uploaded, submitted or published. `upload`, `submit` and `go-live` are the owner's steps (below).

## 1. Security audit items (SECURITY-AUDIT.md L15, L16, L17)

### L16: another extension holding the proxy or WebRTC setting (fixed)

Chrome gives a setting to one extension at a time: the most recently installed one that set it, or the organization's
policy. 1.0.4 set WebRTC with `.catch(() => undefined)` and never looked at who really held either setting.

1.0.5 reads `levelOfControl` of `chrome.proxy.settings` and `chrome.privacy.network.webRTCIPHandlingPolicy`
(`controlConflict()` in `lib.js`). It checks before and after connecting, whenever the popup asks for state, on each
setting's `onChange`, and on every connection check.

| Situation | What the user sees |
|---|---|
| Proxy held by another extension or policy, while off | Warning in the popup; **Connect is refused** with the reason (nothing would be protected). |
| Proxy taken while on | **"Not protected"** with the reason, badge `!`, exit IP hidden. Kiwi's setting stays registered, so when the other extension lets go Kiwi's proxy is in force again and it says Connected. Traffic never silently goes around Kiwi because of Kiwi: the other extension decides where it goes, and the popup says so. |
| WebRTC held by another extension or policy | Connect works (traffic is proxied) but the popup says **"Not fully protected"**: websites may see the real IP through WebRTC. Badge `!`. |
| Neither | Unchanged: "Connected", badge ON. |

Tests: 3 new lib tests (every level combination, badge, popup wording), and an e2e section that loads a second test
extension (`vpn-extension/e2e/conflict-ext`, never packaged) which takes WebRTC, then the proxy, then lets go, then takes
the proxy while Kiwi is off. 9 checks.

### L15: proxy password at rest (moved to memory)

1.0.5 keeps the proxy password only in `chrome.storage.session` (in memory, cleared when the browser closes, never written
to the profile on disk). `chrome.storage.local` keeps the on/off state and the login's name, host, port and server.
1.0.4 installs are migrated on first read: the password is moved to session storage and removed from local storage.

After a browser restart:
1. The proxy setting is applied at once with no password, so traffic is **held from the first request** (the proxy's
   password request is cancelled, never sent around the VPN).
2. A fresh login is requested with the Price Chat session, replacing the old one (`replace=<old username>`, so logins
   don't pile up against the device limit).
3. Traffic flows again and the popup says Connected.

The e2e restarts the real browser on the same profile and checks: the same extension id; sites load through the proxy
after the restart; a new login was minted; the popup says Connected; no password on disk before or after. The strict
revoked-login scenario runs after the restart, so it is covered on a re-minted login too. Both pass every run (section 4).

Trade-offs, accepted:
- **A restart needs a valid Price Chat session.** The member session lasts 30 days and is renewed only by visiting the
  website (`/api/members/session`), not by the extension's own API calls. A member who hasn't visited pricechat.live for
  30 days and restarts the browser gets "You're signed out. Traffic is stopped until you sign in, or switch off." That
  fails closed and says why. 1.0.4 already showed "You're signed out" in that state but kept the old login working.
  Optional backend follow-up (not done, not this branch): renew the session on `GET /api/members/vpn` too.
- **Rate limit.** Each restart mints one login. `vpn_browser` allows a burst of 5, then one per 10 minutes per member.
  Restarting browsers more than 5 times in 10 minutes holds traffic ("Connection lost") until the bucket refills. It
  recovers by itself on the next request or "Try again".
- **Offline at startup.** No login can be minted until the network is back. The next request (or "Try again") retries.
  Traffic stays held meanwhile, as before.

### L17: what isn't covered (documented)

- The popup has a "What isn't covered" section: pricechat.live always uses the normal connection (so the extension can
  reach the account when the VPN is down, which means Price Chat sees the real IP); incognito isn't covered unless the
  user allows Kiwi VPN in incognito; local network addresses and other apps aren't covered; another extension holding the
  proxy or WebRTC setting is reported.
- `store/LISTING.md` description: the same two points spelled out ("Important" list).
- `vpn-extension/README.md`: updated.
- The website is owned by another agent, so `frontend/` wasn't touched. Suggested wording for `/vpn/privacy`
  (`frontend/src/website/community.tsx`, "Kiwi VPN" legal section):
  - Replace "…and the password itself stays in your browser's extension storage." with
    **"…and the password itself is kept only in your browser's memory while it runs. After the browser restarts, the
    extension asks for a new login with your Price Chat sign-in and the old one is removed."**
  - Replace "…except pricechat.live and addresses on your own network." with
    **"…except pricechat.live and addresses on your own network. pricechat.live always uses your normal connection, so
    the extension can still reach your account and explain a problem when the VPN is down; Price Chat therefore sees
    your real IP address. Incognito windows aren't covered unless you allow Kiwi VPN in incognito in your browser's
    extension settings."**
  - Replace "Storage: to remember your on/off state, server and login on your device." with
    **"Storage: to remember your on/off state and server on your device, and the login's password in memory only."**
  - Optional, on `/vpn` near the extension card: "Covers that browser only, not incognito unless you allow it there."

Not done: incognito support (`"incognito": "spanning"` + proxying incognito when allowed). It changes the review
surface; leave it for a later version if users ask.

## 2. Listing text and permission justifications

Paste from `vpn-extension/store/LISTING.md` (Store listing tab and Privacy tab). Name and summary come from the package.
Changes in this branch: two "Important" bullets (pricechat.live bypass, incognito), one feature bullet (another extension
taking over is reported), and the `storage` justification (password in memory only).

| Permission | Justification (as in LISTING.md) |
|---|---|
| `<all_urls>` (host access) | Chrome only passes the proxy's password request, and a refused login, to an extension for sites it has access to. The proxy carries every site, so the extension needs access to every site to sign in to it and to notice when the login is refused. It also reaches the Kiwi VPN account API on pricechat.live and `http://check.kiwi-vpn.invalid/` (exists only inside the proxy) to confirm traffic goes through it. It doesn't read or change page content, and runs no content scripts. |
| `proxy` | Core function: points the browser at the HTTPS proxy on the server the user chose while switched on, and clears it when switched off. |
| `webRequest` | Listens for the proxy's password request and request results, to sign in and detect a refused login. Doesn't read or change page content. |
| `webRequestAuthProvider` | Answers that password request with this browser's login. Only the proxy's own challenge, never a website's. |
| `privacy` | Sets WebRTC to use only the proxy while connected, so it can't reveal the real address, and restores it on disconnect. Also reads who controls that setting, to warn when another extension does. |
| `storage` | Keeps the on/off state and chosen server on the device, so the connection comes back after a restart. The proxy password is kept only in session storage (memory). |

Permissions are identical to 1.0.0 (same five permissions, same `<all_urls>`), so Chrome won't show existing users a new
permission prompt and the update won't be disabled pending re-approval.

## 3. Privacy practices tab (answers)

- **Single purpose:** "Sends the browser's web traffic through an encrypted proxy on a Kiwi VPN server, switched on and off by the user."
- **Remote code:** No.
- **Data usage** (owner certifies; read against https://pricechat.live/vpn/privacy first):

| Category | Tick | Why |
|---|---|---|
| Authentication information | Yes | Sends the Price Chat session cookie to pricechat.live; uses a proxy login (password in memory only). |
| Web history | Yes | Each site's address passes through the proxy in transit; the proxy code doesn't record it. |
| Website content | Yes | Page traffic passes through the proxy in transit (encrypted for https). Not stored. |
| Location | Yes, if the definition includes IP address | The server sees the user's IP to carry traffic. |
| PII, health, financial, personal communications, user activity | No | Not collected by the extension. |

- **Certifications:** not sold or transferred to third parties outside approved uses; not used for unrelated purposes;
  not used for creditworthiness or lending (if true for the owner's business).
- **Privacy policy URL:** https://pricechat.live/vpn/privacy

## 4. Tests on this branch

| Suite | Result |
|---|---|
| `node --test vpn-extension/lib.test.js vpn-extension/store/cws.test.mjs` | 27/27 (lib 16, 4 of them new; cws 11) |
| e2e (`vpn-extension/e2e/run.mjs`, run directly, no retry) | 48 checks. 12 runs with the 1.0.5 changes: 10 all-pass; 2 harness failures fixed on the way (the second test extension was blocked by `--disable-extensions-except`; one screenshot of a background tab hung until the outer timeout, now bounded). The last 4 runs on the final code (95491d6): 48/48 each |
| `node vpn-extension/store/release.mjs prepare` | passed first try, no retry: unit, proxy/agent pytest, browser suite on the source 48/48, browser suite on the store ZIP under the store ID 48/48 |

The e2e was 32 checks; it is now 48: + password-not-on-disk, 6 restart checks, 9 conflict checks. The strict
revoked-login check (one page load after the revoke, then only the popup) passed in all 15 runs (baseline, 12 direct runs, and both `prepare` suites), with no retry; since this branch it also runs after a browser restart, on a re-minted login.

## 5. The package

- ZIP: `vpn-extension/store/dist/kiwi-vpn-chrome-1.0.5.zip` (not in git; `python vpn-extension/build.py` rebuilds it
  byte for byte: fixed timestamps)
- SHA-256: `da6e0e83ad28b6d4b1927dd4d5df807ac049177c0d2244654c4a9c1bc9e4e1a9`
- Release record: `vpn-extension/store/releases/1.0.5.json`
- Tested as the store's own extension ID (the public key from the published 1.0.0 CRX) by `prepare`.
- `build.py` also rewrites `frontend/public/downloads/kiwi-vpn-extension.zip` (the testers' download). That file was
  restored on this branch because `frontend/` belongs to the website agent. Rebuild it when merging if testers should get 1.0.5.

## 6. Screenshots

`vpn-extension/store/assets/` (1280x800 x4, promo tiles, logo) were regenerated from this branch's e2e screenshots with
`node vpn-extension/store/assets.mjs <chrome.exe> <e2e screenshot folder>`, so they show the 1.0.5 popup (Smart Connect
picker, Kiwi look). They frame real popup captures from a test run against the stand-in API; the IP shown, 203.0.113.10,
is a documentation address. Upload them in the dashboard (the API doesn't carry listing images): the store still shows
the 1.0.0-era images.

## 7. Owner submission checklist (1.0.5)

Only you run these, from the repository root of the merged checkout, in your own terminal. Agents never run `upload`,
`submit` or `go-live`, and `submit`/`go-live` refuse to run without a real terminal.

**A. One-time setup (about 10 minutes; RELEASING.md has the details)**

- [ ] Google Cloud project, e.g. `kiwi-vpn-release`: https://console.cloud.google.com/projectcreate
- [ ] Enable the **Chrome Web Store API** in it: https://console.cloud.google.com/apis/library/chromewebstore.googleapis.com
- [ ] IAM > Service accounts > create `kiwi-cws-release` with **no roles**; Keys > Add key > JSON (downloads one file).
- [ ] Store it encrypted for your Windows user (the plaintext file is deleted):
      `node vpn-extension/store/release.mjs setup-key "%USERPROFILE%\Downloads\<the file>.json"`
      It prints the service account's email. (Today `%APPDATA%\KiwiVPN\cws-service-account.dpapi` doesn't exist.)
- [ ] Chrome Web Store Developer Dashboard > Account > **Service accounts**: add that email.
- [ ] Dashboard > Publisher > Settings: copy the **publisher ID** into `vpn-extension/store/cws.json` as `"publisherId": "<id>"`
      (empty today), commit it. It's an identifier, not a secret.
- [ ] `node vpn-extension/store/release.mjs status` shows "live 1.0.0" and no submitted version.

**B. Just before submitting**

- [ ] The package is the tested one: `python vpn-extension/build.py`, then
      `Get-FileHash vpn-extension\store\dist\kiwi-vpn-chrome-1.0.5.zip` = `DA6E0E83AD28B6D4…C9E4E1A9` (= `store/releases/1.0.5.json`).
      `upload`/`submit` refuse any other bytes anyway.
- [ ] Helsinki 1 is online with the browser badge in Admin (the reviewer needs one browser server).
- [ ] Decide on the reviewer account (C below). If you use it, `https://pricechat.live/vpn/reviewer` loads and the sign-in works.
- [ ] Decide on the "Draft" banner on /vpn/privacy (section 9: medium-low rejection risk; 1.0.0 passed with it).

**C. Upload and submit**

```
node vpn-extension/store/release.mjs status
node vpn-extension/store/release.mjs upload 1.0.5       # the exact tested ZIP becomes the store DRAFT; nothing is submitted
```

Then fill the dashboard by hand (D), and only then, at your own terminal:

```
node vpn-extension/store/release.mjs submit 1.0.5       # type exactly:  submit 1.0.5 da6e0e83
node vpn-extension/store/release.mjs status             # later: "approved" / staged; nothing reaches users yet
node vpn-extension/store/release.mjs go-live 1.0.5      # after approval, type:  go-live 1.0.5 da6e0e83
```

`submit` needs `upload` first (it refuses otherwise) and submits **staged**: approval doesn't publish; `go-live` does. While
in review, the dashboard can cancel the submission. Version numbers can't be reused.

**D. Dashboard fields the API doesn't carry (fill before `submit`)**

- [ ] **Store listing**: paste the description from `store/LISTING.md`; category Privacy & Security (or closest); language
      English; screenshots `store/assets/screenshot-1..4-*-1280x800.png`; small promo tile `promo-small-440x280.png`; marquee
      `promo-marquee-1400x560.png` (optional); homepage https://pricechat.live/vpn; support URL; mature content off. Name,
      summary and the 128 px icon come from the package.
- [ ] **Privacy practices**: single purpose; one justification per permission (`proxy`, `webRequest`, `webRequestAuthProvider`,
      `privacy`, `storage`, host access `<all_urls>`), all from LISTING.md; remote code **No**; data usage ticks
      (authentication information, web history, website content, location if it includes IP address: yes; the rest: no);
      the three certifications; privacy policy URL **https://pricechat.live/vpn/privacy**.
- [ ] **Test instructions** for the reviewer (below).

**Reviewer test instructions.** Kiwi Passport sign-in has no password (Google, Telegram or an emailed code), so a reviewer
can't sign in to an ordinary account. Two choices:

1. *Reviewer account (recommended: lets the reviewer connect).* Run `python vpn-extension/store/reviewer_account.py
   <a reviewer email>` yourself: it sets `VPN_REVIEWER_*` on Railway `api` (valid 30 days, one browser login, Helsinki 1
   only, no admin rights), puts the password on your clipboard only, then redeploy `api`. Paste LISTING.md "Reviewer
   access" steps 1-4 with that email and password into the test instructions. Remove the variables after approval.
2. *No account.* Paste this:

   > Kiwi VPN needs a Kiwi Passport (Price Chat account), and Passport sign-in uses Google, Telegram or a one-time email
   > code, with no password, so we can't give you shared credentials. Without an account you can check: the extension
   > installs with no page scripts and no remote code; clicking the icon shows "Sign in to connect" and its Sign in button
   > opens https://pricechat.live/join; "What isn't covered" explains the limits; with the switch off the browser's own
   > connection is unchanged. Connecting requires an account with Kiwi VPN access (a free 30-day trial is started on
   > https://pricechat.live/vpn after signing in with your own Google account). After connecting, https://api.ipify.org
   > shows the server's address, and pressing the switch again restores the normal connection.

   Reviewers often reject an extension they can't fully exercise ("not functional"), so option 1 is safer.

## 8. What changed since 1.0.0 (the live version)

- **1.0.1-1.0.3 (never published):** narrower host access was tried and reverted to `<all_urls>` (with it, Chrome
  answered the proxy itself and a revoked login could leave the popup on "Connected"). Refused logins are now also
  caught from failed requests and proxy/tunnel errors, so the popup never says Connected while traffic is held. Kiwi
  icon and the /vpn look; the plan reads as on the website; servers only in the apps are named, not hidden.
- **1.0.4 (never published):** Smart Connect, the default: times each online server's proxy (3 small HTTPS requests to
  `https://<proxy host>/kiwi-ping`, only while off) and picks the fastest, with a penalty for load.
- **1.0.5:** L16 (another extension or policy holding the proxy or WebRTC setting is detected, never shown as
  protected; connecting is refused when the proxy is taken); L15 (proxy password in memory only, fresh login after a
  restart); L17 ("What isn't covered" in the popup).
- Permissions unchanged since 1.0.0.

## 9. Review-rejection risks

- **Broad host access (`<all_urls>`) + `webRequestAuthProvider`:** triggers in-depth review and longer review times.
  1.0.0 was approved with the same permissions and the justification is specific. Low risk.
- **The privacy policy page carries a "Draft: this page has not had final legal review yet" banner** (all legal pages,
  `community.tsx` `LegalPage`). A reviewer could treat it as no final policy. Medium-low risk; 1.0.0 passed with it.
  LISTING.md's checklist already says to remove it after legal review. The KIWI-LAUNCH-REVIEW items (trader identity,
  contact email) are also missing from the policy.
- **Policy wording vs. behaviour:** the live policy says the password "stays in your browser's extension storage"; 1.0.5
  keeps it in memory. It over-discloses, which reviewers don't reject for, but update it (section 1, L17).
- **Reviewer access:** reviewers must be able to sign in at https://pricechat.live/vpn/reviewer without an email code
  and connect to Helsinki 1. If that account has expired, or Helsinki 1 is offline, it's rejected as "not functional".
  Check with `store/reviewer_account.py` and Admin right before submitting. Note the reviewer account's limit is one
  browser login (`limit = 1 if review`): a restart replaces it, so that still works.
- **Store ID allowed by the backend:** `VPN_EXTENSION_ORIGINS` on Railway must include
  `chrome-extension://jeadaadhacdikcbahliklcaaeaddeloa` (it must already, as 1.0.0 is live; check before submitting).

## 10. Rollback

The Chrome Web Store has no rollback. If 1.0.5 misbehaves after going live: revert the change on a branch, bump to
**1.0.6** (`prepare --bump patch`), and ship it through the same steps. Until 1.0.6 is approved, users stay on 1.0.5.
Because 1.0.5 is submitted **staged**, nothing reaches users at approval: `go-live` is a separate, deliberate step, and
while it's still in review the dashboard can cancel the submission. Version numbers can't be reused, so never re-upload
1.0.5 with different contents. After go-live, install from the store on a clean profile and check sign-in, connect,
disconnect and a browser restart (LISTING.md "After approval").

## 11. Microsoft Edge Add-ons (optional)

`build.py` also writes `vpn-extension/store/dist/kiwi-vpn-edge-1.0.5.zip`: the same code without the `key`. Submit it at
https://partner.microsoft.com/dashboard/microsoftedge (no API tool here; manual upload). Listing kit: the Edge section of
LISTING.md. Edge assigns its own id at first upload: add `chrome-extension://<edge id>` to `VPN_EXTENSION_ORIGINS`, or
sign-in from that install is refused. Edge is Chromium, so the `levelOfControl` checks and session storage work the same;
the e2e harness takes `msedge.exe` as its browser argument if you want to run it there.

## 12. Re-verified 2026-11-17 (feat/kiwi-sec)

- Store (CWS update check for `jeadaadhacdikcbahliklcaaeaddeloa`): still serves **1.0.0** (CRX sha256 `d5d433ba…`). Nothing uploaded since.
- `python vpn-extension/build.py` from this branch's source rebuilds `kiwi-vpn-chrome-1.0.5.zip` byte for byte:
  sha256 `da6e0e83ad28b6d4b1927dd4d5df807ac049177c0d2244654c4a9c1bc9e4e1a9` = `store/releases/1.0.5.json` = the ZIP in
  D:/pc-wt/kiwi-ext-105. `vpn-extension/` here is identical to feat/kiwi-ext-105.
- Tests: `node --test vpn-extension/lib.test.js vpn-extension/store/cws.test.mjs` 27/27; browser e2e
  (`node vpn-extension/e2e/run.mjs <venv python> %LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe <shots>`) 48/48.
- Production: `chrome-extension://jeadaadhacdikcbahliklcaaeaddeloa` gets 401 "Please sign in" on `/api/members/vpn/browser` (allowed),
  an unknown ID 403; `/vpn/privacy` already carries the 1.0.5 wording (password in memory only); still has the "Draft" banner.
- Website `STORE_LINKS.chrome` = `https://chromewebstore.google.com/detail/jeadaadhacdikcbahliklcaaeaddeloa` (unchanged; no edit needed
  for an update of the same item). Testers' ZIP `frontend/public/downloads/kiwi-vpn-extension.zip` rebuilt to 1.0.5 on this branch
  (was 1.0.3), so running build.py in the release checkout leaves no diff.
- Release tool (feat/kiwi-cws-release ca59dcc) is merged. Blockers are all owner-side: `vpn-extension/store/cws.json` `publisherId` is
  empty and `%APPDATA%\KiwiVPN\cws-service-account.dpapi` does not exist (setup steps 1-8 in section 7 not done).

## 13. Submission prep re-verified 2026-11-17 (feat/kiwi-release-rc)

- `python vpn-extension/build.py` on this branch: `kiwi-vpn-chrome-1.0.5.zip` and `kiwi-vpn-edge-1.0.5.zip` are both
  sha256 `da6e0e83ad28b6d4b1927dd4d5df807ac049177c0d2244654c4a9c1bc9e4e1a9`, 30,579 bytes = `store/releases/1.0.5.json` = the
  ZIP in D:/pc-wt/kiwi-ext-105 (built 2026-11-14 11:35). The testers' ZIP in `frontend/public/downloads` came out unchanged.
- In the ZIP: the 10 expected files at the top level, each byte-identical to the source; manifest version 1.0.5, **no `key`**,
  permissions `proxy, storage, webRequest, webRequestAuthProvider, privacy` + host `<all_urls>` (same as live 1.0.0), no content
  scripts, no CSP override; the only network targets are pricechat.live's account API, the proxy check address and each
  server's `/kiwi-ping`, and nothing is evaluated or imported from outside the package (remote code = none).
- Every permission has its LISTING.md justification; the data-use answers match /vpn/privacy (community.tsx `vpnprivacy`:
  session cookie + proxy login, sites and content in transit only, IP seen by servers and hosting logs, no ads/analytics,
  Limited Use statement). Listing text has no "no logs", "military grade" or "100% anonymous" claim (it says it doesn't
  guarantee complete anonymity). Icons 16/32/48/128 and assets 1280x800 x4, 440x280, 1400x560, 300x300 are the right sizes.
- Tests here: `node --test vpn-extension/lib.test.js vpn-extension/store/cws.test.mjs` 27/27; browser e2e on the source
  (`node vpn-extension/e2e/run.mjs <backend venv python> <playwright chromium-1243> <shots>`) 48/48, one run, no retry. The
  store-ID run on the ZIP wasn't repeated (it downloads the live CRX from Google); the ZIP's files equal the source byte for byte.
- **For the owner to decide (not changed, since it would change the tested package):** the popup's footnote says "We don't
  log the sites you visit." (popup.js). The privacy page deliberately avoids "We don't log" (its test forbids the phrase; owner
  2026-11-15) and says "We don't keep a history of the websites or addresses you visit". The code backs the narrower meaning
  (the proxy logs no addresses), but DNS lookups go to Hetzner's resolvers, which the policy names. To align, change it to
  "We don't keep a history of the sites you visit." and run `prepare` again (new SHA-256, new confirmation code).
- Small wording difference, policy over-discloses: /vpn/privacy says incognito isn't covered; the popup and listing say it isn't
  unless allowed in incognito (true: settings in the regular scope apply to incognito only when the extension is allowed there).
