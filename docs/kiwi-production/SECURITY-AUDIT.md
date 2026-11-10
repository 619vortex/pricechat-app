# Kiwi VPN security audit

Date: 2026-11-10. **Read-only code review** of branch `feat/kiwi-production` (main + `feat/kiwi-monitoring` + `fix/kiwi-stage0`
+ `feat/kiwi-infra`), plus `fix/vpn-device-limit` (8cc3766, undeployed). Live servers weren't touched; live settings
were checked only through the Hetzner API (firewall rules). Paths are repo-relative; line numbers are from this branch.

**Bottom line: no Critical or High finding in the code.** The core protections are solid (below). The gaps are: things one
attacker can exhaust for everyone (rate-limit buckets, proxy slots), kill-switch release cases, free-trial abuse before paid
launch, and privacy/ops on the shared Helsinki host. Operations has the most serious open items (O1-O3).

## Findings

Severity: High = fix before paid launch / more users; Medium = fix in the next releases; Low = hardening.

### Operations (not code)

| ID | Sev | Finding | Fix |
|---|---|---|---|
| O1 | High | **Backups unproven.** `system.backup` failed every night 2026-10-23 to 2026-11-03 (bucket SignatureDoesNotMatch); success not confirmed since. The backend DB holds accounts, devices and payments. | Fix the bucket credentials, see one successful run, test one restore. |
| O2 | High | **Owner alerts go nowhere**: no owner Telegram chat set, so offline / full / bandwidth / the new fleet alerts reach only logs. | Owner sets the chat ID in Admin > Alerts. |
| O3 | Medium | Credentials pasted in chat earlier (old root password, an old Hetzner token) were still valid when last checked; Railway breach rotation list (APP_ENCRYPTION_KEY, bucket keys) still open per notes. | Rotate; confirm in Hetzner console that only the current token exists. |
| O4 | Medium | The read/write Hetzner token is in the Windows user environment and used for read-only audits too. | Second, read-only token for audits/monitoring; RW only for owner-run `create`. |
| O5 | Low | Server IPs (including Helsinki's, the owner's personal exit) appear in older repo files: `windows-client` tests/docs, `vpn-node/test_*.py`, `vpn-node/infra/lib.test.mjs`. The two docs merged here were redacted. | Replace with TEST-NET addresses (192.0.2.x) in tests on the next touch of those files (not now: Windows 2.0/2.1 files belong to other branches). |
| O6 | Low | The PC's C: drive ran out of space during this work (0 MB free), which also broke tests and git for other agents. | Owner frees space; keep build caches/VMs on D:. |

### Code

| ID | Sev | Area | Where | Finding | Fix |
|---|---|---|---|---|---|
| M1 | Medium | device limits | `backend/app/security/rate_limit.py:69-73`; `backend/app/api/routes/vpn.py:69,89,135,273` | The site-wide `vpn_device` bucket (60 then 1 per 5 s) is spent **before** the eligibility check and shared by trial, devices, browser logins and anonymous devices. Free Passports or `/anon` keys (no payment) can drain it so nobody can add a device; same for `vpn_new` (120). `fix/vpn-device-limit` splits per-member buckets but keeps this order and the shared backstops. | Check eligibility first, then charge; spend shared tokens only for eligible accounts; key the anonymous bucket by client IP as well. |
| M2 | Medium | node | `vpn-node/proxy.py:197` (count before login), `:216` (MAX_TOTAL after login), HEAD_TIMEOUT 20 s | Unauthenticated connections count toward MAX_TOTAL=3000: ~3000 idle TLS connections make the proxy answer 503 to every paying browser. No per-IP cap. | Count only logged-in connections; per-source-IP pre-login cap (~50); header timeout 5 s; optional nft connection rate limit. |
| M3 | Medium | kill switch | `windows-client/service/src/firewall.rs:69` (dynamic WFP session); `protocol.rs:28,139-147`; `docs/KIWI-2.0.md` | The kill switch releases by design: (a) ~3 s while Windows restarts a crashed service (filters die with the process); (b) at boot before the service starts; (c) after 6 failed tunnel restarts; (d) 10 min after reboot if the server never answers; (e) 2 min after a Pause. Users may expect "always closed". | A "strict" option with persistent + boot-time WFP filters (`FWPM_FILTER_FLAG_PERSISTENT` / `BOOTTIME`) removed only by Disconnect or the unblock tool; at minimum, list these cases in the app and privacy page. **For 2.2+** (2.1 is in progress on another branch). |
| M4 | Medium | billing | `backend/app/vpn/service.py:214-232` | One 30-day trial per **account**; Passports are free (email code, Google, X, Telegram), so disposable addresses give unlimited free months. | Before payments go live: one trial per verified identity (email hash + linked platform IDs), minimum account age/XP, or IP/ASN throttle on trial starts. |
| M5 | Medium | privacy / ops | `vpn-node/install-shared.sh` | Customers exit from the owner's personal VPS (wg1 beside wg0, same public IP): abuse reports and blocklists hit the owner's personal IP; a compromised agent has CAP_NET_ADMIN host-wide. | Dedicated Finland node via `install.sh` (or at least a second IPv4 with SNAT for wg1). |
| M6 | Medium | privacy | `install-shared.sh:20,144` (`DNS=1.1.1.1`) vs `backend/app/vpn/service.py:422-426` | Helsinki WireGuard customers' DNS goes to Cloudflare, not a no-logs unbound on the node, contrary to the wording. | unbound on the wg1 address, or correct the privacy text. |
| M7 | Medium | privacy | `install-proxy.sh:134,169-170`; `proxy.py:283-285` | Browser-proxy lookups go via systemd-resolved to the hosting provider's resolvers in clear text: the provider sees every domain extension users visit. | Local unbound on 127.0.0.1 for the proxy; allow only that in the guard chain. |
| L1 | Low | node | `install.sh:60,175-181`; `install-shared.sh:202` | `AGENT_ALLOW` empty = 8443 open to all, and `install-shared.sh` always opens 8443 in ufw. **Live:** all three Hetzner firewalls limit 8443 to Railway's 3 IPs (verified via API today), so this is defence in depth. `/v1/health` is unauthenticated; `ThreadingHTTPServer` is unbounded (`agent.py:429`). | Add the allowlist option to `install-shared.sh`; make AGENT_ALLOW required in `install.sh`; put `/v1/health` behind the token. |
| L2 | Low | privacy | `backend/app/vpn/billing.py:532-541` | `scrub` covers only anonymous accounts; members' payments keep `pay_address`, `provider_ref`, `pay_amount` forever (identity ↔ on-chain payment). | Null them N days after settlement for members too (keep amount/date for the books). |
| L3 | Low | privacy | `billing.py:320-327,538` | Repeat-deposit `order_ref = "np:<payment_id>"` survives the scrub; `seconds` + `credited_at` can be matched to the `paid_until` jump. | Random `order_ref`; on scrub round `credited_at` to the day and zero `seconds`. |
| L4 | Low | privacy | `backend/app/models/vpn.py` (`last_handshake`, `last_active`); `service.py` `admin_customers` | Exact "last online" per person (to the second) shown next to the username in Admin. | Store rounded to the 10-min window; coarsen to the day after 24 h. |
| L5 | Low | logging | `backend/railway.json:7` (`--no-access-log`) | Uvicorn access log is off, but Vercel/Railway edge logs still hold client IP + path for VPN API calls; retention unverified. | Check/minimise retention; state it in the privacy policy. |
| L6 | Low | device limits | `service.py:499-510,649-651` | `max_devices` counts WireGuard peers and browser logins separately (3+3); the Windows app uses one slot per server; one proxy login works from many browsers. | Decide the policy (per installation, browsers in the same budget, lower proxy connection cap). Owner decision. |
| L7 | Low | device limits | `service.py:313-317,1151-1235,539-553` | Lowering `max_devices` never removes extra devices; switching a server to testers-only leaves non-testers' devices working (sync doesn't check `sees()`). | Suspend over-limit devices and non-testers on testers-only servers in sync. |
| L8 | Low | auth | `backend/app/api/routes/desktop.py:92-98` | "Sign the app out everywhere" deletes sessions, not the app's WireGuard devices: a stolen PC keeps working keys until each device is deleted. | Option to remove that PC's devices too. |
| L9 | Low | auth | `backend/app/vpn/reviewer.py` | Reviewer rights hang on `member.email == VPN_REVIEWER_EMAIL`; unverified whether any sign-in path sets email without a code. | Bind to a stored member ID; mark its trial used. |
| L10 | Low | kill switch / IPv6 | `windows-client/service/src/guard.rs:176-178` | All ICMPv6 allowed both ways while the kill switch is on (not only neighbour discovery): IPv6 ping outside the tunnel shows the real address. | Limit to fe80::/10, ff02::/16 and ND types 133-137. |
| L11 | Low | kill switch | `guard.rs:172-175` | DHCP rule allows UDP to remote port 67 on any host from any program: a side channel past the kill switch. | Restrict remote address to broadcast + LAN ranges. |
| L12 | Low | kill switch | `guard.rs:168-170` | KiwiVPN.exe and the service are allowed everything in and out. | Outbound only: app TCP 443, service UDP to the endpoint. |
| L13 | Low | kill switch | `windows-client/service/src/config.rs` (cidr) | Any prefix accepted on `Address`; `0.0.0.0/0` would make the in-tunnel allow rule a hole. Only the app can send configs (LOCAL-SECURITY.md). | Require /32 and /128. |
| L14 | Low | DNS | `guard.rs:163` | DNS guard blocks only port 53; DoT/DoH outside the tunnel not blocked when the kill switch is off (split-tunnel ranges, pricechat.live bypass). | Optionally block 853 outside the tunnel. |
| L15 | Low | extension | `vpn-extension/background.js:23,140` | Proxy username/password in plain text in `chrome.storage.local`. | Accept (revocable) or `storage.session` + re-mint after restart. |
| L16 | Low | extension / WebRTC | `background.js:36` | WebRTC policy set with `.catch(() => undefined)`: if another extension controls it, WebRTC can leak silently. | Check `levelOfControl`; warn or refuse; same for `chrome.proxy.settings`. |
| L17 | Low | extension | `vpn-extension/lib.js:9`; `background.js:34` | pricechat.live always bypasses the proxy (site sees real IP + session); incognito never proxied. | Document both; optional incognito support. |
| L18 | Low | node logging | `install-shared.sh`, `install-proxy.sh` vs `install.sh:383-389` | Shared host and proxy hosts keep Ubuntu's default journal retention; ufw "low" logging records blocked packets with source IPs (content on Helsinki unverified). | Same 7-day `journald.conf.d` and `ufw logging off` in both scripts. |
| L19 | Low | node | `install.sh:122-173` | Only TCP 25 blocked; 465/587 open (spam if accounts are abused). | Block 465/587 as the proxy already does (`proxy.py:47`). |
| L20 | Low | ops | `install.sh:764`; `install-proxy.sh:101` | Agent token stays in `/root/kiwi-node.txt`; certbot registered without an email, so expiry warnings go nowhere. | Delete the file after registration; ops email for certbot; certificate-expiry check in `kiwi-infra.mjs health` on a schedule. |
| I1 | Info | privacy | WireGuard / conntrack | `wg show` gives root each peer's last real IP; conntrack holds active NAT flows. | Optional: re-add idle peers periodically; say it in the policy. |
| I2 | Info | auth | `windows-client/docs/LOCAL-SECURITY.md:13` | Same-user code injected into KiwiVPN.exe can point the tunnel at any server; the signed server list isn't built. | Build it with the auto-update manifest key (AUTO-UPDATE.md reserves a separate signing prefix). |
| I3 | Info | Android | `android-client/.../GoTunnelDriver.kt:97-104`; `AndroidManifest.xml:69-81` (branch) | Always-on/lockdown only read, not encouraged; a NotificationListenerService ("autocall") ships in the VPN app (Play review / privacy). | Suggest always-on + lockdown in onboarding; split autocall out or justify it. |
| I4 | Info | billing | `billing.py:233-249` | IPN accepts up to 7 body encodings, all HMAC-SHA512 + constant-time compare (safe); deeply nested JSON → RecursionError → 500. | Catch and return 400. |

Correction to an earlier note: `windows-client/docs/SIGNING.md` still says "no secrets yet", but the live 2.0.0 installer **is**
signed (SSL.com OV, verified by the release check on 2026-11-10). Update the doc's status line when that branch is next touched.

## Verified good (with paths)

- **Agent pinning**: backend pins the agent certificate by SHA-256 inside the TLS handshake, the token never goes to an unpinned peer; no proxies/redirects; TLS 1.2+ (`backend/app/vpn/node.py:39-97`). Agent tokens encrypted at rest (`service.py:68-73`).
- **Agent**: constant-time bearer compare even without a header (`vpn-node/agent.py:341-345`); request logging off (`:399`); endpoints never read; strict key/IP validation; sandboxed systemd unit (non-root, CAP_NET_ADMIN only, ProtectSystem=strict) (`install.sh:332-381`).
- **Proxy**: no per-connection logs; resolves then connects to the checked IP (no DNS rebinding); private, metadata and own ranges blocked in code and nftables; Proxy-Authorization stripped; mail ports blocked (`proxy.py:83-92,268-302`; `install-proxy.sh:118-193`).
- **Dedicated nodes** (`install.sh:122-217,383-389`): client isolation, tunnel can't reach the host, private/metadata blocked, TCP 25 blocked, IPv6 rejected fast and never forwarded, no LOG rules, unbound with logging off, 7-day journal.
- **Billing**: NOWPayments IPN HMAC-SHA512 + `compare_digest`; one `_apply_np` path (idempotent); refunds final; wrong currency → review; NaN/inf rejected; Stripe fully off behind `CARD_SUBSCRIPTIONS=False` incl. webhook; anonymous keys 160-bit, hash only (`billing.py`).
- **Admin**: everything under `/api/vpn/admin/*` needs the owner login in production (`backend/app/security/admin_auth.py:234-263`; `main.py:269-290`), including the new `/api/vpn/admin/health` (test `test_admin_health_needs_the_owner`). Cross-origin check on writes.
- **Desktop sessions**: `kd.` sessions from PKCE S256, one-time 5-min codes, scope limited to `/api/members/vpn*`, `/me`, logout, refresh; refresh rotates (`routes/desktop.py`, `members/desktop_scope.py`).
- **Device keys**: private key generated, returned once, never stored; apps can bring their own public key; no IPs/keys/endpoints in logs; uvicorn `--no-access-log`.
- **Windows service**: hostile-config parsing (no hooks, unknown keys refused); pipe DACL; Connect only from the app image or elevated callers; DPAPI with entropy; per-machine install; strict CSP; restart on crash; split-tunnel ranges re-checked (no wider than /8 or /16). Kill switch 30/30 and e2e 36/36 green on CI for the signed 2.0.0.
- **Extension**: answers only its own proxy's challenge; popup never gets the password; sender-ID check; stays closed on failure; WebRTC restricted.
- **New health data** (this branch): only per-server counts and timings; no customer identifiers; 14-day retention.

## Fix order proposed

1. O1, O2 (owner, no code). 2. M4 + M1 before turning payments on. 3. M6/M7 + M5 (privacy promises). 4. M2 (proxy DoS).
5. L10-L13 + M3 strict mode in Windows 2.2 (after 2.1 lands). 6. Remaining Lows as files are touched.
