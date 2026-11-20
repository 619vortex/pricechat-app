# Deploying the Kiwi mobile release (backend first, website second)

Prepared 2026-11-20. **Nothing here has been run. Deploy only after the owner's explicit approval.**

## What ships
- Branch `feat/kiwi-mobile` = production's `release/kiwi-hardening` `08a1385` (Railway `e57be040`, live since 2026-11-19) plus the
  mobile work only: the unified mobile API (`app/vpn/unified.py`), the store-review account for the Android and iPhone apps (review
  seats, scoped sessions), the public `/vpn/delete-account` page, and the mobile privacy-policy sections.
- One migration: `0119_vpn_review_seats` (after production's `0118_nd_editor`):
  - Adds `members.review_seat` (boolean, server default false). On SQLite that is an `ALTER TABLE ... ADD COLUMN`, which is instant.
  - Turns the review account bound so far (`vpn_settings.reviewer_member_id`) into the first seat and clears its email.
  - Deletes every session of that account except the scoped `kr.` browser session and the app's `kd.` session. **This revokes the
    older unrestricted browser sessions at deploy.** From then on `members.service.member_for_token` refuses and deletes any
    other session of a seat on its first use.
- No News Desk, Trending, payments or trading code changes. Payments stay off.

## 0. Before deploying
1. The owner's approval in this conversation.
2. **A confirmed backup** of the production database (the owner's rule for any migration): check that the latest `system.backup`
   succeeded, or take a manual Railway volume backup and check that it restores.
3. Note the rollback points: Railway `api` deployment `e57be040` (code `08a1385`). Get the current Vercel production URL from
   `npx vercel ls pricechat --prod` (run in `D:/pc-wt/kiwi-hardening/frontend`) and write it down.

## 1. Backend (Railway, service `api`)
Use the clean production worktree `D:/pc-wt/kiwi-hardening`, the usual route. **Never deploy from its root**: the root is linked to
the stray project "backend". Use the `backend` folder, which is linked to `pricechat`.

```
git -C D:/pc-wt/kiwi-hardening merge --ff-only feat/kiwi-mobile
npx @railway/cli up D:/pc-wt/kiwi-hardening/backend --path-as-root --project c2a48c3b-60de-4eec-80a3-1e7cf5ff3e26 --environment 7c06bad6-768e-4625-9a69-7251e6225a89 --service api --ci
```

Pass the path as an argument. Never use `cd ...; railway up`: on 2026-11-03 a failed `cd` deployed the frontend to `api`.

Check:
- The deploy logs show `Running upgrade 0118_nd_editor -> 0119_vpn_review_seats` and then `Database schema is up to date`.
- `/api/health`: `checks.migrations` is ok at `0119_vpn_review_seats`, and the other checks are as before.
- `GET https://pricechat.live/api/members/vpn/v1/state` with no session returns **401**. It was 404 before, because the route is new.
- `POST https://pricechat.live/api/members/auth/reviewer` with a wrong password returns **401**.
- The scheduler jobs still run: News Desk, Trending, `vpn.sync`. Look for their next lines in `railway logs`.

## 2. Website (Vercel, project `pricechat`)
```
npx vercel --prod --yes --cwd D:/pc-wt/kiwi-hardening/frontend
```

Check:
- `https://pricechat.live/vpn/delete-account` loads, titled "Delete your Kiwi VPN account · Price Chat", with the in-app steps and
  the deleted/kept lists. Signed out, it shows Sign in.
- `https://pricechat.live/vpn/privacy` says "Last updated 20 November 2026" and has "The Android app (beta)" and "Deleting your account".
- `https://pricechat.live/vpn/desktop?...` opened from a phone shows "App Store or Google Play reviewer? Use the review account".

## 3. Review account (owner, after both deploys)
Follow `docs/KIWI-STORE-REVIEW-ACCOUNT.md`, "Owner setup":
1. Run `reviewer_account.py` with a dedicated mailbox.
2. Redeploy `api` so the variables take effect.
3. Check Helsinki 1 is online.
4. Test it once on a phone, then delete that seat.

The extension's existing reviewer simply signs in again.

## Rollback
**Website:** `npx vercel rollback <previous production URL from step 0.3> --yes`. This is safe on its own: the old site works with the
new backend.

**Backend:** deploy the prepared rollback build. **Never use Railway's "Redeploy" on `e57be040`.** That build doesn't know migration
0119, so it stops at start-up with "Can't locate revision".

```
npx @railway/cli up D:/pc-wt/kiwi-mobile-rollback/backend --path-as-root --project c2a48c3b-60de-4eec-80a3-1e7cf5ff3e26 --environment 7c06bad6-768e-4625-9a69-7251e6225a89 --service api --ci
```

- `D:/pc-wt/kiwi-mobile-rollback` is branch `rollback/kiwi-mobile` (`74b2675`): production's `08a1385` plus the 0119 migration
  file, nothing else.
- Checked 2026-11-20: a database migrated by the new code is at `0119_vpn_review_seats`. The rollback code's start-up migration
  is then a no-op. Its models still create members and sessions (the new column takes its default), and it signs a session in.
- Leave the schema in place. Don't downgrade.
- What a rollback does not undo:
  - The review account's cleared email and its deleted unrestricted sessions stay that way, as intended.
  - The old code treats the **latest** seat as the one review account (`reviewer_member_id`) and issues unscoped sessions again,
    as production does today.
- Restoring the backup is only for data damage. Nothing in this release rewrites other members' data.

## Tests behind this (2026-11-20)
- Backend full suite: 3470 passed, 0 failed. Reviewer and migration tests are in `tests/test_vpn_reviewer_mobile.py`, which covers
  two reviewers at once, re-sign-in keeping its seat, the seat cap, unscoped sessions refused at once, and the 0119 migration on a
  production-shaped database.
- Website: `tsc -b` clean, vitest 565/565, `vite build` ok.
- Android and iPhone apps: no code change in this step. The signed `e4aaef1` APK and AAB stay current.
