# KiwiVPN hardening release: integration report

Date: 2026-11-14. Branch `release/kiwi-hardening` (worktree `D:/pc-wt/kiwi-hardening`), from main `a7d2081`.
Nothing here is deployed. Payments stay off (`payments_enabled` default and `CARD_SUBSCRIPTIONS = False` unchanged).

## What was integrated

| Order | Branch | Head | Area | Detail doc |
|---|---|---|---|---|
| 1 | feat/kiwi-hardening-monitoring | 0364e00 | Health monitoring (migration 0115, was 0112), backup freshness + weekly restore test, alert dedup, "alerts go nowhere", uptime workflow draft | MONITORING.md, MONITORING-OWNER-SETUP.md |
| 2 | feat/kiwi-hardening-trial | 69b89eb | M1 rate-limit order, M4 one trial per identity (migration 0116, was 0113), L6–L9, I4 | TRIAL-AND-LIMITS.md |
| 3 | feat/kiwi-hardening-claims | 55ec808 | Honest privacy/DNS text, draft AUP, credential audit | PRIVACY-TEXT.md, CREDENTIALS.md |
| 4 | feat/kiwi-hardening-nodes | c95fcb7 | feat/kiwi-infra drift merged, local no-log DNS, proxy limits, L1/L18–L20, floating-IP and DNS apply scripts | NODES.md |
| 5 | feat/kiwi-ext-105 | caf4567 | Extension 1.0.5 (L15–L17) | CHROME-EXTENSION.md |
| 6 | feat/kiwi-windows-22 | a81a586 | Windows 2.2: signed auto-update, kill switch L10–L14 + strict mode | WINDOWS-2.2.md |
| 7 | docs/kiwi-second-provider | c373450 | Second-provider plan | SECOND-PROVIDER.md |
| 8 | 1374981 (fix/newsdesk-cashtags) | 1374981 | The News Desk claim gate production already runs (Railway 5b48b707); main lacked it, so a release from main would have rolled it back | — |
| 9 | feat/trending-topics | cf713cb | What production runs now (Railway 569ccbf5): 1374981 + feat/newsdesk-wire (e896ffb, cc29781) + Trending Topics (dd910d1, migration 0114). **Re-merge right before deploying** | STAGE-3.md |

Integration fixes:
- Import-only conflicts in `backend/app/models/__init__.py` and `backend/app/vpn/service.py` (monitoring + trial), resolved as unions.
- Migration `0113_vpn_trial_guards` rechained: down_revision `0112_vpn_server_checks`. Single head verified (`ScriptDirectory.get_heads() == ['0113_vpn_trial_guards']`).
- After merging feat/trending-topics (production's DB is at `0114_trending_topics`): Kiwi migrations renumbered and rechained after it, chain
  `0111 → 0114_trending_topics → 0115_vpn_server_checks → 0116_vpn_trial_guards`, one head. 0114 itself is unchanged and must stay so (STAGE-3.md).
- Merge conflicts (cf713cb): `models/__init__.py` imports and `observability.py` health checks (owner_alerts + trending), both kept;
  `jobs.py` conflicted as a whole file because the release had turned it from CRLF to LF. It is CRLF again (as production), and the
  Trending jobs merged cleanly. `frontend/src/api/client.ts` had the same LF conversion and is CRLF again (no text change).
- `/vpn/privacy` aligned with extension 1.0.5: password in memory only, pricechat.live sees the real IP (`community.tsx`).
- Not done here on purpose: untracking `worker/worker.env`. It belongs to the worker-token rotation (CREDENTIALS.md R1), because the running worker reads that file.

## Tests on the integration branch

Latest full run (2026-11-14, after merging feat/trending-topics cf713cb and renumbering the migrations): backend **2850 passed, 6 failed**:
the same 5 as main plus `test_trending.py::test_the_scheduler_job_runs_the_tick_and_posts_through_a_fake_x`, which fails the same way on
untouched cf713cb (result `waiting` instead of `published`; a Trending Topics test issue, not this merge). The new migration test
(`test_kiwi_migrations_follow_productions_trending_revision`) passes. Frontend vitest 518 passed (59 files) + `tsc -b` clean,
vpn-node pytest 214, vpn-node/infra 22, extension lib + cws 27, single alembic head `0116_vpn_trial_guards`. Migration check: STAGE-3.md.

Previous run (head 40e9cdf, after merging trial monitor mode b91cc9d): backend **2666 passed, 5 failed** (the same 5 as main), frontend 513 passed + `tsc -b` clean, website re-run 356, extension 27, infra 22, single alembic head 0113 (now renumbered 0116). Earlier run below.


| Suite | Result |
|---|---|
| Backend full (`pytest tests`) | **2659 passed, 5 failed**. The same 5 fail on clean main a7d2081: test_community, test_lunch_bot, test_trading_execution, test_trends, test_tg_trading (/pnl needs a network lookup) |
| Backend News Desk after merging 1374981 | 514 passed |
| Frontend vitest | 512 passed (58 files); website re-run after the privacy edit 356 passed; `tsc -b` clean |
| vpn-node pytest | 214 passed |
| vpn-node/infra node tests | 22 passed |
| Extension lib + cws | 27 passed (the e2e, 48/48, ran on feat/kiwi-ext-105; vpn-extension/ is byte-identical here) |
| Windows | vitest 99 on the branch; Rust only in CI: KIWIVPN run 43574419119 on branch kiwi-22: build, upgrade, update-test 35/35, e2e 37+12 pass; kill-switch 41/41 checks pass but the job ended "cancelled" (no failed step); needs one clean re-run. windows-client/ is byte-identical to feat/kiwi-windows-22 |

## By workstream: blockers, production risks, dependencies, cost

| Workstream | Blockers (owner) | Production risks | Depends on | Extra cost / month |
|---|---|---|---|---|
| Monitoring | Set the alert chat and press Send test; GitHub secrets for the uptime workflow | Weekly restore test downloads the full DB into Railway temp disk (time/disk unproven on production size); admin `/api/health` reports `owner_alerts: fail` until the chat is set | Verified backup before migration 0115 | $0 (private-repo Actions: ~1,440 of 2,000 free min at 30-min cadence; hourly halves it) |
| Trial and limits | Decide when to switch `trial_guard_mode` from **monitor** (default: guards evaluated and counted in Admin, nobody refused) to enforce; defaults then: account age 24 h, 3 trials/IP and 10 per /24 per day, XP off, L7 and L6 off | In monitor mode none; enforce later can refuse CGNAT users (watch "networks seen more than once" in the Admin trial card first) | Migration 0116 after 0115 (after production's 0114); backup first | $0 |
| Privacy text + AUP | Approve the wording; a real abuse mailbox (placeholder in the page); P2P "not supported" vs blocked | None technical; legal review optional | Ships with the backend (heads.py) | $0 (optional legal review $500–2,000 one-off) |
| Credentials | Every rotation is the owner's (CREDENTIALS.md order) | The worker token is still tracked on main and valid; the Railway-breach list was never confirmed rotated | R1 before untracking worker.env | $0 |
| Nodes | Approve each apply at a terminal; ops email for certbot; whether to apply host-wide journald/ufw changes on Helsinki | If the local resolver fails, connected customers lose DNS; rule order and unbound are untested on a real node; rollback of DNS needs fresh configs for devices made after the switch | Monitoring and uptime live first; dry-run diff read before apply | $0 |
| Extension 1.0.5 | publisherId + CWS service account (not set up); `! submit` / `! go-live` | After a browser restart a new login is needed: fails closed if the Price Chat session (30 d) expired or the login rate limit is hit | Privacy page (this release) live before submission; M1 rate-limit fix deployed first | $0 |
| Windows 2.2 | Generate update keys offline, put public keys in PRODUCTION_KEYS, create `kiwi-publish` env, signed build | Placeholder keys = no updates; installer dying midway and strict-mode boot filters across a real reboot untested | Clean kill-switch CI re-run; website deploy hosts the manifest | $0 (existing signing certificate) |
| Second provider | Approve each purchase | EDIS throttles at 1 TB (set a 1,000 GB allowance); 2 GB price unconfirmed; maybe no cloud firewall (8443 then relies on untested `AGENT_ALLOW`) | Nodes branch (`install --host`, AGENT_ALLOW) | Deleting sin1+hil1 −$30.68; EDIS hel2/sgp1/lon1/ams1 +$26.64; FIP −$3.50; B2 $0 → net −$7.54 (today $74.95 → $67.41) |

## Urgency ranking

1. Owner alert chat set and tested (works on today's production: the test endpoint is already live).
2. Worker-token rotation, then the Railway-breach list (CREDENTIALS.md R1–R3).
3. Release deploy of this branch's backend + frontend: monitoring, honest privacy text, trial abuse and rate-limit fixes.
4. Delete idle sin1/hil1 (−$30.68/mo).
5. Helsinki DNS privacy (makes the privacy text's "server's own resolver" true everywhere), then Ashburn/Germany.
6. Uptime workflow enabled.
7. Extension 1.0.5 submission.
8. Windows 2.2 keys, signed build, publish.
9. EDIS Helsinki node; move customers off the personal server.
10. Remaining EDIS regions, B2 off-platform backup copy.

## Safe deployment order (each step needs the owner's go)

| Step | What | Who | Gate | Rollback |
|---|---|---|---|---|
| 0 | Set alert chat, Send test. Confirm today's backup landed (expected ~12:00 UTC) | Owner | — | — |
| 1 | Rotate the worker token (R1); then untrack `worker/worker.env` on a branch | Owner (Claude prepares the commit) | — | Old token until deleted |
| 2 | Delete sin1/hil1 (turn off delete protection first) | Owner | Approval | Not reversible (no data on them) |
| 3a | Manual Railway backup, restore-checked | Owner / Claude read-only check | — | — |
| 3b | Re-merge feat/trending-topics (whatever production runs then) and retest; then backend from this branch (`railway up` from the worktree, not the main checkout): production's 0114 → 0115 → 0116 | Owner | Wording approved, abuse mailbox filled, trial defaults decided | Production's code at deploy time (now feat/trending-topics `cf713cb`) **plus** the 0115/0116 migration files, `railway up`; never Railway "Redeploy" (crashes on the unknown revision); settings off restores old behaviour. STAGE-3.md |
| 3c | Check: migrations at 0116_vpn_trial_guards, News Desk + Trending Topics jobs still running, `/api/members/vpn/public` unchanged (payments false), Admin health + trial cards, a test alert, a trial refusal reason | Owner + Claude | — | — |
| 3d | Frontend (Vercel) from this branch | Owner | 3c passed | Promote the previous Vercel deployment |
| 4 | Enable the uptime workflow + its 2 secrets | Owner | 3 live | Disable the workflow |
| 5 | Helsinki: `apply-dns-privacy.sh` dry run → read the diff → apply; floating-IP script dry run must show no change; then Ashburn, Germany per NODES.md | Owner at terminal | Monitoring live; quiet period | `--rollback` (fresh configs for devices made after the switch) |
| 6 | Extension 1.0.5: publisherId, upload, `! submit 1.0.5`, `! go-live 1.0.5` | Owner | 3 live (privacy text, rate-limit fix) | Ship 1.0.6 (store has no rollback) |
| 7 | Windows 2.2: keys, clean CI re-run, signed build, publish installer + manifest | Owner | Kill-switch job green | Promote previous Vercel deployment (2.1 download) |
| 8 | EDIS hel2 testers-only → move Helsinki customers; then sgp1, lon1/ams1; B2 copy | Owner | Each purchase approved | Testers-only flag; delete node |
