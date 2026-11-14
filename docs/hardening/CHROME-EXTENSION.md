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
| `node --test vpn-extension/lib.test.js vpn-extension/store/cws.test.mjs` | 27/27 (lib 23 incl. 4 new; cws 4) |
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

## 7. What the owner does

### One-time setup (about 10 minutes, from RELEASING.md)

1. Google Cloud project `kiwi-vpn-release`: https://console.cloud.google.com/projectcreate
2. Enable the Chrome Web Store API: https://console.cloud.google.com/apis/library/chromewebstore.googleapis.com
3. Service account `kiwi-cws-release` with **no roles**: https://console.cloud.google.com/iam-admin/serviceaccounts
4. Keys > Add key > JSON (downloads a file).
5. Store it encrypted for your Windows user (deletes the plaintext):
   `node vpn-extension/store/release.mjs setup-key "%USERPROFILE%\Downloads\<the file>.json"`
   (A DPAPI-encrypted key already exists at `%APPDATA%\KiwiVPN\cws-service-account.dpapi`; redo this only if it's not
   the right account.)
6. Developer Dashboard > Account > Service accounts: add the printed email (one per publisher).
7. Developer Dashboard > Publisher > Settings: copy the **publisher ID** into `vpn-extension/store/cws.json` as
   `"publisherId"` (it's empty today, so `status`/`upload` can't talk to the store API yet).
8. Check: `node vpn-extension/store/release.mjs status`

### This release (from the repo root, after merging this branch)

```
node vpn-extension/store/release.mjs status
node vpn-extension/store/release.mjs prepare            # only if anything changed since the record; else skip
node vpn-extension/store/release.mjs upload 1.0.5       # uploads the exact tested ZIP as a draft; does not submit
! node vpn-extension/store/release.mjs submit 1.0.5     # you, at a terminal: type  submit 1.0.5 <first 8 of sha256>
node vpn-extension/store/release.mjs status             # wait for "approved" / staged
! node vpn-extension/store/release.mjs go-live 1.0.5    # you, at a terminal: type  go-live 1.0.5 <first 8 of sha256>
```

The first 8 of the SHA-256 for this build: `da6e0e83`. The `!` prefix runs the command in your own terminal from
Claude Code; without a real terminal `submit` and `go-live` refuse.

In the dashboard (not the API): paste the updated description and the `storage` justification from LISTING.md, upload
the regenerated screenshots, and check the Privacy tab answers above.

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
