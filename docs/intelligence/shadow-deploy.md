# Intelligence Engine: shadow-mode deployment runbook (prepared, NOT executed)

**Status: waiting for owner approval.** Nothing in this file has been run against production.
- Draft PR: #18 (`owner/feat/intel-p0-integrated` into `release/kiwi-hardening`).
- Production today: Railway deployment f4642e55, code 058e148, database revision `0120_trending_text_ok`.

## What shadow mode does, and what it cannot do
- **Both lanes record** the shared semantic gate's verdict next to their own decision:
  - News Desk: `_newsworthy` in `app/newsdesk/events.py`;
  - Trending: `suggest` in `app/trending/engine.py`.
  - The verdicts go to the `intel_shadow` table.
- **The return value is never used.** A test checks that it cannot hold, reject or publish anything.
- **Only the PC analyses shadow stories.** They are queued as `bulk` jobs:
  - The PC worker (qwen3.8:27b) answers them.
  - With the PC offline, they wait up to 6 h and then fall back to the rules.
  - **Bulk jobs never call Claude**, so shadow mode spends $0 of API money, even if a key is set.
  - Paid calls happen only through `router.run` with slot or breaking priority. No lane calls that in this branch.
- **The Admin dashboard (Intelligence, shadow)** shows decisions, disagreements, latency, PC availability, possible missed stories and API spend.
- **No AI-controlled publishing.** No publish path reads `intel_shadow` or `intel_jobs`.

## Switches (Railway variables on the backend service)
| Variable | Shadow value | Effect |
|---|---|---|
| `INTEL_SHADOW_ENABLED` | `true` | Lanes write shadow rows; the `intel.shadow` refresh job runs |
| `INTEL_ENABLED` | `true` | Shadow rows are queued for the PC. Registers the `intel.tick` job (lease expiry and fallback) |
| `INTEL_WORKER_TOKEN_SHA256` | hash from `python -m app.intel.tokens` | Lets the PC worker in. Railway stores only the hash |
| `INTEL_DAILY_USD_CAP` | leave unset (default 3.0) | Hard cap. Shadow mode spends nothing anyway |
| `ANTHROPIC_API_KEY` | not needed for shadow | Without it, any non-bulk job falls back to the rules |

Setting `INTEL_SHADOW_ENABLED=true` alone (with `INTEL_ENABLED=false`) records rule-only shadow rows and no model analysis. That is a valid first step.

## Deploy steps (once the owner approves)
1. **Back up the database.** Run the normal pre-deploy backup and note its key. Migrations 0121 and 0122 only add tables.
2. **Merge** PR #18 into `release/kiwi-hardening`, or deploy the branch head. Then `railway up` from the backend directory of a linked worktree, as for earlier deploys.
3. **Migrations run at startup** (`app/db/session.init_db` calls `upgrade_database`), moving the database from 0120 to 0122.
4. **Check the deploy:**
   - `/api/health/live` responds;
   - Admin → Intelligence shows "intel disabled" with zero rows;
   - the News Desk and Trending behave as before.
5. **Make the PC token on the PC:** `python -m app.intel.tokens`.
   - Put `INTEL_WORKER_TOKEN` in `worker/worker.env`.
   - Set `INTEL_WORKER_TOKEN_SHA256` on Railway.
6. **Start the PC worker** (`docs/intelligence/pc-worker.md`). Its default model is now `qwen3.8:27b`.
7. **Set `INTEL_SHADOW_ENABLED=true` and `INTEL_ENABLED=true`.** Railway redeploys.
8. **Check within one hour:**
   - the dashboard shows the PC online;
   - shadow rows from both lanes appear;
   - analyses land, normally within seconds while the PC is up;
   - spend stays at $0.00;
   - the post counts per lane are unchanged from the day before.

## Rollback

### Level 1: turn shadow off (no code change)
1. Set `INTEL_ENABLED=false` and `INTEL_SHADOW_ENABLED=false`.
2. The lanes stop writing at once, and no intel job is registered.
3. The tables stay and are harmless.

### Level 2: roll the code back (use the rollback build, not bare 058e148)

> **Verified locally 2026-11-25:** migrations run at startup, so **bare 058e148 cannot start on a database at `0122_intel_shadow_gate`**.
> It fails with `CommandError: Can't locate revision identified by '0122_intel_shadow_gate'`, and the deploy would not become healthy.
> Rolling back to Railway deployment f4642e55 would hit the same problem the next time it restarts.

**Preferred: deploy branch `owner/rollback/pre-intel`.**
- It is 058e148 plus only the intel migration files and the inert table models. There are no application code changes.
- It starts on a database at 0122. The four intel tables are left unused.
- No `railway ssh` and no database downgrade are needed.
- Checked locally: it started on a 0122 database and on a downgraded 0120 database (which it upgrades back to 0122).
  `test_migrations.py` and `test_vpn_hardening.py` pass on it (38 passed).

**Alternative: the downgrade route.** This needs the owner's `railway ssh` key.
1. While the new code is still running, run:

       railway ssh -- sh -c 'cd <backend dir> && python -m alembic -c alembic.ini downgrade 0120_trending_text_ok'

   This drops only `intel_shadow`, `intel_jobs`, `intel_workers` and `ai_usage`.
2. Then deploy bare 058e148. Locally, 058e148 started cleanly after this downgrade.

`tests/test_intel_migrations_rollback.py` covers 0120 → head → 0120 → head, keeping existing rows and leaving no stray tables.

**Confirm afterwards** that `/api/health/live` responds and Admin opens.

### Level 3: restore the backup from step 1
Only if the database itself is damaged; that isn't expected, since the migrations only add tables.

## Not part of this deployment
- Any change to what is published, held or rejected.
- Claude benchmarking: it waits for an API key.
- Wiring the Gemma second opinion into the worker. The tested policy function exists; it runs in shadow only after a separate approval.
- KiwiVPN: no changes.
