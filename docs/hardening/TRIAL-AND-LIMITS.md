# Kiwi VPN: free-trial abuse, rate limits, device and auth fixes

Branch `feat/kiwi-hardening-trial` (from main `a7d2081`). Security audit items M1, M4, L6, L7, L8, L9 and I4
(`git show feat/kiwi-production:docs/kiwi-production/SECURITY-AUDIT.md`). Nothing is deployed. Payments and their
defaults, `CARD_SUBSCRIPTIONS` and billing behaviour are unchanged, apart from I4.

## What changed

### M1: check first, then charge the rate limit
Every Kiwi VPN write route now runs its checks before it spends a rate-limit token. A refused request (no access, trial
already used, unknown server, device limit reached, payments off) costs nothing. That means it also never spends the
shared site-wide backstop, so only accounts that would actually succeed can drain it.

| Route | Checks first (free) | Then charges |
|---|---|---|
| `POST /members/vpn/trial` | trial off or closed, already used, has access, blocked, identity, age, XP, address throttle | `vpn_trial` (per member + shared) |
| `POST /members/vpn/devices` | `service.device_precheck`: eligibility, reviewer, monthly cap, device slots, server visible | `vpn_device` or `vpn_replace` (unchanged) |
| `POST /members/vpn/browser` | `service.browser_precheck`: eligibility, cap, proxy server visible | `vpn_browser` (already its own bucket) |
| `POST /members/vpn/anon` | VPN open, payments on | `vpn_new` (per client) |
| `POST /members/vpn/anon/devices` | `device_precheck` | **new** `vpn_anon_ip` per real client address (burst 10, then 1 per 10 min), then `vpn_anon_device` per key |
| Telegram `/vpn add` | `device_precheck` | `vpn_device` |

The anonymous-device bucket is keyed by client address only when the address is real (see "Real address" below). That
way website visitors who share Vercel's egress addresses are never limited together. The per-address `vpn_anon_ip`
bucket applies **only in `enforce` mode**, so in `monitor` and `off` modes no request is refused or charged that main
wouldn't refuse or charge. The check-then-charge ordering itself is a fix and stays on in every mode.

### Trial guard modes (`trial_guard_mode`)
The owner wants the restrictions off at first. Every M4 guard below runs under one switch. The default is **`monitor`**,
both for new settings rows and for the existing row (set by migration 0113's server default).

| Mode | Guards evaluated | Refuses | Records would-be refusals | Writes `vpn_trial_claims` | Counts starts for the address throttle |
|---|---|---|---|---|---|
| `off` | no | no | no | **yes** (see below) | no |
| `monitor` (default) | all: identity, age, XP, per address, per /24 | **never** | yes, in `vpn_trial_signals` | yes | yes |
| `enforce` | all | yes, with the reasons below | no | yes | yes |

- **What monitor records.** One `vpn_trial_signals` row for each guard that would have refused a trial start that went
  through. Each row holds:
  - `code`: `identity_used`, `account_too_new`, `needs_xp`, `ip_limit` or `network_limit`;
  - the time (`created_at`);
  - `member_id` (set to NULL if the member is deleted);
  - for `ip_limit` and `network_limit` only, `net_hash`: a salted SHA-256 of the caller's IPv4 /24 or IPv6 /48. This is
    enough to spot many starts from one network, such as CGNAT or an office. **No IP address is stored**, not even
    truncated.

  Rows older than **30 days** are deleted whenever a new row is written. The address counters themselves stay in memory
  only, as before.
- **Why `off` still writes claims.** Writing claims refuses nobody. It only records "this identity has had a trial". If
  `off` skipped it, every trial started while the guards were off would be invisible to `enforce` later. That is the
  only thing `off` writes.
- **Users see nothing in `monitor`.** `GET /members/vpn` reports the trial as available whenever only the guards would
  object, and the apps keep showing "Start your free trial". The only refusals are main's own: trial off or closed,
  already used, already has access, blocked.
- **Switching to enforce later:**
  1. Check the counts in the Admin card. Watch especially "networks seen more than once" and the `ip_limit` and
     `network_limit` counts, since those show shared addresses that would be false positives.
  2. Set the thresholds.
  3. Pick **Enforce** in Admin › Kiwi VPN › "Free trial guards" and save. Or send
     `PUT /api/vpn/admin/settings {"trial_guard_mode": "enforce"}`.

  It takes effect on the next trial start. No migration or deploy is needed, and existing trials are untouched. Trials
  started while in `monitor` or `off` mode already have their claims, so their identities can't start a second one.

### M4: one free trial per person
- **One trial per verified identity** (`trial_one_per_identity`, default **on**). The table `vpn_trial_claims` keeps a
  salted SHA-256 of every identity a trial was used with: the verified email, normalised so that `a.b+x@gmail.com` and
  `ab@gmail.com` count as one address (lower-case, `+tag` dropped, Gmail dots dropped), plus each linked Google, X and
  Telegram account. Hashes are written in four places:
  - when a trial starts;
  - when a member who has had a trial links another identity or adds an email, so identities can't be passed through an old account;
  - when the store-review account is bound;
  - by migration 0113, for every trial started before it.

  Like `xp_claims`, the hashes survive account deletion and store no identity. Salt: `kiwi-vpn-trial-v1`, a constant
  like the XP one. A per-server secret would make every claim worthless whenever the key is rotated.
- **Minimum Passport age** (`trial_min_account_age_hours`, default **24**, 0 = off), measured from `members.created_at`.
- **Minimum XP** (`trial_min_xp`, default **0** = off). Uses reputation XP (`xp_total`); counts as 0 while XP is suspended.
- **Soft address throttle**, in memory, counting successful starts over 24 hours:
  - `trial_per_ip_day`, default **3** per address (an IPv6 /64 counts as one address);
  - `trial_per_network_day`, default **10** per IPv4 /24 or IPv6 /48. This stands in for ASN: there is no ASN database.

  It applies only to real addresses. A restart forgets the counts.
- **Clear reasons.** A refusal returns the message in `detail` and a machine code in the `X-Kiwi-Reason` header. Status
  is 403, or 429 for the address throttle, which also sends `Retry-After`. `GET /members/vpn` now returns
  `trial: {available, days, code, reason}`. While a guard blocks the trial, the top-level `reason` (what the apps show)
  carries the guard's message instead of "Start your free trial".

  Codes: `off`, `no_passport`, `used`, `has_access`, `blocked`, `identity_used`, `account_too_new`, `needs_xp`, `network_limit`.
- **Existing trials, paid time, grants, testers and Royal access are untouched.** The guards run only when a new trial
  starts (tested: a running trial stays active and its device stays on the node after every guard is tightened).

**Real address** (`trial_guard.real_ip`): a `PROXY_SHARED_SECRET` is configured, and one of the following holds:
- the request came through the website with the valid secret header (Vercel reported the visitor);
- or it came straight to the API with no `x-vercel-id` header (the desktop and Android apps, the extension).

Website traffic without the secret resolves to Vercel's egress addresses, so it is never counted. With no secret
configured at all, nothing is counted, because the address is then the left-most X-Forwarded-For entry, which the
caller can forge. Known limit: a direct caller can add a fake `x-vercel-id` header to skip the soft throttle. The
identity, age and XP rules are the real gates.

### L6: one PC, one slot (opt-in)
- **Investigation.** The clients send no stable installation ID. The Windows app (`windows-client/client/src/flow.rs`,
  `device_name`) names each device `"<PC name> (<server id>)"`, makes its own key per server, and remembers device IDs
  per server in its vault. The extension's proxy logins carry nothing either. Android isn't on main.
- **Built:** `count_pc_once` (default **off**). When on, a member's devices named `"<X> (<n>)"`, where `n` is that
  device's own server, share one slot per `<X>`. A second device of the same PC on the same server takes another slot.
  Anything else is one slot each. Browser logins still have their own budget (`max_devices`, oldest retired).
- **Trade-off.** The PC name is chosen by the client, so with the setting on, one account can hold one device per
  server for each name it uses. That is bounded by servers × `max_devices`, and every device is still a separate key
  that can be handed out.
- **Proper design (later):** the app generates a random installation ID once (stored with DPAPI) and sends it on
  `POST /members/vpn/devices`. The backend stores `vpn_devices.installation_id` and counts distinct IDs, with no name
  heuristics. That needs a Windows release, so it isn't done here.

### L7: sync suspends past the limit (opt-in)
`sync_enforce_limits` (default **off**). When on, the 5-minute sync switches off (removes the peer or proxy login on the
node, **keeps the row**):
- devices past `max_devices`. The oldest keep their slots, counted with `count_pc_once` if that is on. Browser logins
  past the limit are switched off the same way, oldest kept;
- every device and browser login on a testers-only server whose account may not see it (not owner, not a current tester).

Raising the limit, re-adding a tester or opening the server brings them back at the next sync. The owner account and the
store reviewer are exempt from the limit. With the setting off, sync behaves exactly as before, so the Ashburn
assumption "testers_only never removes existing devices" still holds (tested).

### L8: signing the app out everywhere can remove its devices
`POST /members/desktop/sessions/revoke` accepts an optional body `{"remove_devices": true}`. With it, the WireGuard
devices the app made are deleted from the node and the database:
- devices with the new `vpn_devices.created_via = "app"`, made through a `kd.` app session;
- devices made before 0113 that are named the Windows-app way (`"<PC> (<server id>)"`).

Website, bot, anonymous and Admin devices and browser logins stay. With no body, nothing changes, as before. The
response adds `devices_removed`. The website's "Sign out on every PC" now has an unticked checkbox, "Also delete the
app's VPN devices". Note: the session table doesn't record which PC made a device, so this means every PC's app devices.
That matches the button, which signs out every PC.

### L9: reviewer bound to a member ID
`vpn_settings.reviewer_member_id` holds the store-review member. `reviewer.is_reviewer(db, member)` compares IDs, so
the member's email no longer grants anything. Binding happens on the first password sign-in:
- If a Passport that the review sign-in did not create already holds the review address, the sign-in is refused (the
  same error as any failure) and no rights are granted.
- Migration 0113 binds the existing review member (found by its `vpn_reviewer_created` audit row), so a review in
  progress keeps working.

The review account's trial is marked used (and over), and its email is added to `vpn_trial_claims`.

### I4: deeply nested IPN JSON
`billing.nowpayments_webhook` now catches `RecursionError` along with `ValueError` and returns 400. Nothing else in billing changed.

### Admin
Admin › Kiwi VPN has a new card, "Free trial guards and device rules", with every setting above. It saves only what
changed, and it is hidden if the backend doesn't send these fields. The card shows:
- the mode selector (Off, Monitor only, Enforce);
- "Would have been refused": a count per reason for the last 7 and 30 days;
- "Networks seen more than once": the number of distinct hashed /24s with more than one `ip_limit` or `network_limit` row;
- the 10 most recent rows, each with its time, reason and member number.

The card's data comes from `trial_signals` in `GET /api/vpn/admin/overview`, which also lists the 20 latest rows. All the settings are also accepted by
`PUT /api/vpn/admin/settings`, with the following ranges:

| Setting | Range |
|---|---|
| age | 0–2160 h |
| XP | 0–1,000,000 |
| per address | 0–1000 |
| per network | 0–10,000 |

## Defaults and owner decisions

| Setting | Default | Owner decides |
|---|---|---|
| `trial_guard_mode` | **monitor** | Nobody is refused by the guards. Switch to `enforce` once the counts look right (see "Trial guard modes"). |
| `trial_one_per_identity` | on | Takes effect only in `enforce`; keep on (recommended). |
| `trial_min_account_age_hours` | 24 | A brand-new Passport waits a day for its trial and sees why. Lower it (or set 0) if that hurts sign-ups more than abuse. |
| `trial_min_xp` | 0 (off) | Optional extra gate, e.g. 50 XP. |
| `trial_per_ip_day` / `trial_per_network_day` | 3 / 10 | Works only once the website reports real visitors (PROXY_SHARED_SECRET valid in Railway **and** Vercel). Until then it limits only app and extension callers. |
| `sync_enforce_limits` (L7) | **off** | Turn on only when you're ready for over-limit devices and non-testers on Ashburn to go dark at the next sync. Check Admin › Customers first. |
| `count_pc_once` (L6) | **off** | Policy choice: one Windows PC = one slot across servers (name-based, see the trade-off). Long term: installation ID in the app. |
| Website "also delete devices" (L8) | unticked | Keep opt-in. |

## Tests
- Backend: new `tests/test_vpn_hardening.py`, **30 tests**:
  - M1 ×6, including "monitor adds no per-address device limit";
  - M4 ×12, including the migration backfill; these run in `enforce` mode;
  - modes ×4: the default is monitor; monitor never refuses but records (a member enforce would refuse four ways gets
    a trial, with four rows recorded and claims written); monitor rows expire after 30 days; `off` records nothing,
    still writes claims, and `enforce` later honours them;
  - L6 ×2, L7 ×2, L8 ×1, L9 ×2, I4 ×1.
- Existing tests updated for the new behaviour:
  - refusals are no longer charged (`test_vpn.py::test_device_limit_and_offline_server`, `test_vpn_rate_limit.py::test_the_trial_does_not_drain_the_device_bucket`);
  - new settings and trial fields (`test_vpn.py::test_admin_routes`, `test_vpn_desktop.py`, `test_vpn_plans.py`);
  - `member_at` backdates `created_at` by 30 days.
- VPN group (`test_vpn*.py`, `test_audit_vpn_payments.py`, `test_client_ip.py`, `test_migrations.py`): **252 passed**
  (222 before the branch, plus 30 new).
- Full backend suite: **2613 passed, 5 failed** (10 min), see below.
- Frontend (`npm ci`, `npx vitest run`, `npx tsc -b`): **508 passed** (58 files), tsc clean. Four new tests:
  - the Admin guards card saves only what changed;
  - it shows the monitor counts and switches to enforce;
  - it is hidden on an old backend;
  - the website checkbox sends `remove_devices`.

## Deploy steps
1. **Back up the production database first** (the audit's O1: backups were failing; take and verify a manual copy).
2. **Rechain the migration.** On this branch 0113's `down_revision` is `0111_vpn_server_traffic_allowance`. On
   `release/kiwi-hardening` it is already `0112_vpn_server_checks`, so keep that when merging. There must be one head
   (`alembic heads`).
3. Deploy the backend (Railway). Migration 0113 runs at startup:
   - adds `vpn_trial_claims` and backfills it;
   - adds `vpn_trial_signals`;
   - adds the settings columns, with the defaults above (`trial_guard_mode` = `monitor`);
   - adds `vpn_devices.created_via`;
   - binds the existing reviewer.
4. Deploy the frontend (Vercel) for the Admin card and the website checkbox. The backend works without it.
5. Check Admin › Kiwi VPN › "Free trial guards" shows **Monitor only** and the defaults. A 6-hour-old test Passport
   still gets its trial, and an `account_too_new` count appears in the card.
6. Leave `sync_enforce_limits` and `count_pc_once` off unless the owner decides otherwise.

## Rollback
Old code crashes at startup on an unknown alembic revision (`0113_vpn_trial_guards` in `alembic_version`), so rolling
back means **old code plus this migration file**. Two options:
- **Keep the schema.** Deploy the previous code with `backend/migrations/versions/0113_vpn_trial_guards.py` added. The
  extra columns and table are ignored by the old models. No data is lost, and redeploying this branch later is instant.
- **Remove the schema.** With this branch's code still deployed, run `alembic downgrade 0111_vpn_server_traffic_allowance`.
  This drops the claims and signals tables, the settings columns and `created_via`, and the trial claims are lost. Then deploy the
  old code.

Restoring the backup also works, but loses everything written since.

## Full-suite result
`pytest tests` on this branch, 2026-11-14 (with the guard modes): **2613 passed, 5 failed**. None of the failures involve Kiwi VPN:
- `test_community`, `test_lunch_bot`, `test_trading_execution` and `test_trends` are on the known-unrelated list.
- `test_tg_trading::test_positions_orders_balance_and_settings` also fails when run on its own, because `/pnl` makes a
  live network lookup (`getaddrinfo failed`) on this PC. This branch's only change to `tradingos/bot.py` is in `/vpn add`.
