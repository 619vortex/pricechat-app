# Feature ideas, simplifications and opportunities

*Independent proposals, ranked by expected impact divided by effort. "Evidence" points to the audit finding that motivates each idea. Impact numbers are judgement, not measurements.*

## A. Simplify (do these before adding anything)

| # | Idea | Why (evidence) | Effort |
|---|---|---|---|
| S1 | **Cut the bot to about 8 commands:** /price (with more), /chart (fc buttons), /signal, /market, /trade (open/enter/close in one guided flow), /positions, /alerts, /help. Fold /committee, /debate and /research into /signal's "Ask the committee" button. | About 30 overlapping commands. /help doubles as a trade check, and /risk means two things (TG notes). | M |
| S2 | **One brand system.** Green Archivo across main, VPN, Earn and market. Retire the purple Earn and light-theme market styles. | Four visual systems (UX-10). | M |
| S3 | **Hide or retire non-core products:** Lunch Bot (public), Kalshi, Realm, Content Studio video, Workers/Invite bot, the Paper Exchange writer loop. | No revenue path; extra jobs on one serial scheduler (AR-3, AR-18, UX-14). | S each |
| S4 | **One audit table** instead of nine. | AR-11. | M |
| S5 | **A device = a physical device** for VPN; changing servers updates it in place. | VPN-2 locks phones out. | M |

## B. New features ranked by impact

| Rank | Feature | What it is | Why it matters | Effort |
|---|---|---|---|---|
| 1 | **"Trade this" on every chart and signal** | A button under each FC chart and signal card opens a private, pre-filled `/open` preview (coin, side, suggested stop from the signal or ATR). Testnet first. | Turns the bot's most-used output into referral volume, the only live revenue line (BUSINESS-GROWTH §2). The engine exists (feat/tg-trading). | S-M after the trading fixes |
| 2 | **Verified track record page** | Public, timestamped, append-only record of every published signal (entry, stop, target, outcome), with a hash chain or a pinned Telegram post per signal. | Trust; the precondition for any paid tier. Today there's no public record (UX-14). | M |
| 3 | **Morning brief in DM (opt-in)** | 07:00 local: your positions and P&L, 3 biggest market moves, the one news event that mattered, your streak. | A daily reason to open the bot. Uses data the system already has. | M |
| 4 | **Price and level alerts that follow the trader** | "Alert me when BTC loses 60k" from any chart, liquidation-level alerts for open positions, funding flips. | Daily-return driver. The alerts module exists; make it one tap from charts. | S-M |
| 5 | **Pokémon market SEO + price alerts + affiliate links** | Serve per-page meta to all crawlers; "notify me when this card drops below $X" (email or Telegram); outbound affiliate links. | Probably the largest organic channel (UX-1, UX-2, UX-4); first revenue for the market. | S (SEO) / M (alerts) |
| 6 | **Copy the Kiwi's trades, sized to you** | Members opt in to mirror published signals at their own size (AUTO already exists) with a public results board. | Referral volume plus a retention loop. Needs the track record first (#2). | M |
| 7 | **News Desk "why it matters" line + market reaction** | Each post shows the measured price reaction 15/60 minutes later as an edit or reply ("BTC -1.8% since"). | Originality and credibility; uses the existing moves engine. Ties news to trading. | S-M |
| 8 | **Weekly community leaderboard by verified P&L** | Ranks by testnet/live P&L of bot trades (opt-in), not by clicks. | Replaces farmable XP as the status signal (PP-2 to PP-8). | M |
| 9 | **Annual VPN plan + family devices** | $59/yr-style plan, 5 devices, server change in place. | Category norm; fixes device frustration (VPN-2). Pairs with turning payments on (VPN-1). | S-M |
| 10 | **Status page + public uptime** | status.pricechat.live: bot, News Desk, VPN servers, last post time. | Trust; would have made the News Desk silence visible (ND-2). | S |
| 11 | **Owner cockpit in Telegram** | `/ops` for the owner: failing jobs, last post, backup age, VPN server health, referral volume today. | The owner is the incident responder and alerts go nowhere today (AR-2). | S |
| 12 | **Wallet-verified "Banter" role sync** | Auto-grant the group role and a trading badge when Hyperliquid confirms the BANTER referral. | Makes the referral visible and social; drives sign-ups through the code. | S |

## C. Opportunities not yet considered

- **Hyperliquid builder codes.** Hyperliquid lets front-ends attach a builder fee to orders placed through them. For bot-placed orders that could earn per trade on top of the referral. **Check the terms and get the owner's approval** before using it, and disclose the fee in the preview.
- **Telegram Mini App** for the trading preview and positions screen: a richer UI than inline buttons, still inside Telegram, with the same backend.
- **Partnerships:** Pokémon/TCG creators (affiliate market links), crypto newsletter swaps for the channel, VPN bundling with crypto wallets (privacy angle).
- **Content from data you own:** a weekly "what the bot saw" thread (biggest moves, liquidations, signal outcomes), generated from your own feeds. Original content, not rewritten headlines.

## D. Things not to build (yet)

- iOS VPN before signing, payments and the device model are fixed.
- More AI personas or committee modes: no evidence of demand, and they add LLM cost risk.
- A paid signals tier before a public verified record exists.
- More XP sources before the existing exploits are closed.
