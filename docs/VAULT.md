# Price Chat Vault: vintage Japanese Pokémon

`pricechat.live/pokemon/vault` is the **Price Chat Vault**: a curated storefront of real vintage Japanese Pokémon cards
from approved sellers, shown as the **◆ Vault** tab of the Pokémon Market (`/pokemon`, prices and market caps, unchanged).
The first and only seller is **Mayumi Card Shop** (eBay seller `mayumicardshop`).

## 1. What was retired from the public site

| Retired | How |
|---|---|
| Paper Exchange (spot, perps, leverage, order books, leaderboard) | route removed; `/api/members/exchange/*` answers **410**; jobs skip (`EXCHANGE_ENABLED=false`) |
| Play-USDT QuickTrade (paper buy/sell/bids) | route removed; the market home's "Trade Pokémon" button now opens the Vault |
| Peer-to-peer graded marketplace (simulated USDT settlement) | public routes removed; backend and data untouched; still in the admin dashboard |

Everything else is kept, including the 👛 Wallet page (now `/pokemon/wallet`). No table or row was deleted.

## 2. eBay: what the terms allow (researched 2026-10-11)

Sources: the eBay API License Agreement (developer.ebay.com/join/api-license-agreement), the Buy API requirements
(developer.ebay.com/api-docs/buy/buy-requirements.html), the Browse API reference.

- **Display.** eBay content can be shown to our users through our app keys. Item listing data shown must be **no more
  than 6 hours older** than eBay's (other eBay content: 24 h); if older, the app must say how much older.
  → Vault syncs every 3 h, shows "confirmed with eBay N ago" on every card, flags anything older than 6 h as
  possibly changed and pauses Requests to Buy on it.
- **No co-mingling in public displays.** eBay content must be visually separated from non-eBay listings.
  → Today every card is from one eBay seller. Seller-catalog items (§5) must get their own section before they are
  shown alongside eBay items.
- **Caching.** Copies only as needed and deleted when no longer needed. → We keep the fields the store displays;
  ended listings drop out of the store (sold ones are shown for 14 days as "sold").
- **Attribution.** Show the seller's username, the condition (and that it's not new), eBay-hosted photos, the price with
  shipping called out, and link to the listing. → All shown on the card page.
- **No AI.** Since the June 2025 update, eBay data may not be fed into AI / gen-AI tools without consent.
  → The classifier is rule-based (`app/vault/classify.py`); no eBay data reaches any model.
- **Purpose.** The licence is for facilitating use of eBay's services. Production use of the Buy APIs is "intended
  for eBay partners": eBay may require the **eBay Partner Network** (EPN) and a Buy API application, approved on the
  business model. **Buying a card on eBay to resell it to our customer is not something the licence clearly covers.**
  Before the resale model goes live: join EPN, describe the model in the Buy API application, and get eBay's answer in
  writing. If eBay says no, switch to the **seller-provided catalog** (§5) with Mayumi's written permission and
  licensed photos. That path needs no eBay API.

## 3. How the catalog works

```
eBay Browse API ──(every 3 h, budgeted)──► vault_listings ──classifier──► eligible? ──admin──► approved ──► /pokemon
```

- **Search:** `item_summary/search` with `category_ids=183454` (Pokémon individual cards),
  `filter=sellers:{mayumicardshop},buyingOptions:{FIXED_PRICE}` and
  `aspect_filter=categoryId:183454,Language:{Japanese}`, 200 a page. Each result's seller is checked again, so another
  seller's item can never slip in.
- **Details:** `item/{id}` once per new candidate, for item specifics (Language, Year, Set), all photos, the seller's
  condition text and availability.
- **Refresh:** approved listings the search didn't confirm are asked about one by one: sold out, ended (404) or still
  available.
- **Budget:** `VAULT_EBAY_DAILY_CALLS` (1,500) of eBay's default 5,000 calls a day; a run stops early rather than exceed
  it.
- **Classifier:** Japanese (the Language item specific, else the title), 1996–2002 (year from item specifics, the
  title or the set), a single raw card (no sealed product, lots, accessories, graded slabs), not another game. Every
  decision carries its reason; the admin can correct the name, set or year.
- **Never invented:** price, shipping, photos, condition and availability are copied from the source. A missing field
  stays empty ("Shipping: see listing").

## 4. Request to Buy (the first purchase step)

A signed-in member taps **Request to Buy**, optionally leaves a contact and a note. That creates a `vault_requests` row:
**not an order, no payment, no reservation**. The page says so before and after. The admin works requests in
Dashboard → Pokémon → ◆ Vault → Requests (new → contacted → closed / declined). Limit: one open request per card per
member, 10 a day.

## 5. Seller-provided catalog (the fallback)

`POST /api/vault/admin/catalogs` creates a seller-catalog source; `POST /api/vault/admin/catalogs/{id}/items` adds or
updates an item by SKU (title, price, currency, https photo URLs, condition, availability, item specifics). The same
classifier and approval apply.

## 6. Physical-card vault (architecture for card-backed NFTs)

Tables: `vault_cards` (permanent id `PCV-XXXX-XXXX`), `vault_card_photos`, `vault_condition_records`,
`vault_redemptions`, `vault_events` (append-only history). Rules in `app/vault/cards.py`:

1. acquisition: `purchase_pending → purchased → (in_transit →) received → verified` (or `rejected`). **Verified needs
   our own condition inspection and our own front photo.**
2. custody: `none → in_custody` only once verified; `in_custody → redemption_pending → shipped → delivered`.
3. NFT: `not_eligible → eligible` automatically when verified AND in custody; `eligible → mint_pending → minted`
   (minted must record chain, contract, token id and tx of a real mint); `minted → burned`.
4. **A card can't ship while its token exists**: it must be burned first.
5. Nothing mints from this code. Minting, payments and custody partners come later, after legal review.

## 7. Configuration

| Setting | Default | |
|---|---|---|
| `VAULT_EBAY_SELLERS` | `mayumicardshop\|Mayumi Card Shop` | username\|name, comma-separated |
| `VAULT_SYNC_SECONDS` | 10800 | 3 h |
| `VAULT_MAX_AGE_HOURS` | 6 | eBay's freshness limit |
| `VAULT_EBAY_MAX_PAGES` | 10 | 200 listings a page |
| `VAULT_EBAY_DETAILS_PER_SYNC` | 60 | getItem calls for new listings |
| `VAULT_EBAY_DAILY_CALLS` | 1500 | |
| `VAULT_INCLUDE_GRADED` | false | the store shows ungraded cards |
| `VAULT_REQUESTS_ENABLED` | true | |
| `EXCHANGE_ENABLED` | false | the retired Paper Exchange |

eBay keys: the Production **App ID** and **Cert ID** (Pokémon › Data sources, or `EBAY_CLIENT_ID` /
`EBAY_CLIENT_SECRET`). The admin panel shows whether they work and what the last sync read.

## 8. API

Public: `GET /api/members/vault/home`, `GET /api/members/vault/listings` (`q, pokemon, set, year, min_price, max_price,
sort, sold, page`), `GET /api/members/vault/listings/{id}`. Members: `POST …/listings/{id}/request`,
`GET /api/members/vault/my/requests`, `POST /api/members/vault/requests/{id}/withdraw`.
Owner: `/api/vault/admin/*` (overview, sources/{id}/sync, listings, listings/bulk, requests, catalogs, cards).
