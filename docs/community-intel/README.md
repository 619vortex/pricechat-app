# Price Chat Community Intelligence

Owner-approved 2026-11-28, with all the recommended decisions. Built behind `COMMUNITY_INTEL_MODE` (`off` by default) on top of
Kiwi Passport 2.0, which supplies the community score and the badges. Nothing ships before the growth experiment ends on 4 December
2026, and only with the owner's go-ahead.

## What members do

- **Telegram (fastest):** paste a link or forward a channel post to @CryptoPriceChatBot in a private chat. No command needed. The bot
  answers with a receipt (CI-0142) and what the checks found, then asks one required question with two buttons: do you hold a
  position? Another row sets "Spotted by" credit and the tip's type. `/tip` explains this. In the group, `/tip` gets a private-chat
  link that deletes itself after a minute.
- **Passport › Intelligence:** a form with a link, a note, the type, the required position answer and credit, plus checks that run as
  you type. Below it: each tip and its progress, answering the desk's questions, withdrawing, appealing, the member's track record,
  and the weekly, monthly and all-time Researcher boards.

## What happens automatically (never a decision)

| Check | How | Reuses |
|---|---|---|
| Publisher | Classified from the link's address (official, outlet, market data, social, unknown, shortener). **The server never fetches a member's link.** | domain lists in `community_intel/rules.py` |
| Independent confirmation | Another reliable outlet or official source in the Terminal reporting the same event. The member's own page, or the same publisher, never counts. | Trending signal store, `newsworthy.same_event`, `terminal.intel.outlet_of / official / DISPUTE` |
| Figures (anomalies) | Funding and the 24 h move against Hyperliquid's live data | Hyperliquid `metaAndAssetCtxs` |
| Duplicates | The same page (tracking removed) or the same forwarded post within 30 days: the first sender keeps the credit. Near-identical words within 7 days. The Terminal already had the story (and whether it had it before the tip). | `newsdesk.checks.similarity`, Terminal topics, News Desk story memory |
| Safety | Pump wording, more than 2 cashtags, a new Passport, an unknown publisher; private keys and recovery phrases refused | `tgtrade.looks_secret` |

## What the editor decides (Terminal › Community)

Accept as a lead, merge into a story (optionally "adds something new"), ask the member a question, or reject with a reason. Every
decision and appeal is logged in `ci_events`, and the member is told in the Passport and Telegram. Accepting **publishes nothing**: the
tip joins the Terminal story about the same event once the desk's own sources report it, and goes out only when the owner publishes
that story through the normal gates. A published Telegram post then carries "🔎 Spotted by …", but only for a verified tip whose
member agreed to be credited.

## Reputation (never XP, money or rewards)

| | Community score |
|---|---|
| Sending a tip | 0 |
| Accepted **and** independently confirmed | +15 (research with data an editor checked: +25) |
| A story built on it was published | +40 |
| Rejected as promotional or misleading (unless an appeal is upheld) | −50 |

Rules that stop farming:
- At most 3 scoring tips per day.
- One scoring tip per member per story or page.
- A near-copy of your own earlier tip never scores.
- Merges score only when they add something new.
- The first sender keeps the credit.
- New Passports (under 3 days old) can't send tips.
- Daily limits by community level: Newcomer 2, Participant 3, Regular and above 5. One tip a minute.
- Three upheld spam rejections in 90 days pause tips until an appeal is upheld.
- Points are recomputed from the decisions every time, so nothing is stored that could be gamed.

Appeals: once per tip, within 14 days, for a rejection or a duplicate verdict. Upheld: the tip goes back to the queue and any penalty
disappears.

Researcher badges (Passport 2.0 collection, family "researcher"): First Lead, Sharp Eye (10), First Spotted, Anomaly Hunter (3 confirmed
by live data), Primary Source (5 official), Analyst (3 research), Fact Checker (editor-marked), and a numbered Researcher of the Month
(1st of each month, top of the monthly board with at least 40 points).

## Tests

- `backend/tests/test_community_intel.py`: 16 tests.
- `frontend/src/website/intel.test.tsx`: 5 tests.
- End to end on a copy of the production database (27 November): a social-post tip was confirmed by Al Jazeera and Bloomberg items in
  the Terminal, and an anomaly matched live Hyperliquid data. Duplicates, pump flags, decisions and an appeal all worked. Points
  were 40 and −50, the Researcher badge was awarded, and nobody's XP or rank changed. No post was written.

## Deploy (after 4 December, with the owner's go-ahead; Passport 2.0 first)

1. Deploy the branch (migrations 0126 and 0127 are additive).
2. Railway: set `COMMUNITY_INTEL_MODE=preview` and `COMMUNITY_INTEL_PREVIEW_MEMBERS=Kiwilegit`, and append `community_intel.` to
   `SCHEDULER_ONLY_JOBS`.
3. The owner tries a tip in Telegram and on the Passport, and decides it in the Terminal. Then `live`.

Rollback: `COMMUNITY_INTEL_MODE=off` (instant: the bot, the site and the Terminal behave as before), or deploy
`owner/rollback/pre-community-intel` (production code plus migrations 0126 and 0127 only).
