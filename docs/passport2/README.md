# Kiwi Passport 2.0

Owner-approved 2026-11-28. Built behind `PASSPORT2_MODE` (`off` by default). Nothing ships before the 7-day growth experiment ends
on 4 December 2026.

## What it is

| Piece | Where | Notes |
|---|---|---|
| Public Passport `/u/<name>` | `app/passport/service.public_v2`, `frontend/src/website/identity.tsx` `PassportCard` | Opt-in. A one-time invitation with a live preview (`profile_invited_at`). Per-section visibility. Linked accounts show as a tick only. |
| Identity editor | Passport › Profile tab (`ProfileTab`) | Title from an earned badge, 80-character bio (no links, @handles or staff wording), frame colour (existing rank-3 gate), up to 3 featured badges, sections, optional invite link. |
| Collectible badges | `app/passport/rules.py` | Families and bronze/silver/gold/mythic tiers. Rarity is the real share of eligible members. Existing badges, Predictions' badges and every holder are kept; only family/tier/title are filled in where empty. |
| Founding Kiwi | `service.award_founding` | Numbered in join order for the first 100 eligible members (active, not a review seat) or until 2027-01-01 00:00 UTC, whichever comes first. A number is never reused or moved (unique index). |
| Community score | `service.community_score` | See below. Computed on read; stores nothing. |
| Share cards | `app/passport/cards.py` | Link 1200×630 (also the X/Telegram link preview of `/u/<name>`) and Story 1080×1920. Same renderer as the Predictions cards. |
| `/passport` in Telegram | `app/passport/telegram.py` | Private chat: the card and share buttons. Group: the card goes privately; the group sees one line that deletes itself after a minute. |
| Job `passport.badges` | `app/jobs.py` | Every 15 minutes: collection, Founding numbers, auto badges for members active in the last 3 days (everyone once a day). |

## Community reputation score

Genuine participation over the last 90 days, from the existing ledger and Predictions:

| Counts | Points | Daily cap |
|---|---|---|
| A day taking part in the Telegram (`telegram.first_participation`) | 10 | 1 |
| Real conversation (`telegram.discussion`) | 1 | 5 |
| Helpful replies (`telegram.helpful_reply`) | 8 | 3 |
| Community missions (`mission.complete`) | 4 | 3 |
| A friend who joined and stayed (`referral.telegram`) | 25 | none |
| A day making a Predictions call (taking part, not being right) | 3 | 1 |
| A moderation flag a person upheld | −50 | |

Never counted: packs (opened or bought), purchases, deposits, trading, sign-ins, linking accounts, views. Levels: Newcomer 0,
Participant 50, Regular 200, Pillar 500, Legend 1000.

The score is separate from XP. It changes no XP balance, rank or reward, and nothing with money value reads it. Any use of it
for rank or rewards needs the owner's approval.

## Guarantees (tests: `backend/tests/test_passport2.py`, `frontend/src/website/identity.test.tsx`)

- `off`: public pages, badges and the bot behave exactly as before; no new badge row is created; the job isn't registered.
- `preview`: only `PASSPORT2_PREVIEW_MEMBERS` see the editor, get the new public layout and earn new badges (also through the ledger).
- No XP total, rank score, rank level or bonus XP changes anywhere in this feature.

## Deploy (after 4 December, with the owner's go-ahead)

1. Backup check; deploy the branch (migration 0126 is additive).
2. Railway: `PASSPORT2_MODE=preview`, `PASSPORT2_PREVIEW_MEMBERS=Kiwilegit`, append `passport.` to `SCHEDULER_ONLY_JOBS`.
3. Vercel: deploy the frontend.
4. Owner reviews the Profile tab, `/u/Kiwilegit`, the link preview and `/passport`; then `PASSPORT2_MODE=live`.
   The first live run numbers Founding Kiwis in join order.

Rollback: `PASSPORT2_MODE=off` (instant), or deploy `owner/rollback/pre-passport2` (production code plus migration 0126 only;
checked on a migrated copy: start-up, health, unchanged XP and ranks, old public page).
