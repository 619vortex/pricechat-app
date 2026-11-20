# Kiwi VPN store-review account (Chrome/Edge, Google Play, App Store)

Built 2026-11-20 on `feat/kiwi-mobile`; review seats and the session guard added the same day before deploying. **Not deployed.** Code: `backend/app/vpn/reviewer.py`, `backend/app/members/service.py` (`member_for_token`), migration `0119_vpn_review_seats`, `backend/app/vpn/service.py` (`_review_device_check`, `create_device`), `backend/app/main.py` (`admin_guard`), `frontend/src/website/vpnDesktop.tsx`, `VpnReviewerPage` in `frontend/src/website/vpn.tsx`. Tests: `backend/tests/test_vpn_reviewer_mobile.py` (12) and `test_vpn_reviewer.py` (8), plus `frontend/src/website/vpnDesktop.test.tsx`.

## What a reviewer does
1. Opens Kiwi VPN on the phone and taps **Sign in**. The app opens `pricechat.live/vpn/desktop` in a Custom Tab (Android) or `ASWebAuthenticationSession` (iPhone).
2. Taps **App Store or Google Play reviewer? Use the review account**. The link only appears on a phone's sign-in.
3. Enters the review email and password on `/vpn/reviewer`. The page goes straight back to the app's sign-in page.
4. Taps **Allow**. The app receives its own scoped session (`kd.`), as with any member.
5. Taps **Connect**. The app makes its WireGuard key on the phone, registers the public key on **Helsinki 1**, and connects.
6. Can test **Delete account** for real. It deletes only that reviewer's seat. The next password sign-in makes a fresh one.

## What it can do, and what it can't
| | Review account |
|---|---|
| Seats | **Every password sign-in gets its own review member (a seat)**, so a Google Play reviewer and an App Review reviewer at the same time never share a device, a session or a deletion. The same browser signing in again keeps its seat. At most 10 seats: a new sign-in first reclaims seats with no live session, then the least recently used one. Seats have no email address |
| When | Only while `VPN_REVIEWER_EMAIL`, `VPN_REVIEWER_PASSWORD_HASH` and `VPN_REVIEWER_UNTIL` are all set, until the end of that UTC day. Works while the VPN is closed to everyone else, and needs no payment |
| Servers | Helsinki 1 only (`reviewer.SERVER`). Other servers aren't listed and are refused |
| WireGuard | **One device per seat**, only from a Kiwi VPN app session, only with a key made on the phone. A new install on the same seat replaces that seat's previous device; other seats are untouched. The website can't make one, so no config with a private key is ever handed out |
| Browser extension | One proxy login per seat |
| Website session | A `kr.` session that reaches only the VPN routes, `/members/me` (read and delete, never a profile change), sign-out, the review sign-in, `/members/auth/providers` and `/members/desktop/code`. Everything else is refused with `403 review_scope`: XP, rewards, trading, wallets, profile changes, and linking a Google, X, Telegram or email sign-in to the shared account |
| App session | The normal `kd.` app scope (`app.members.desktop_scope`) |
| Sessions | A seat accepts only the scoped `kr.` browser session and the app's `kd.` session. Any other session of a seat (for example an unscoped one from the browser-extension review before this change) is refused and deleted on its first request; migration 0119 also deletes them at deploy. No email-code, Google, X or Telegram sign-in can reach a seat |
| Admin | No Admin rights. Admin's **block** still wins, per seat; removing the variables ends every seat |
| Ending | When the date passes or the variables are removed, every seat loses access and the next sync takes its devices and logins off the server |
| Leaderboard | Off it |

Only an scrypt hash of the password is on the server. The password sign-in is rate-limited per client (`vpn_reviewer`: 5 tries, then one more every 2 minutes).

## Owner setup (after deploying, with your approval)
1. Make a mailbox only for this, for example a new Gmail address. Never use your own account.
2. In your own terminal: `python vpn-extension/store/reviewer_account.py that.address@example.com`. It sets the three Railway variables for 30 days and puts the password on your clipboard. Redeploy the `api` service so the new values take effect.
3. Check that **Helsinki 1** is enabled and online in Admin › Kiwi VPN. The review account sees no other server.
4. Sign in once yourself on a phone, following the steps above. Connect, check that the IP shown is Helsinki, then delete the account. That proves the whole path before a reviewer tries it.
5. Paste the email and password into Play Console › App content › App access, and into App Store Connect › App Review Information. Both submission documents have the wording.
6. **Keep it valid for the whole review, and for every later update's review.** Rerun the script before `VPN_REVIEWER_UNTIL` passes (30 days each time).

## Limits
- More than 10 reviewers signed in at once would reclaim the least recently used seat. Store reviews come nowhere near that.
- At deploy, migration 0119 turns the review account bound so far into the first seat and clears its email. The extension's reviewer then signs in again with the same password, which still works.
- The tests use fake VPN nodes. A real connection on Helsinki 1 with a phone is owner setup step 4.
