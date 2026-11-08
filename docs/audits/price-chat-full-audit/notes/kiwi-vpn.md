# Kiwi VPN, all platforms (VPN-n)

Reviewer scope: backend/app/vpn/*, api/routes/vpn.py, desktop.py, members/desktop_scope.py, website VPN pages, vpn-node/, vpn-extension/, windows-client on main (0.1.1) and feat/kiwi-windows-2 (334f45d, 2.0.0), Android (feat/kiwi-autocall d0a051c, published RC1 e113fc6), iOS (feat/kiwi-ios-cloud 5fd451e), feat/kiwi-payments, feat/kiwi-monitoring, feat/kiwi-infra, fix/kiwi-stage0. Audit tree = main e978cc3. No server, CI run, GitHub token, Admin or Railway variable was touched.

## Summary

- **The core engineering is solid.** The node agent is pinned by certificate fingerprint inside the TLS handshake, so the bearer token is never sent to an unpinned peer. The agent compares tokens in constant time and logs nothing. The proxy refuses private ranges, its own addresses and mail ports. The Windows and Android apps generate WireGuard private keys on the device and seal them with DPAPI or Android Keystore. The website and store copy is unusually honest: no "no logs" claim, "no kill switch" disclosed, uptime shown as null. Live downloads match the published SHA-256 values. Existing VPN tests are green: 116 backend, 120 node, 10 extension.
- **Biggest business risk: the trial cliff (VPN-1).** Production has the trial on and payments off. Nobody can pay, so trials that started at launch (2026-11-04/05) all expire around 2026-12-04 with "The $9.99 plan isn't available yet". The payment fixes are on a branch that can no longer merge cleanly (VPN-11).
- **Biggest product bug: device slots (VPN-2).** Windows 2.0 and Android create one device per server. With a 3-device cap and 3 public servers, a single PC visiting every country locks the same person's phone out (409). I reproduced this in a test.
- **The rate limiter is misunderstood (VPN-3).** Commit 334f45d and the memory notes say deletes count toward the device limit. They don't. What does count is one shared 5-per-10-minute bucket for trial start, device creation and every browser-extension login. The extension re-logs in automatically, so it can starve the apps.
- **Infrastructure is fragile.** Everything runs on one Hetzner account, which was blocked on 2026-11-05 and took the whole VPN down. There are no backups. Paying customers share the owner's personal Helsinki VPS and exit IP. Ashburn is now public and "recommended" to everyone, but it includes only 1 TB of traffic (VPN-4, VPN-5).
- **Windows 2.0 is not ready to ship.** It is unsigned, and Smart App Control blocks it. No app has an update mechanism. The kill switch is fail-open by design. Smart Connect has nothing to measure against production. Real-server e2e results are UNVERIFIED from here: no reports exist locally and I had no CI access (VPN-7, VPN-8, VPN-12).
- **Biggest opportunities:**
  1. Make a "device" mean one physical device, and change its server in place.
  2. Give each action its own rate-limit bucket.
  3. Do one real NOWPayments test, then turn payments on before 2026-12-04.
  4. Move customers off the personal VPS, and add a second provider.
  5. Buy signing and ship 2.0 with an update check.

## Findings

### VPN-1: Trial on, payments off: every trial user hits a dead end around 2026-12-04
Severity: High · Status: CONFIRMED (live config + code) · Effort: M
Evidence:
- `curl https://api.pricechat.live/api/members/vpn/public` returns `{"enabled":true,"payments":false,"trial":true,"free":false,"billing":null}`.
- In service.py:42-46, `PAID_UNAVAILABLE = "Your free trial has ended. The $9.99 plan isn't available yet."`, and `no_access_reason()` (161-165) returns it when payments are off. `TRIAL_DAYS = 30`. The trial has been on since the 2026-11-04 dashboard deploy, and public access opened 2026-11-05.
- billing.py on main has no reconcile and no reminder job. Those exist only on feat/kiwi-payments, which is unmerged and has a migration conflict (VPN-11).
- Memory says no real payment has ever gone through, and the IPN signature form is unproven.

Impact: From about 2026-12-04, every trial user's devices are switched off by `vpn.sync` (lapsed), and there is no way to pay. That is the paying funnel's first cohort, lost. Anonymous keys are refused while payments are off (vpn.py:240), so they are no escape hatch either.

Fix, before 2026-12-01:
1. Rebase feat/kiwi-payments and renumber its migration.
2. Do one real ~$10 USDT payment in test mode.
3. Turn payments on.
4. Ship the renewal reminders.

If payments aren't ready, decide deliberately: either free mode (trial off and payments off means free for everyone), or a tester grant for existing users. Also show the end date in the apps; `plan.days_left` already exists.

### VPN-2: One device per server, a 3-device cap and 3 public servers: one PC locks out the person's phone
Severity: High · Status: CONFIRMED (code + audit test) · Effort: M
Evidence:
- Windows 2.0 `client/src/flow.rs:28-34,80-92`: `device_name(computer, server)` is "<host> (<server id>)". A new server means a new key and `add_device`. The 409 error tells the user to "Remove one under My Devices on pricechat.live".
- Android `vpn/Provisioner.kt:81-90` and `DeviceIdentity.kt:25` do the same per server.
- The cap is `max_devices` 3 (service.py:42). Live `/stats` lists 3 public servers: Germany, Helsinki 1, United States (Ashburn).
- Test `backend/tests/audit_vpn_limits.py::test_audit_one_device_per_server_fills_the_cap` passes. One "PC" on 3 servers gets 201 three times, then the phone gets 409.
- Smart Connect failover in 2.0 switches servers automatically, so it burns slots without the user noticing.

Impact: Normal use, such as trying each country once, silently consumes the whole account. The next platform fails with an error that sends the user to the website. Each extra server makes this worse. Server switching also burns the rate limit (VPN-3).

Fix: Count devices, not peers. Add `PATCH /members/vpn/devices/{id}` that moves the same public key to another server (delete the peer on the old node, put it on the new one, reallocate the IP). Alternatively, let one device own peers on several servers and count distinct device ids toward the cap. The apps then keep one key per installation.

### VPN-3: The rate limiter: deletes are NOT counted; trial, devices and browser logins share one 5-per-10-minute bucket
Severity: Medium · Status: CONFIRMED (code + audit tests) · Effort: S
Evidence:
- `rate_limit.py:69`: `vpn_device` is burst 5, then 1 per 600 s.
- It is hit by:
  - `POST /members/vpn/trial` (vpn.py:69)
  - `POST /members/vpn/devices` (vpn.py:89)
  - `POST /members/vpn/browser` (vpn.py:135)
  - anonymous device creation (vpn.py:252)
  - the Telegram `/vpn add` command (tradingos/bot.py:641)
- `DELETE /members/vpn/devices/{id}` (vpn.py:117-123) has no limiter.
- `audit_vpn_limits.py::test_audit_deletes_do_not_spend_the_device_limit` passes: 3 creates, 3 deletes, then 2 more creates succeed and the 6th is 429.
- `test_audit_browser_logins_and_devices_share_one_bucket` passes: 4 extension logins plus 1 device, then the phone's device gets 429.
- The extension calls `newLogin` on every server change and in `recheck()` after any failed connection check (vpn-extension/background.js:63-66, 104-111), with no backoff.
- Windows 2.0 commit 334f45d says "deletes count against Price Chat's limit too". That is wrong per code, so the e2e redesign rests on a false premise. The real cause of the blocked Helsinki/Ashburn/country-switch runs was creates: one device per server (VPN-2).

Impact: A flaky Wi-Fi in the browser can lock the same account's Windows or Android app out of connecting a new server for up to 10 minutes, and vice versa. CI e2e planning is based on a wrong model.

Fix:
1. Give trial, device and browser their own buckets.
2. Don't charge a token when `replace` swaps a login in place.
3. Add exponential backoff to the extension's `recheck`.
4. Correct the 334f45d comment and the memory note.

### VPN-4: Single provider, single account, customers on the owner's personal VPS, no backups
Severity: High · Status: CONFIRMED (code/DNS) / UNVERIFIED (Hetzner state, from memory notes) · Effort: L
Evidence:
- `hel1.vpn.pricechat.live` resolves to 2.29.149.29. Memory identifies this as the owner's personal VPS (wg0 with the personal peer client1). Customers are on wg1 via `vpn-node/install-shared.sh`.
- All three servers (hel1, fsn1, ash1) are in one Hetzner project.
- Memory records a full outage on 2026-11-05 (about a day) when Hetzner blocked the account.
- Memory audit: "No backups, no delete protection" (protection was later turned on for hel1/fsn1; ash1 unknown).
- `install-shared.sh:202` runs `ufw allow 8443/tcp` from anywhere. Re-running it reopens the agent port; the Hetzner firewall is the only narrowing layer.

Impact:
- One abuse complaint or billing problem takes every country offline.
- Customer abuse (DMCA, attacks) is attributed to the owner's personal IP, and the owner's personal traffic is mixed with customers'.
- Rebuilding a node means re-keying every customer. The server private key isn't backed up, so all devices would need new configs.

Fix:
- Move customers off the personal VPS.
- Add a second provider. The expansion plan already proposes one for AMS/LON.
- Back up `/etc/wireguard` and `/etc/kiwi-vpn` encrypted, off-box.
- Make install-shared honour `AGENT_ALLOW`, as install.sh does on feat/kiwi-infra.
- Write an abuse-handling runbook.

### VPN-5: Ashburn is public and "recommended" to everyone, with 1 TB included and no data cap
Severity: Medium · Status: CONFIRMED (live) · Effort: S
Evidence:
- Live `/api/members/vpn/stats` shows `{"id":3,"name":"United States (Ashburn)",...,"recommended":true}`. Memory says the owner had asked for Ashburn to be testers-only; it is now public.
- `recommended_id()` (service.py:349-352) picks the lowest device-count load, so a new, empty server wins for everyone, EU users included.
- Windows `suggested()` (flow.rs), the extension's `pickServer` and the bot all default to it.
- Ashburn costs $21.09/mo with 1 TB included, then $1.20/TB (memory, Hetzner prices of 2026-11-07).
- `monthly_cap_gb` defaults to 0 (unlimited).
- The traffic-allowance alerts are on unmerged feat/kiwi-monitoring (migration 0106).

Impact: EU users get transatlantic latency by default. A handful of heavy users can push Ashburn past its included traffic. There is no alert until monitoring merges.

Fix:
- Recommend by region, using the client's country from the request IP on the server, or by measured RTT.
- Weight recommendations by remaining included traffic.
- Merge monitoring.
- Consider a fair-use cap on US servers.

### VPN-6: One failed sync marks a server offline; alerts go to an owner chat that may not exist
Severity: Medium · Status: CONFIRMED (code) / UNVERIFIED (owner chat) · Effort: S
Evidence:
- `sync_server` (service.py:1029-1033) sets `server.online=False` on the first `NodeError`.
- `_checked_server` (472-473) and `create_browser` (608-609) then refuse with 503 until the next sync, 5 minutes later.
- The fix ("one failed check no longer takes a server offline") is on fix/kiwi-stage0 31c6148, undeployed.
- Alerts (alerts.py:242-279) fire after 2 failures through `notify_owner`. Memory of 2026-11-05 says no owner Telegram chat was set, so alerts went only to logs. I couldn't verify the current setting: the `railway` CLI wasn't on PATH in this shell.
- There is no external uptime monitoring; `public_stats` returns `uptime: None`.

Impact: Transient blips block new connections and extension logins for 5 minutes. A real outage may go unnoticed.

Fix: Merge the flap guard. Confirm the owner alert chat with a test alert. Add an external probe (e.g. Healthchecks or UptimeRobot on `/v1/health`, which needs no auth).

### VPN-7: No update mechanism on any platform; Windows 2.0 is unsigned
Severity: Medium · Status: CONFIRMED · Effort: M (code) + cost
Evidence:
- `windows-client/app/src-tauri/tauri.conf.json` (2.0.0) has no updater plugin. Capabilities are `core:default` only. Public 0.1.1 has none either.
- The live `KiwiVPN-Setup.exe` has SHA-256 077ae0f6…0598, matching `WINDOWS_BETA` in vpnConfig.ts. `Get-AuthenticodeSignature` reports **NotSigned**.
- The CI workflow (ci/kiwi-windows.yml:6-7, 218-225) produces `…-unsigned-Setup.exe` with "signed no".
- The Android APK can't self-update outside Play. The live RC1 hash cb52f939…06c7 matches.

Impact:
- Everyone on 0.1.1 (no kill switch, older reconnect logic) stays there and never learns about 2.0.
- Smart App Control blocks every Windows build. A fix for a security bug can't be pushed.
- Upgrading 0.1.1 to 2.0 in place (service replacement) is untested: CI installs fresh.

Fix:
1. Add a signed version check. A minimal option is a JSON at pricechat.live with version, URL and SHA-256, signed with an Ed25519 key embedded in the app. Alternatively use the Tauri updater with its own signing key.
2. Add a CI test for upgrading over 0.1.1.
3. Code signing: memory says Azure Artifact Signing is open only to organizations in the US, CA, EU or UK. The owner appears to be outside these. An OV certificate from a public CA needs an HSM or token, plus SmartScreen reputation build-up. Price is UNVERIFIED here; it typically runs a few hundred USD per year.

### VPN-8: The Windows 2.0 kill switch is deliberately fail-open; describe it precisely before marketing it
Severity: Medium · Status: CONFIRMED (code/docs); real-tunnel behaviour UNVERIFIED · Effort: S (copy) / M (design)
Evidence:
- `service/src/guard.rs:1-11` and `firewall.rs:1-2`: the filters live in a dynamic WFP session and vanish when the service process ends.
- docs/KIWI-2.0.md:41-45: the block is released after 6 failed tunnel restarts, after 10 minutes without a handshake following a reboot, and after a 120 s Pause with no Connect.
- KiwiVPN.exe and the service are exempt from the block (guard.rs:5).
- The website (vpn.tsx:517, 748; community.tsx:360) still says, correctly for 0.1.1, "There's no kill switch".
- The CI `kill-switch` job (28 checks) uses a TEST-NET server. Real-server kill-switch and leak results are claimed in memory and the brief, but no report exists on this PC.

Impact: If 2.0 is marketed as having a "kill switch" without these caveats, users on a crashing service or a dead server get silent leaks after the release timers. That is a misrepresentation risk.

Fix:
- Website and app copy must say when the block is released.
- Consider a persistent "block until I disconnect" mode, using a persistent sublayer with a boot-time unblock tool, as already designed in docs/KIWI-2.0.md.
- Keep the e2e kill-switch reports as release artifacts.

### VPN-9: Server-generated private keys travel through Telegram (and Railway/Vercel); the privacy page doesn't mention Telegram
Severity: Medium · Status: CONFIRMED (code) / SUSPECTED (bot live in prod) · Effort: S
Evidence:
- `tradingos/bot.py:612-652`: `/vpn add` calls `vpn.create_device(...)` without a public key, so `keys.generate_keypair()` runs on the server (service.py:549). The bot then sends the `.conf` with `PrivateKey` as a Telegram document. Telegram bot chats are cloud-stored and not end-to-end encrypted.
- The website path (`POST /members/vpn/devices` without `public_key`) also generates the key on Railway and returns it through the Vercel proxy.
- The privacy page (community.tsx:341) says the private key is "created on our server, handed to you once in its config, and not stored". Telegram isn't listed under "Who else is involved".

Impact: A private key kept indefinitely by a third party is a privacy-claim gap. It also contradicts the apps' "never sent to us" model.

Fix:
- Drop `/vpn add`, or send only a link to the website.
- Generate keys in the browser for the website flow (WebCrypto X25519 is in all current browsers; send only `public_key`, which the API already accepts).
- Update the privacy page.

### VPN-10: No Kiwi VPN terms, acceptable-use or refund policy is live while the service is public
Severity: Medium · Status: CONFIRMED · Effort: S (owner/legal)
Evidence: `grep vpnterms|vpnrefunds|Acceptable` over frontend/src on main finds nothing (`/vpn/terms` returns 200 only because the single-page app falls back to its index). The drafts exist only on feat/kiwi-payments, unmerged; memory says "legal review … owner must accept".
Impact: The VPN carries anonymous strangers' traffic on the owner's IPs with no AUP to point to when Hetzner forwards abuse reports. Users have no refund terms once payments start.
Fix: The owner reviews and publishes terms (including an AUP and the abuse process) before turning payments on.

### VPN-11: Branch sprawl: payments migration conflicts with main; about 25 unmerged Kiwi branches
Severity: Medium · Status: CONFIRMED · Effort: M
Evidence:
- feat/kiwi-payments adds `0103_vpn_payment_recovery.py` with `down_revision '0102_desktop_codes'`. Main already has `0103_newsdesk_telegram`, `0104_newsdesk_events` and `0105_vpn_server_testers_only`, so merging creates two Alembic heads.
- feat/kiwi-monitoring adds 0106 on top of 0105.
- `git branch -a | grep kiwi` lists 25 branches. Several touch vpn.tsx and service.py: payments (+174 lines in service.py), monitoring, unified, stage0.

Impact: Every release requires manual conflict and migration surgery. Fixes like the flap guard and payment recovery sit undeployed for days.

Fix: Rebase payments onto main, renumber its migration to 0107 after monitoring's 0106, and merge in order: stage0, monitoring, payments. Close the dead branches.

### VPN-12: Smart Connect (Windows 2.0, extension 1.0.4) has nothing to measure against production
Severity: Medium · Status: CONFIRMED (code) · Effort: S (deploy)
Evidence:
- `service.overview()` (service.py:421-422) sends `load` and `browser: bool` only, with no `proxy_host` or `live_load`.
- The Windows client expects `proxy_host`/`live_load` (client/src/api.rs:37-41; app/src/smart.ts:22). These are added only by unmerged feat/kiwi-monitoring.
- Germany has no proxy at all (`"browser":false` live), so even with monitoring merged it can't be timed through the proxy host.

Impact: "Fastest server" falls back to the backend's load ranking, which currently means Ashburn for everyone (VPN-5). The feature looks broken.

Fix: Merge monitoring first. Give every node a probe endpoint, such as a TCP or HTTPS `/v1/health` on a public port or the WireGuard handshake time, rather than relying on the browser proxy.

### VPN-13: Mobile readiness: a public Android RC carries Play-sensitive permissions; iOS has never run on a device
Severity: Medium · Status: CONFIRMED (code) / UNVERIFIED (device behaviour) · Effort: M
Evidence:
- The published RC1 (e113fc6, live at /downloads/KiwiVPN-0.1.0-rc1.apk, hash matches) declares `FOREGROUND_SERVICE_SYSTEM_EXEMPTED` with `foregroundServiceType="systemExempted"` and a `BIND_NOTIFICATION_LISTENER_SERVICE` service (Auto-Call).
- Memory flags both as Play-approval risks and says no team test has run on a physical phone; there is only the owner's verbal report.
- iOS (feat/kiwi-ios-cloud) compiles unsigned on Codemagic per memory but has never run on an iPhone.
- `grep -ri "delete account"` over ios-client Swift finds nothing. App Store guideline 5.1.1(v) asks for in-app account deletion, and 5.4 requires VPN apps to come from an organization account.

Impact: The current Android build may not be publishable on Play as-is. iOS needs an organization (D-U-N-S) account before any App Store submission.

Fix:
- Strip Auto-Call from the Play build flavour.
- Run the device test plan (test 47: systemExempted on Android 14+).
- Owner decision on an organization entity, which also unblocks Windows signing (VPN-7).
- Add account deletion: a link to website deletion is acceptable if it's reachable in-app.

### VPN-14: CI e2e runs on the owner's real account with a 90-day session secret
Severity: Low · Status: CONFIRMED (workflow) · Effort: S
Evidence:
- `ci/kiwi-windows.yml:333` sets `KIWI_CI_SESSION: ${{ secrets.KIWI_CI_SESSION }}`. Per memory, this is the owner's desktop "kd." session.
- Its scope (members/desktop_scope.py:7-8) includes `/api/members/me` and everything under `/api/members/vpn/`: devices, browser logins, USDT invoices.
- The private repo 619vortex/KIWIVPN is shared with the iOS export.
- The e2e consumes the owner's device slots and rate-limit tokens. The Android Pixel uses the same account.

Impact: Anyone with push access to KIWIVPN can exfiltrate the session through a workflow. Test runs interfere with the owner's own devices.

Fix: Use a dedicated tester account (`/vpn/admin/testers`, up to 90 days) on a testers-only server. Revoke the session after the test campaign.

### VPN-15: Testers-only servers keep working for people who are no longer testers
Severity: Low · Status: CONFIRMED (code) · Effort: S
Evidence: `sees()` (service.py:248-250) is checked only at creation (`_checked_server`, `create_browser`). `device_peer()` (505-519) and `sync_server()` never call it. When `tester_until` passes, or a server is switched back to testers-only, existing devices and browser logins stay on the node as long as the account is otherwise active. With the trial on, everyone is active.
Impact: Unreleased servers leak to former testers.
Fix: In sync, treat `not sees(acct, server)` like "lapsed" for that server. In `device_peer`, return 404.

### VPN-16: Trial abuse and silent trial start
Severity: Low · Status: CONFIRMED (code) / SUSPECTED (abuse) · Effort: S
Evidence:
- One trial per `VpnAccount`, and each Passport member gets its own account (service.py:206-224). There is no device, payment or IP fingerprint, and a Passport is created through Google, X or Telegram sign-in.
- The Windows 2.0 `account_starting_trial` (flow.rs:126-137) starts the single trial automatically right after sign-in, before the user picks a server.
Impact: Unlimited free use through new Google accounts. Users burn their trial by merely installing.
Fix: Start the trial on first Connect. Optionally tie trials to a hash of the OAuth identity, which persists across Passport deletion; there is a precedent in the members delete flow.

### VPN-17: Capacity is a device count (default 250), not throughput
Severity: Low · Status: SUSPECTED · Effort: S
Evidence:
- `VpnServer.capacity` defaults to 250 (models/vpn.py:41). `_load` is devices divided by capacity (service.py:345-346).
- The Germany node is a cpx02 with 1 GB RAM per memory. Hetzner reported 2 servers at about $22/mo combined before ash1.
- There is no CPU, bandwidth or concurrent-handshake signal. `live_load` exists only on the monitoring branch.

Impact: 250 devices on a 1-vCPU box would saturate long before the "nearly full" alert at 90%.

Fix: Set per-server capacity from a benchmark (e.g. node-selftest.sh plus an iperf run), and feed live handshake counts into the load figure.

## What is good (verified)

- **Agent TLS pinning:** checked in the handshake before any request (node.py:47-77). The test `test_pinning_refuses_a_wrong_certificate_before_sending_the_token` passes.
- **Agent hygiene:** constant-time token check, request logging disabled, body cap of 4 KB, peer IPs validated against the pool (agent.py:172-184, 341-345, 399).
- **Proxy:** refuses non-global destinations and its own IPs (`PUBLIC_IPS`), blocks ports 25/465/587, and accepts only a 64-hex hash per login (proxy.py:45, 76-85).
- **Apps' private keys:** generated on the device. Windows uses getrandom with DPAPI and app entropy (client/src/keys.rs, store.rs). Android uses Keystore with `noBackupFilesDir` and `allowBackup=false`. The backend stores only public keys and refuses a duplicate public key (service.py:544-547).
- **Windows service named pipe:** SYSTEM plus interactive users get read/write only, without the create-instance right. Remote clients are rejected. Only the elevated caller or Program Files\KiwiVPN\KiwiVPN.exe may connect, and requests are strict-schema (windows.rs:34-39, 876-909; protocol.rs:162-187).
- **Desktop session scope:** "kd." tokens are limited to the VPN API, /me, logout and refresh, enforced in middleware (main.py:271). The code exchange is PKCE, single use, valid for 5 minutes.
- **Website and store copy:** honest. No "no logs" claim, "no kill switch" disclosed, "we don't measure speed", uptime null. Countries shown (3: DE, FI, US) come from live servers.

## Untested / couldn't verify (and why)

- **Windows 2.0 real-server e2e** (Germany pass; Helsinki/Ashburn blocked; kill switch on a real tunnel): no CI report or artifact exists under C:\AI\pc-wt\kiwi-release\ci (only installers 532c4c9 and 7e74c55). I was told not to use the GitHub token or trigger runs, so these remain owner/memory claims.
- **Current Railway env** (owner alert chat, `VPN_EXTENSION_ORIGINS`, monthly cap value, reviewer variables): Railway variables are off-limits. `railway logs` failed because the CLI wasn't on PATH in this shell.
- **Live server state** (firewalls, ufw, unbound logging, journald retention, delete protection on ash1, wg0/wg1 isolation): not touched, per the hard rules.
- **NOWPayments IPN signature format and a real payment:** none has ever happened.
- **Android on a phone, iOS on an iPhone, the Chrome extension signed in on a real account:** no devices available here.
- **The Telegram bot's `/vpn` in production:** whether the trading bot's update loop runs in prod depends on env.
- **Hetzner EU included-traffic figures and code-signing prices:** not looked up live.

## Improvement and feature ideas (ranked)

1. **Paid launch before 2026-12-04:** rebase payments, one real payment, reminders, terms. Without it, the funnel ends in a dead end (VPN-1, VPN-10, VPN-11).
2. **One device per physical device, with in-place server moves:** fixes cross-platform lockouts, makes Smart Connect failover free of slot and rate-limit costs, and simplifies Admin (VPN-2, VPN-3).
3. **Separate rate-limit buckets plus extension backoff:** a small change with a large reliability gain (VPN-3).
4. **Dedicated customer nodes and a second provider:** removes the single-account outage risk and the owner's personal IP exposure (VPN-4).
5. **Signed update channel for Windows and the APK:** without it every fix is stranded. Pair it with the signing decision (VPN-7).
6. **Region-aware recommendation plus traffic-aware load:** better latency and protects the US bandwidth budget (VPN-5, VPN-12, VPN-17).
7. **Browser-side key generation on the website and retiring Telegram `/vpn add`:** makes "your private key never leaves your device" true everywhere, a strong marketing line (VPN-9).
8. **External uptime probe plus a public status page:** honest "uptime" instead of null (VPN-6).
9. **Persistent "block until I disconnect" kill-switch mode:** what privacy-focused users expect (VPN-8).

## Commands run (for the evidence appendix)

- Read code: `backend/app/vpn/{service,billing,keys,node,reviewer}.py`, `api/routes/{vpn,desktop}.py`, `members/desktop_scope.py`, `security/rate_limit.py`, `alerts.py:242-279`, `main.py:255-290`, `tradingos/bot.py:612-652`, `vpn-node/{agent,proxy}.py`, `install-shared.sh`, `install.sh` (grep), `vpn-extension/{manifest.json,background.js,lib.js,store/LISTING.md}`, `frontend/src/website/{vpn.tsx,vpnConfig.ts,community.tsx}` (grep).
- Read Windows 2.0 (C:\AI\pc-wt\kiwi-win2\windows-client @334f45d): `client/src/{flow,keys,store,api,measure}.rs`, `service/src/{protocol,guard,firewall}.rs`, `windows.rs:34-39,876-930`, `ci/kiwi-windows.yml` (grep), `docs/KIWI-2.0.md` (grep), `app/src-tauri/tauri.conf.json`.
- Read Android (C:\AI\pc-wt\kiwi-android @d0a051c) by grep, and `git show e113fc6:android-client/app/src/main/AndroidManifest.xml`.
- `git -C C:\AI\price-chat grep -n vpn_device <branch> -- backend/app` for 6 branches; `git log -S'LIMITS["vpn_device"]'`; `git show 334f45d`.
- `git ls-tree main backend/migrations/versions/`; `git diff --name-only --diff-filter=A main...<branch> -- backend/migrations/versions/`; `git show feat/kiwi-payments:…/0103_vpn_payment_recovery.py | grep revision`. Output: down_revision 0102_desktop_codes, which conflicts with main 0103_newsdesk_telegram.
- `curl https://api.pricechat.live/api/members/vpn/stats`, which returns 3 servers, Ashburn recommended, active_users 5, uptime null.
- `curl https://api.pricechat.live/api/members/vpn/public`, which returns enabled, trial on, payments off.
- Downloaded both live binaries and ran `sha256sum`: setup.exe 077ae0f6…0598 and rc1.apk cb52f939…06c7, both matching vpnConfig.ts. `Get-AuthenticodeSignature setup.exe` returns NotSigned.
- `nslookup hel1.vpn.pricechat.live` returns 2.29.149.29; `ash1.vpn.pricechat.live` returns 178.156.252.184.
- New diagnostic file `backend/tests/audit_vpn_limits.py`, run with the pyshim: `pytest tests/audit_vpn_limits.py -q` gives 3 passed. It proves: deletes don't spend the limit; browser logins and devices share a bucket; one device per server fills the cap.
- `pytest tests/test_vpn*.py -q` gives 116 passed. `vpn-node: pytest test_agent.py test_proxy.py -q` gives 120 passed. `vpn-extension: node --test lib.test.js` gives 10 passed.
- `railway logs` was not run: the CLI wasn't on PATH from Git Bash or the PowerShell job.
