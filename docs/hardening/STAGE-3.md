# Stage 3: backend + frontend release (prepared, NOT deployed)

Branch `release/kiwi-hardening` (worktree `D:/pc-wt/kiwi-hardening`). Nothing here is deployed or pushed.

## Why the release merges feat/trending-topics
Production Railway runs **feat/trending-topics `cf713cb`** (deploy 569ccbf5), not main:
- `1374981` News Desk claim gate (already in this release);
- feat/newsdesk-wire `e896ffb`, `cc29781`: wire style opt-in, same-event grouping, one cashtag on X, post-style switch;
- Trending Topics `dd910d1`, with migration **`0114_trending_topics`** (down_revision 0111). The production database is at 0114.

A release without these would roll production back (News Desk wire style and Trending Topics gone, and the backend would not start:
the database is at 0114, which an older migration set does not know). So `cf713cb` is merged into this branch.

**Re-merge right before deploying.** Another helper is still adding commits to feat/trending-topics. Before step 3b:
`git merge feat/trending-topics` (or whatever production runs at that moment; check the Railway deployment's commit), rerun the
backend tests and the migration check below. If that branch adds a migration, it must stay before the Kiwi ones (rechain
0115's `down_revision` onto it) only if production already ran it; otherwise rechain it after 0116.

## Migration chain
`… 0110 → 0111_vpn_server_traffic_allowance → 0114_trending_topics → 0115_vpn_server_checks → 0116_vpn_trial_guards` (one head).

- The Kiwi migrations were renumbered: `0112_vpn_server_checks` → **`0115_vpn_server_checks`** (down_revision `0114_trending_topics`),
  `0113_vpn_trial_guards` → **`0116_vpn_trial_guards`** (down_revision 0115). Schema and data steps are unchanged. No database ever
  ran 0112/0113 (production is at 0114; the laptop databases are at 0060/0010), so the rename is safe.
- `0114_trending_topics` is untouched. Its docstring and docs/trending/TRENDING.md say to change 0114's down_revision after this
  branch: **do not**. Production already ran 0114; rechaining it would make alembic skip the Kiwi migrations or fail.
  `tests/test_vpn_hardening.py::test_kiwi_migrations_follow_productions_trending_revision` fails if anyone does.

## Migration check (temporary SQLite, 2026-11-14)
| Check | Result |
|---|---|
| (a) empty database → head | ok, at `0116_vpn_trial_guards`; Kiwi and Trending tables present |
| (b) production state: upgrade to 0114, then head | runs only `0115_vpn_server_checks`, `0116_vpn_trial_guards`. New tables `vpn_server_checks`, `vpn_trial_claims`, `vpn_trial_signals`; new `vpn_settings` columns (9) and `vpn_devices.created_via` |
| (c) downgrade head → 0114 | ok; schema equals the 0114 schema, Trending tables and their rows intact |
| (d) upgrade again | ok, back at 0116 |

## Old production code against the new database (rollback)
- New columns on existing tables: `vpn_settings` gets 8 NOT NULL columns, **all with server defaults**, plus nullable
  `reviewer_member_id`; `vpn_devices.created_via` is nullable. Nothing is renamed or dropped. Old models select named columns, so
  inserts and selects keep working (checked: `cf713cb` code inserted a `VpnSettings` row and selected every VPN table on a head database).
- **But old code does not start as-is**: at startup `upgrade_database()` fails with `Can't locate revision identified by
  '0116_vpn_trial_guards'` (checked with `cf713cb`). So:
  - **Rollback = old code plus the two migration files**: check out production's commit at deploy time (now `cf713cb`), add
    `backend/migrations/versions/0115_vpn_server_checks.py` and `0116_vpn_trial_guards.py` from this branch, `railway up` from that
    worktree. Checked: `cf713cb` + the two files starts its migration step as a no-op and works on the head database.
  - **Never Railway "Redeploy"** of the previous deployment: it has no 0115/0116 and crashes on start.
  - Or remove the schema first: with this release still running, `alembic downgrade 0114_trending_topics` (drops the server-check
    history, trial claims and signals, the new settings columns, `created_via`), then deploy the old code unchanged.

## Deploy (owner's go; see README.md "Safe deployment order")
1. 3a: manual Railway backup, restore-checked.
2. Re-merge feat/trending-topics (above), retest.
3. 3b: `railway up` from this worktree (not the main checkout). Start-up runs 0115 → 0116.
4. 3c: `/api/health` `checks.migrations` ok with code and database both at `0116_vpn_trial_guards`; `checks.trending` as before;
   News Desk and Trending Topics jobs still listed and running; then the Kiwi checks in README.md.
5. 3d: frontend (Vercel) from this branch; it also carries the Trending Topics tab and post-style switch production has now.
