# Price Chat Card Marketplace — plan, architecture and launch gates

Status (2026-10-09): **Phase 1 (MVP) and Phase 2 (trading simulation) implemented.** No real money moves. Live USDT
settlement (Phase 4) is disabled in code and cannot be switched on without the gates in §8.

## 1. Collector Crypt: publicly documented functionality (functional reference only)

Sources: docs.collectorcrypt.com (Vault: deposit/withdraw; Marketplace: user guide), Alchemy case study, Solflare Packs FAQ.

| Capability | Collector Crypt (public docs) | Price Chat marketplace |
|---|---|---|
| Inventory | Graded cards only, deposited to their vault (free deposit) or partner vaults | Graded cards only (PSA/BGS/CGC); one inventory record per physical slab (unique company+cert) |
| Custody | Insured vault (operator not named publicly) | Vault-partner interface; **no partner contracted yet**, so no card is shown as vaulted/insured until a partner confirms it |
| Ownership record | 1:1 Solana NFT per vaulted card; trades move the token, not the slab | Internal ownership ledger (Option A) now; token (Option B) only after legal/custody/audit sign-off |
| Payments | USDC on Solana (+ SOL for gas) | USDT, network chosen explicitly (Tron, Ethereum, Solana); simulation only today |
| Buy / offers | Buy Now; offers escrowed in USDC until accepted/cancelled | Buy Now; offers with expiry; funds only ever move through an audited escrow/payment partner (not built in-house) |
| Redemption | Burn NFT, pay shipping, card ships | Redeem → item locked → vault release → tracking → marked redeemed only after release |
| Extras | Gacha packs (VRF), instant buyback 85–90%, cross-chain bridge, eBay bid tool | Not planned (gambling-adjacent / regulatory risk); price intelligence is our differentiator |

Not copied: no code, text, images, logos or layouts. Our UI reuses Price Chat's market design.

## 2. Reused Price Chat components

Kiwi Passport sessions (member auth), owner-only admin gate (`/api/*` private by default), Pokémon catalogue (exact
printings), valuation engine (`portfolio/valuation.Pricer`: exact grade, comparable grades, raw), verified sales,
Scrydex/PriceCharting reference prices, private photo bucket + image sanitiser (`portfolio/photos`), Fernet secret
protection (`security/secrets.protect`) for addresses, portfolio service (holdings, flows), scheduler (single-run jobs).

## 3. Architecture (implemented)

Backend `app/market/` (service, payments, compliance, vault) + routes `app/api/routes/market.py`; migration 0028.

Tables: `market_sellers`, `market_items` (physical inventory, unique `cert_key`, unique `vault_inventory_id`),
`market_photos`, `market_listings` (partial unique index: one open listing per item), `market_listing_events`,
`market_watch`, `market_offers`, `market_orders` (partial unique index: one live order per listing; unique idempotency
key), `market_payments` (unique network+tx), `market_ledger` (unique idempotency key per entry), `market_transfers`,
`market_redemptions` (encrypted address), `market_disputes`, `market_reports`, `market_audit`.

Concurrency: every state change is a compare-and-swap (`UPDATE … WHERE id=? AND status=? AND version=?`), backed by
the partial unique indexes, inside one DB transaction. Settlement is idempotent (ledger entries keyed per order+kind).

Listing statuses: draft → awaiting_verification → awaiting_custody → verified → active → reserved → sold;
cancelled / rejected terminal. Order statuses: awaiting_payment → paid → completed; expired / cancelled /
refund_pending → refunded / failed.

## 4. Ownership: Option A (ledger) vs Option B (token)

| | A — internal ledger | B — token per vaulted card |
|---|---|---|
| Complexity | Low: DB rows + audit trail | High: mint/burn programs, indexer, wallet custody of NFTs |
| Custody needs | Vault contract + reconciliation | Same, plus legal link token ⇄ slab, burn-on-redeem |
| Smart-contract risk | None | Program bugs, key compromise, frozen/duplicated claims |
| UX | Email/Google sign-in, no wallet needed to own | Wallet required to hold/transfer |
| Legal | Contractual title held by marketplace terms | Token may be a virtual asset / security in some jurisdictions (UAE VARA, EU MiCA…) |
| Fees | None per transfer | Network fees per mint/transfer/burn |
| Ops cost | Low | Audits (USD 30–100k+), RPC/indexer, monitoring |
| Recovery | Admin-correctable with audit log | Lost keys = lost claim unless custodial |

**Recommendation: Option A now**; design items so a token can later mirror one `market_items` row (1:1, burn on
redeem). Do not mint transferable asset-backed tokens until legal rights, custody contracts, redemption terms and an
independent smart-contract audit are complete.

## 5. USDT networks

USDT contracts (Tether's published list, tether.to/supported-protocols): Ethereum `0xdAC17F958D2ee523a2206206994597C13D831ec7`,
Tron `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`, Solana `Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB` (all 6 decimals).
Bridged versions on other chains are NOT accepted (not interchangeable).

| | Tron (TRC-20) | Solana (SPL) | Ethereum (ERC-20) |
|---|---|---|---|
| Typical fee | ~1–3 USDT (energy) | < 0.01 USD | 1–10+ USD |
| Demand | Highest USDT volume, strong in MENA/Asia | Strong collectibles/NFT community | Institutional, costly |
| Wallets | TronLink, exchanges | Phantom, Solflare, Backpack | MetaMask, WalletConnect |
| Infra | TronGrid | Helius/Alchemy | Alchemy/Infura |

Recommendation: launch with **Tron + Solana**, Ethereum optional for high-value orders. Payments must go through a
regulated payment/escrow partner (e.g. a licensed VASP / crypto payment processor with escrow) — never a custom,
unaudited escrow contract.

Payment verification (implemented as an interface; simulation provider active): the server independently fetches the
transfer by network+tx, checks token contract, recipient (per-order deposit address from the partner), amount, sender,
confirmations and time window; a tx can settle at most one order (unique index); amounts are compared in base units.

## 6. Missing third-party providers (blockers for live trading)

1. Vault / fulfilment partner (receive, inspect, photograph, store, insure, ship) with API or SFTP reconciliation.
2. Regulated USDT payment + escrow partner (deposit addresses, confirmations, refunds, payouts).
3. KYC/KYB + sanctions screening provider (sellers always; buyers above thresholds).
4. Durable transactional database (managed PostgreSQL with PITR backups) — SQLite is acceptable for simulation only;
   live mode refuses to start on SQLite.
5. Insurance (in-vault and in-transit), shipping carrier accounts, tax/VAT advice.
6. Legal: UAE (VARA/CBUAE/ADGM depending on structure), consumer protection, terms of sale, redemption rights.

## 7. Operating cost estimate (monthly, early stage)

Managed Postgres 25–100 USD; vault partner per-card storage ~1–3 USD/card/month + intake fees; KYC 1–3 USD/check;
payment partner 0.5–1.5 % per transaction; insurance ~0.5–1 % of insured value/yr; legal set-up 15–60k USD one-off;
security review of payment integration 10–30k USD one-off. Price Chat infrastructure otherwise unchanged.

## 8. Live-trading gates (enforced in code: `app/market/compliance.py`)

`MARKET_MODE=live` is refused unless ALL are true: `MARKET_LIVE_APPROVED=true` (explicit owner approval), a payment
partner configured, at least one approved vault partner, KYC provider configured, PostgreSQL database, and the
jurisdiction block-list loaded. Until then the platform runs in `simulation`: sandbox listings only, simulated
payments, never counted as marketplace volume.

## 9. Play Market (virtual, separate from this marketplace)

The Pack Giveaway has its own **Play Market** (`app/members/play_market.py`, migration 0038): members sell giveaway cards
instantly at their market estimate, list them at their own price, or buy other members' listings — paid in **Play USDT**,
a test currency with no value. Play USDT is not Tether's USDT: it can't be bought, deposited, withdrawn or exchanged, and
never buys packs. It shares nothing with this marketplace (no items, payments, ledger or live-trading gates). Balances move
only by compare-and-swap with an append-only `play_ledger`; listings by compare-and-swap on status + version.
Giveaway cards can also be shown in a member's Pokémon Portfolio (opt-in) as private `source=giveaway` holdings: counted in
the owner's own value and chart, never in public value, the collectors' leaderboard, explore, or this marketplace.

## 10. Raw cards, verified sellers, wallets and testnet (2026-10-11)

**Verified sellers only.** Listing requires `market_sellers.status = verified` (or a sandbox tester). Members request
verification from the Sell page (country + a note); the owner approves it in Admin → Marketplace → Sellers.

**Raw (ungraded) cards.** A raw listing comes from a raw portfolio card that the AI condition assistant has reviewed
(front + back photos, `app/portfolio/condition.py`). Its condition (NM / LP / MP / HP / DMG) is derived from the AI's
1-10 range (`app/market/raw.py: label_for`), never from the seller's say-so; the AI report is stored on the item and shown
on the listing. Members can add their own 1-10 grade (one each, not the seller) and comment (one reply level). Raw items
have no cert (`cert_key = RAW:H<holding>`), need front + back photos, and are valued from the raw condition range.

**Wallets (EVM).** Members connect OKX Wallet, Binance Wallet, MetaMask… (EIP-6963 discovery) and prove ownership by
signing a one-time message (EIP-191). The server recovers the signer itself (`app/market/evm.py`: pure-Python keccak-256
and secp256k1 recovery, low-s only, checked against published vectors) and stores only the public address
(`market_wallets`, one address per account). The first wallet receives seller payouts. No key or seed phrase is ever
asked for or stored, and the server never signs anything.

**Testnet payments (before real money).** Set `MARKET_TESTNET_RECEIVE` (the owner's receiving TEST wallet address) to
enable the `sepolia` network: Ethereum Sepolia with Circle's test USDC (`0x1c7D…7238`, free at faucet.circle.com).
Only sandbox listings and sandbox testers can use it, so test tokens never buy a real card. Each order's amount carries
a unique sub-cent tail; the buyer's wallet sends the ERC-20 transfer from the order page; the server reads the receipt
from a Sepolia JSON-RPC node (`EvmRpcProvider`, read-only, `MARKET_TESTNET_RPC`) and settles only when the token,
recipient, exact amount, sender (a wallet verified on the buyer's account), time and 3 confirmations all check out.
Pending payments are re-checked by the buyer's page and the `pokemon_market_expire` job; an order with a payment on its
way doesn't expire. One transaction settles at most one order.
