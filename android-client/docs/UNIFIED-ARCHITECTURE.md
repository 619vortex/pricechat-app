# Kiwi VPN across Windows, Android and the website

Written from the code on 2026-11-05. **Nothing here is deployed.** "Implemented" means written on `feat/kiwi-unified` and tested on a throwaway database.

```
  Windows app 0.1.1 (live)   Android app (new)      Website (live)
        | kd. session             | kd. session          | browser session
        | /members/vpn/*          | /members/vpn/*       | /members/* (all)
        |                         | /members/vpn/v1/*    | /members/vpn/v1/* (once deployed)
        +-------------------------+----------------------+
                                  |
                 Price Chat backend (FastAPI on Railway, one SQLite)
     members | reputation_events (XP ledger) | referrals | vpn_accounts | vpn_devices | vpn_servers
                                  |
                  VPN servers (node agents): Germany, Helsinki
```

## What already works: most of what the brief asks for
| Area | Where | State |
|---|---|---|
| One identity | `members`, `member_sessions`; sign-in by email, Google, X, Telegram | Live, already shared |
| App sessions | `kd.` tokens, limited by `desktop_scope.py` | Live |
| One trial per account | `vpn_accounts.trial_started_at`, one conditional write | Live, already shared |
| Entitlement | `service.access()`, on the server | Live, already shared |
| Device limits | `service._checked_server` | Live, already shared |
| Servers | `/members/vpn` | Live: a new server reaches every client |
| XP ledger | `reputation_events` | Live. **`UNIQUE(member_id, event_type, source_ref)` already stops double payment** |
| Levels | `xp.rank_for`, `rank_score` | Live |
| Achievements | `badges` | Live |
| Referrals | `referrals.py`: one per referred member, self-referral rejected, daily limit, repeat-browser flag | Live |
| Leaderboard | `leaderboard.py`, with opt-out | Live |

**There are no duplicate systems to merge.** The Android app and the new routes use these same tables.

## What was missing
1. **An app session couldn't read Passport data.** `/members/me/referrals`, `/members/leaderboard` and `/members/me/xp` answer 403 to a `kd.` token, and that limit protects accounts if a device's token is stolen. Widening it was avoided: the new routes sit under the prefix a `kd.` token already reaches, and they return only what a VPN client shows. `/members/me` is reachable but heavy, and returns more than a client needs.
2. **No capability switches.**
3. **No VPN missions.**
4. **No Gaming Mode data.**

## API inventory
| Route | Status | Used by |
|---|---|---|
| `GET /members/vpn` | Existing, unchanged | All |
| `POST /members/vpn/trial` | Existing, unchanged | All |
| `POST`, `DELETE /members/vpn/devices`, `GET …/peer` | Existing, unchanged | All |
| `GET /members/vpn/ip` | Existing, unchanged | Apps |
| `POST /members/desktop/code`, `token`, `refresh` | Existing, unchanged | Apps, website page |
| `POST /members/logout`, `GET /members/me` | Existing, unchanged | All |
| `GET /members/me/referrals`, `/members/leaderboard` | Existing, unchanged, browser only | Website |
| **`GET /members/vpn/v1/state?platform=&version=`** | **New.** Signed-in. Reads, plus the referral code's lazy creation, as the existing route does | Android; Windows and website later |
| **`GET /members/vpn/v1/capabilities?platform=&version=`** | **New.** Public. Says only which features exist | All |

`v1/state`'s `vpn` block is the output of the same function as `GET /members/vpn`, and a test holds them equal. `passport` comes from the same functions as the website.

**No migration, no new table, and no new XP rule.** The only writes are ones existing routes already make: a settings row, a VPN account row, and a referral code on first use.

## Shared data model
```
state {
  api_version: 1, refresh_after_seconds: 60,
  capabilities: { passport_enabled, referrals_enabled, auto_call_enabled, gaming_mode_enabled, smart_routing_enabled, vpn_missions_enabled },
  vpn: <GET /members/vpn>,
  passport: { username, display_name, xp, bonus_xp, xp_suspended,
              rank: { current, next, score, xp_to_next, progress },
              badges: [...], standing: { position, board } | null,
              referral: { code, link, joined, qualified, xp_earned, reward_xp } | null } | null,
  missions: []
}
```

## Capabilities
| Feature | Android | Windows | Website |
|---|---|---|---|
| `passport_enabled` | 0.1.0+ | 0.2.0+ (0.1.1 has no screen for it) | Yes |
| `referrals_enabled` | 0.1.0+, if the program is on | 0.2.0+, if on | Yes, if on |
| `auto_call_enabled` | 0.1.0+ | Never | Never |
| `gaming_mode_enabled`, `smart_routing_enabled`, `vpn_missions_enabled` | Off | Off | Off |

An unknown platform or unreadable version gets nothing. **A capability never grants access:** a test sets every feature on for an account with no plan, and creating a device is still refused. The table is code, so changing it needs a backend deploy. A database or Admin switch would need a migration, so it wasn't added for the foundations.

## Synchronization
Polling, with no new infrastructure. Android refreshes when it opens, whenever it comes to the front, and every 60 s while on screen. `refresh_after_seconds` lets the server slow clients down later. Because every client reads the one ledger, there are no caches to invalidate. The rate limit is `earn`: 30, then 1 per second per visitor.

## Missions: defined, off, and unable to pay
`unified.PROPOSED_MISSIONS` holds your five proposals. They have no XP rule and no claim route, and clients aren't sent them. A test enforces all three.

**Decisions needed before Phase 3:**
1. **Three of the five can't be verified by the server:** onboarding, trying Gaming Mode, and the security check. Only the client could report them, and you ruled out trusting clients. I recommend dropping them or finding server evidence.
2. **First connection can be verified:** `vpn_devices.last_handshake`, written by the server's own sync, is real evidence. `source_ref "vpn:first_connection"` makes it one payment per member, and deleting the device can't earn it again.
3. **Referral +500 could be verified,** but needs care: it's farmable through throwaway accounts unless payment or something costly qualifies, and with payments off nothing does. The existing rule requires linking X or Telegram.
4. **The sizes matter.** `DAILY_XP_CAP` is 60 a day across automatic sources, and these would be up to 500. A new `vpn` source sits outside that cap by construction, like `earn`. Is that what you want? +100 is also more than joining (+20). You may want to see the rank thresholds before approving them.

Reconnect farming is prevented by making each mission one-time. Nothing rewards traffic or time connected.

## Gaming Mode: data model only
No routes, no tables, off everywhere. When it's built:
```
game_profile { key, name, ports?: [...], notes }
route { server_id, kind }                    // from vpn_servers
measurement { server_id, region, latency_ms, jitter_ms, loss_pct, samples, measured_at, method }
quality_history = measurement[], kept briefly, never tied to what was played
recommendation { game_key, server_id, reason, based_on: measurement ids }
```
Rules: no number is shown without a real measurement behind it. A measurement says how it was taken. No claim that a VPN lowers latency unless before-and-after measurements show it on that route, and a VPN usually adds a hop. It needs a real measuring method first, because the backend has none.

## Design
See `DESIGN-SPEC.md`.

## Phases
| Phase | State |
|---|---|
| 1. Shared account, subscription, server and Passport APIs | **Implemented on the branch, tested, not deployed** |
| 2. Passport in Android | **Written, not compiled.** Hidden until the route is live |
| 2. Passport in Windows | **Planned.** Needs a client change, a VM build and a new installer, none of which were started. `windows-client/client/src/api.rs` needs one call, and `App.tsx` one panel |
| 2. Passport on the website | Already there: it's the source |
| 3. Missions | **Definitions only.** Needs the decisions above |
| 4. Gaming Mode | **Data model only** |
| 5. Auto-Call VPN | **Written for Android, not compiled, not tested on a phone** |

## Deploying Phase 1, when you approve it
1. Merge to `main` in a clean worktree. The main checkout has another session's uncommitted files.
2. Run the full backend suite on the merged commit.
3. Take a Railway volume backup anyway, though no migration runs. The nightly backup's reliability isn't established.
4. `npx @railway/cli up <clean-worktree>/backend --path-as-root --service api --ci`, passing the path as an argument.
5. Check `GET /api/health/live`, that `GET /api/members/vpn` still answers for a signed-in session, and `GET /api/members/vpn/v1/capabilities?platform=windows&version=0.1.1`.
6. Confirm a Windows 0.1.1 connection still works.

**Rollback:** no migration, so redeploying the previous code is safe here, unlike the warning for 0102. Nothing depends on the new routes except an Android app that treats a 404 as "not available".

The website changes (phone wording, disabled Android card) deploy separately with Vercel and can be rolled back with `vercel promote`.
