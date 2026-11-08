# Hyperliquid trading (TR)

Reviewer scope: backend/app/tradingos/{execution,accounts,exchange,risk,signals,market,kitchen,brainx}.py, backend/app/tradingbot/*, routes trading.py / tradingos.py / tradingbot.py, website terminal (frontend/src/website/terminal.tsx), and branch feat/tg-trading (worktree C:\AI\pc-wt\tg-trade, commit 8d14a7c: app/tradingos/tgtrade.py + bot/ingestion changes). Code read at audit worktree = main e978cc3.

## Summary
The execution core is careful and better than most hobby bots. A position is only booked from the exchange's own fill. The stop goes in for the whole filled size right after the fill is committed, and if the stop is refused the position is closed at once (the emergency policy). Closes are always reduce-only. Order ids are 16-byte hex, and unique constraints on (account, signal) and on the order id stop duplicates. An interrupted entry is adopted or recovered by reconciliation. Every member query is scoped to that member's account, and admin routes are deny-by-default. I checked price rounding against Hyperliquid's live metadata: it is valid for all 179 mainnet assets. The liquidation formula matches Hyperliquid's isolated-margin formula.

Biggest risks:
1. Nobody is alerted when a live position is left without a stop (TR-1).
2. All Hyperliquid traffic shares one Railway IP and one request budget. Each executor costs 4 metadata calls, and previews have no rate limit, so one member can trigger rate limiting (HTTP 429) that breaks entries, closes and reconciliation for everyone (TR-2).
3. Emergency and manual closes use an IOC limit only 0.24% from the mark, which can fail in exactly the fast markets where they matter (TR-3).
4. Members' agent keys may still be readable after the October Railway breach. I can't verify this (TR-10).

On the new Telegram branch:
- With `telegram_live` on, a manual `/open` on LIVE skips the LIVE-enabled and API-wallet checks. A test reproduces this (TR-4).
- The Banter referral gate also changes the existing signal ENTER button, against the module's own docstring (TR-5).
- TESTNET, which is the branch's default non-paper mode, prices orders off mainnet (TR-6).

Biggest opportunities: owner and member alerts on unprotected positions, a cached shared executor (metadata fetched once), wider reduce-only close bounds, and testnet-native prices and metadata.

## Findings

### TR-1: A live position left without a stop triggers no alert to the member or the owner
Severity: High · Status: CONFIRMED · Effort: S
Evidence: `execution.emergency()` (execution.py:564-588) sets `PROTECTION_FAILED` and pauses the account. It writes only an audit row. `_retry_emergency` (1024-1041) retries the reduce-only close once per reconciliation, up to `EMERGENCY_RETRIES=10`, then "left to the member". `bot.due_position_announcements` only announces `OPEN`/`CLOSED` rows. A grep for `PROTECTION_FAILED|protection_failed` outside execution.py finds only displays (terminal, admin health counter, brain). There is no owner alert in tradingos (`app/alerts.py` is never called from tradingos or jobs).
Impact: an AUTO entry can open at 3am while nobody watches. If the stop is refused and the emergency close is not filled (see TR-3), a real leveraged position sits unprotected. Nobody is told unless they open the website.
Fix: on `protection_failed` and on every failed retry, send a Telegram DM to the member plus the owner alert chat. Do the same when the retries are exhausted, with the Hyperliquid close link. Add a scheduler health check: any LIVE `PROTECTION_FAILED` with size > 0 counts as an incident.

### TR-2: One member can use up the shared Hyperliquid request budget and block trading for everyone
Severity: High · Status: code CONFIRMED / budget exhaustion SUSPECTED · Effort: M
Evidence: `HyperliquidExecutor.__init__` (exchange.py:132-143) builds `Info(...)` and `Exchange(...)`. In SDK 0.24.0, `Exchange` creates a second `Info`, and every `Info.__init__` fetches `spot_meta()` and `meta()` (site-packages/hyperliquid/info.py:35-71, exchange.py:98). That is 4 info calls at weight 20 each, so 80 weight per executor. The IP limit is 1200/min (docs: rate-limits-and-user-limits; `userRole` is 60, `clearinghouseState` 2, everything else 20). Executors are rebuilt on every `_plan` (each preview and confirm), every `reconcile` (once a minute per account with open TESTNET/LIVE positions), every close and every protection change. `/members/trading/preview` has no rate limit: admin_guard rate-limits only the public Pokémon market. Each preview also costs `ex.state()`, `ex.equity()` (with `userAbstraction`/spot calls) and `extraAgents`, and writes a TrIntent row plus Kiwi Brain rows. All of this shares the Railway IP with the market-data, structure, liqtape and hltape jobs.
Impact: about 14 accounts with open positions already use the whole per-minute budget on reconciliation alone. A member who scripts previews (with their own cookie, TESTNET or LIVE mode) can cause 429s. Then every member's entries fail, closes fail ("Hyperliquid didn't answer the close") and reconciliation errors.
Fix: build `Info` once per network, pass `meta`/`spot_meta` into `Info`/`Exchange` (the SDK accepts them), and cache one `Exchange` per account. Rate-limit preview, confirm, close and checks per member (`service.limit`). Track HL weight in-process and log or alert when it nears 1200/min.

### TR-3: Emergency and manual closes use an IOC limit only 0.24% from the mark
Severity: Medium-High · Status: CONFIRMED (code) · Effort: S
Evidence: execution.py:574 (emergency) and 744 (manual `_close`) use `px = mark * (1 ∓ 3*SLIPPAGE)` with `SLIPPAGE=0.0008`, so 0.24%. The entry is bounded at 0.16% (480). The Hyperliquid SDK's own `market_open/market_close` default is 5% slippage.
Impact: in a fast move (exactly when a stop was refused, or a member hits CLOSE / `/closeall`), an IOC 0.24% through the mark often gets no fill. The emergency path then leaves the position unprotected for up to about 10 more reconciliation minutes (TR-1). The manual path tells the member "Close not filled". Reduce-only already guarantees the order can't flip the position, so a wider bound costs nothing in safety.
Fix: for reduce-only closes use 2-5% (or a book-walk bound), and on "could not immediately match" retry at once with a wider bound. Keep the tight bound for entries.

### TR-4 (feat/tg-trading): a manual `/open` on LIVE skips the LIVE-enabled and API-wallet checks
Severity: Medium (latent: only once the owner sets `strategies.telegram_trading.live`) · Status: CONFIRMED (reproduced) · Effort: S
Evidence: `tgtrade.preview_open`/`confirm_open`/`_sizing` (tgtrade.py:304-397) check only `mode_gate` (the `telegram_live` flag) and the referral gate. They never run execution's `acc.live_enabled_at and live_allowed(...)` (execution.py:283, 368) or `check_live_permission` (agent still listed on Hyperliquid, verified wallet unchanged; 119-134). Reproduction: `backend/tests/audit_tg_trading_live_gate.py` (copy run from the tg-trade worktree). An account with `mode=LIVE` and `live_enabled_at=None` opens a LIVE position and the test passes. That state arises after `connect_api_key` with open LIVE positions (accounts.py:525-529 keeps mode LIVE, clears `live_enabled_at`).
Impact: once Telegram live is switched on, real-money orders can be sent for accounts whose LIVE was withdrawn, or whose verified wallet changed (the agent would sign for the old master). Without the agent check the member also gets an opaque exchange error instead of "permission revoked".
Fix: call the same `execution._plan`-style LIVE preamble (`live_enabled_at`, `live_allowed`, `check_live_permission` when no test exchange is injected) in `_sizing`. Also apply `allowed_coins` and the v2 enforce checks (TR-11).

### TR-5 (feat/tg-trading): the Banter referral gate changes the existing signal ENTER button, and the website stays ungated
Severity: Medium · Status: CONFIRMED · Effort: S (decision)
Evidence: bot.py diff adds `tgtrade.gate_for_entry` before `_enter` for the inline `enter` action. The tgtrade docstring says "The existing signal ENTER flow keeps the rules it already had". The branch had to monkeypatch `tgtrade.entry_gate` away in `test_trading_execution.py` and `test_trading_os.py` (autouse `_banter_member`) so the existing tests keep passing. Website `/members/trading/preview` has no such gate, and `execution.eligibility` says LIVE is open to every member since 2026-10-21.
Impact: existing non-Banter members suddenly can't ENTER from Telegram but can on the website. Every gated press also triggers a referral recheck against Hyperliquid (referral, ledger pages and portfolio, at least 60 weight; rate-limited to 6 per 10 min per member).
Fix: the owner should decide whether this is policy. If not, gate only `/open` (manual trades). Either way, make the website and Telegram consistent and fix the docstring.

### TR-6: TESTNET trades are priced and sized from mainnet data
Severity: Medium · Status: CONFIRMED · Effort: M
Evidence: `_plan` and `open_position` use `market.data().market(coin)`, which is mainnet (mark, `sz_decimals`, `max_leverage`), for every mode. `ref_px` uses testnet mids only for closes and adjustments (execution.py:147-154). `tgtrade._sizing` also uses the mainnet mark. A live read-only comparison today (scratchpad tr/cmp.py) showed mids differing by HYPE +5.97%, ETH +0.44% and SOL +0.31%. `maxLeverage` differs on 77 shared coins (e.g. APT mainnet 10x vs testnet 3x), and 20 mainnet coins are missing on testnet.
Impact: TESTNET entries are IOC at the mainnet mark ±0.16%, so they often get no fill (or fill far away). Stops and targets computed from mainnet can sit on the wrong side of the testnet price and trigger at once. `update_leverage` can be refused. The tg branch makes TESTNET the default non-paper mode, so test results will be noisy or misleading.
Fix: give the executor a network-aware market source (testnet `metaAndAssetCtxs` for TESTNET) and use it in `_plan`, `open_position` and `tgtrade._sizing`.

### TR-7: The row lock that serializes racing entries does nothing on SQLite (production)
Severity: Low-Medium · Status: CONFIRMED (lock no-op) / race SUSPECTED · Effort: M
Evidence: execution.py:445 `select(TrAccount.id)...with_for_update()`. `backend/tests/audit_trading_tr_checks.py::test_sqlite_ignores_with_for_update` shows SQLite renders no `FOR UPDATE`, and pysqlite doesn't start a write transaction on a SELECT. The duplicate-coin check and position-cap check at 446-454 are therefore check-then-insert. The (account, signal) unique constraint covers only the same signal. The scheduler serializes jobs (scheduler.py `_serial`), but website requests run concurrently with AUTO (`tradingos.track`).
Impact: two entries on different signals in the same coin can both pass in a narrow window: a website ENTER racing AUTO, a 1h and a 15m signal, or a tg `/open` racing a website ENTER. Hyperliquid nets them into one position with two stop sets, and the position cap can be exceeded by one.
Fix: on SQLite take the write lock first (`BEGIN IMMEDIATE`, or a dummy `UPDATE tr_accounts SET updated_at=... WHERE id=?` before the checks), or add a partial unique index on (account_id, coin) for held statuses.

### TR-8: Take-profit orders on small positions may be under Hyperliquid's $10 minimum
Severity: Medium · Status: SUSPECTED (behaviour of reduce-only triggers under $10 UNVERIFIED) · Effort: S
Evidence: `protect()` splits the position into TP1 = half and TP2 = the rest (execution.py:550-560). `reprotect` does the same. `plan_trade` with `min_bump` deliberately creates positions just over $10. `audit_trading_tr_checks.py::test_min_bumped_position_gets_take_profit_orders_under_10_usd` shows a $12 position producing TP orders of $6.60 and $6.90. The code knows the minimum for partial closes (742-743) but not for targets. A failed TP is only logged (560).
Impact: if Hyperliquid refuses sub-$10 reduce-only triggers, small positions (the default for small accounts) run without take-profits and the member isn't told. The stop is unaffected.
Fix: if half the position is under $10, place a single TP for the full size at TP1 (or TP2), and surface TP refusals on the position. Verify with one testnet order (needs owner approval to place a testnet write).

### TR-9: The owner's minimum rank and deposit settings no longer gate LIVE, but the UI and tests still treat them as active
Severity: Medium · Status: CONFIRMED · Effort: S (decision)
Evidence: `execution.eligibility` (81-89) requires only a verified wallet. `accounts.connection` still returns `rank.required_level` and `deposit.min_usd`. The known failing test `test_trading_execution::test_referral_states_come_from_hyperliquids_record_and_no_longer_gate_bot_trading` (line 692) asserts "the owner can still set a rank and a minimum deposit ... the existing minimum still applies" and gets `blocker=None`.
Impact: the Admin settings `min_rank_level` / `min_deposit_usd` silently do nothing for bot trading. The failure on main is a real policy mismatch, not a flaky test.
Fix: either honour the settings in `eligibility` (if > 0) or delete the assertion and the UI fields. Don't leave a red test on main.

### TR-10: Members' API-wallet keys may still be decryptable after the October Railway breach
Severity: High · Status: UNVERIFIED (read-only; can't inspect Railway variables or the DB) · Effort: M
Evidence: agent keys are stored as `xch.enc(key)`, Fernet with `APP_ENCRYPTION_KEY` (security/secrets.py). `rekey.py` covers `tr_accounts.*_agent_key` (rekey.py:70-71), which is good. The owner memory (price-chat-railway-todo) lists as still pending: rotate `APP_ENCRYPTION_KEY`, rotate bucket keys (nightly DB backups), and "members' Hyperliquid API wallets (pause live trading + announce)".
Impact: anyone holding an old backup plus the old key can sign trades on every connected member's Hyperliquid account. Trades alone can drain an account through deliberately bad fills. Re-keying does not help keys that were already exposed: only revoking them on Hyperliquid does.
Fix: confirm the rotation status. If it isn't done: set global pause, require members to create a new API wallet (connect_api_key), and mark old agents unusable.

### TR-11 (feat/tg-trading): manual trades skip several of the member's own limits
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: `manual_plan` and `_sizing` check leverage, margin %, max loss, the position cap, daily loss and pause. They skip `allowed_coins`, the trades-per-day cap and cooldown (`style_limits`), the Engine v2 `enforce` portfolio checks and `intraday_allowed`. Compare execution `_plan` 299-313.
Fix: reuse the shared checks, or document the exception in the member's settings text.

### TR-12: P&L booking from exchange fills can be incomplete
Severity: Low · Status: CONFIRMED (code) · Effort: S
Evidence: `HyperliquidExecutor.fills_since` calls `user_fills_by_time` once, with no paging (Hyperliquid returns at most 2000). `tradingbot.hyperliquid.InfoClient.fills` pages, so the paged version already exists. In `_reconcile` a TP1 taken on the exchange only shrinks `size` (1005-1006). Realized P&L for that half isn't booked until the final close, so `day_loss_basis` under-counts realized gains and losses meanwhile.
Fix: page fills, and book partial exchange fills during reconciliation.

### TR-13 (feat/tg-trading): manual signal rows can collide and are never cleaned up
Severity: Low · Status: CONFIRMED code / race SUSPECTED · Effort: S
Evidence: `_anchor` uses `min(number)-1` on a unique column (models/tradingos.py:48). Two concurrent previews raise an `IntegrityError`, which `tgtrade.command` doesn't catch (it catches only TradeError/MarketError). Every `/open` preview inserts a TsSignal even if never confirmed. brainx's member results (118, 355) will mix manual trades into "signal" learning.
Fix: retry on IntegrityError (or use `-id`), create the anchor at confirm time, and filter `strategy=="manual"` out of brainx learning.

### TR-14 (feat/tg-trading): Telegram claims an API wallet "can never withdraw"
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: tgtrade.py:19 and `SECRET_WARNING` (147-150) say this. accounts.py:340-346 says Hyperliquid's docs don't state it, so the website deliberately doesn't claim it.
Fix: use the website's wording ("Price Chat never sends withdrawals or transfers with it; you can revoke it on Hyperliquid").

### TR-15 (feat/tg-trading): an old `/closeall` button still works and closes whatever is open when pressed
Severity: Low · Status: CONFIRMED · Effort: S
Evidence: the button's callback data is the constant `ca:all` (tgtrade.py:668-669, 719-726), with no count or expiry. The website requires `confirm_count` to match (routes/trading.py:191-201).
Fix: put the count and a timestamp in the callback, and refuse it if either is stale.

### TR-16: Switching the Hyperliquid account through the API key can orphan TESTNET positions
Severity: Low · Status: CONFIRMED (code) · Effort: S
Evidence: `connect_api_key` blocks a master change only when `mode == "LIVE" and open_count` (accounts.py:508). It then sets `acc.master_address = master` (523), but the testnet agent was approved for the old master. The `HyperliquidExecutor(TESTNET, testnet_key, new master)` reconciles the wrong account, finds nothing held, and marks those positions CLOSED.
Fix: block on any `_open_on(...)` for any network, as `agent_start` does.

### TR-17: Things checked and found correct (no action needed)
Status: CONFIRMED
- Tick size: `risk.round_px` (5 significant figures, at most `6 - szDecimals` decimals, integers always OK) produced valid prices at mark ×{1, 1.0016, 0.9976, 0.97, 1.05} for all 179 live mainnet and 159 testnet assets (scratchpad tr/meta.py). Lot size: `round_size` floors to `szDecimals`.
- Order ids: `cloid_for` gives "0x" + 32 hex (16 bytes). A unique DB constraint plus suffixes per attempt and per sequence mean no id is reused.
- Entries are IOC limits. Stops and targets are `trigger{isMarket: True, tpsl}` with `reduce_only=True`. Closes are reduce-only. Leverage is set isolated (`is_cross=False`) before the entry. The stop covers the whole filled size, and a partial IOC fill is protected for what filled.
- Duplicates: `confirm` marks the intent CONFIRMED before sending. A double press hits `uq_tr_positions_account_signal`. A FAILED-row retry is claimed with an atomic UPDATE. The close lease is an atomic UPDATE with no write lock held during exchange calls.
- Crash safety: the OPENING row is committed before the order, and the fill is committed before protection. `_adopt` handles exceptions, and `_recover_opening` takes over after 3 minutes. `_held` refuses an answer without a position list, so all stops are never cancelled by mistake.
- Isolation between users: every member route resolves the account from the session. `_mine`, `cancel_intent`, `cancel_order` and the tg callbacks all bind to the member. Admin routes go through admin_guard (deny-by-default), and a same-origin check runs on writes.
- Agent keys: the website flow is paste-the-key (`connect_api_key`). The key is validated with `userRole`, a main-wallet key is refused, the key is never echoed in API responses or audit rows (only the agent address), and one agent maps to one Passport. ApproveAgent (`agent_start`/`agent_finish`) checks that the EIP-712 signer is the verified wallet and the nonce is within 10 minutes. The frontend doesn't use that flow.
- Liquidation: `tgtrade.liquidation_price` matches Hyperliquid's isolated formula. `risk.plan_trade` keeps the stop within 80% of the distance to liquidation, using the first-tier maintenance margin (positions are small; margin tiers are ignored).
- Fees: `TAKER_FEE=0.00045` matches Hyperliquid's base-tier taker fee. Fill booking uses `closedPnl - fee` from the exchange.
- `exchange/` is the Pokémon paper exchange (paper USDT, no real money), and `arbitrage/` is paper or read-only (lunch bot is paper; funding alerts are read-only). Neither touches real money.

## Untested / couldn't verify (and why)
- Whether Hyperliquid accepts reduce-only trigger orders under $10 (TR-8), and how it treats `limit_px == triggerPx` on `isMarket` triggers (I believe it applies its own 10% slippage). Settling either needs a testnet order (a write), which the brief doesn't allow without approval.
- The real production Hyperliquid weight use and any 429s (TR-2): the Railway logs and job stats weren't read for this item.
- Key rotation and revocation status after the breach (TR-10): reading it needs `railway variables`, which the brief forbids, or the DB.
- Nonce collisions: the SDK uses millisecond timestamps per agent, and AUTO, website and reconciliation share one agent per account. Two actions in the same millisecond would collide. This is unlikely given the serialized scheduler, and not reproduced.
- Frontend Earn and terminal UX was not exercised in a browser.

## Improvement & feature ideas (ranked)
1. **Unprotected-position alarm** (TR-1): Telegram DM plus owner alert, and a red banner on /earn. It's cheap and closes the worst outcome.
2. **One cached executor per account** with shared metadata, plus per-member rate limits (TR-2). This also makes every command faster (4 fewer round trips per action).
3. **Reduce-only closes with 2-5% bounds and an immediate wider retry** (TR-3).
4. **Testnet-native market data** (TR-6), so TESTNET is a real rehearsal for LIVE, which the tg branch relies on.
5. **API wallet expiry reminders**: `validUntil` is already read in `agent_status`. A daily job could DM members 7 days and 1 day before expiry, since an expired agent can't close or adjust positions from Price Chat.
6. **Move the stop to breakeven after TP1** (a member setting). Today, once TP1 is taken, the remaining half still risks the full original stop distance.
7. **SQLite-safe entry lock** (TR-7) and paged fills (TR-12).
8. Put the tg-branch fixes (TR-4, TR-5, TR-11, TR-13, TR-14, TR-15) in the PR before merging.

## Commands run (for the evidence appendix)
- `pytest tests/test_trading_bot.py tests/test_trading_execution.py tests/test_trading_execution_safety.py tests/test_trading_live_recovery.py tests/test_trading_os.py tests/test_trading_qa.py` (audit worktree, shim): **118 passed, 1 failed** (the known `test_referral_states_...`, see TR-9).
- `pytest tests/test_tg_trading.py tests/test_trading_execution.py tests/test_trading_os.py tests/test_trading_execution_safety.py tests/test_trading_live_recovery.py` (tg-trade worktree, shim): **100 passed, 1 failed** (same known test). All of test_tg_trading.py passes.
- `pytest tests/audit_trading_tr_checks.py -s` (audit): 2 passed. SQLite renders no FOR UPDATE, and a $12 position gets TP orders of $6.60 and $6.90.
- `pytest -p tests.conftest <scratchpad>/tgrun/test_audit_live_gate.py` (run from tg-trade/backend; the source file is kept at audit/backend/tests/audit_tg_trading_live_gate.py): 1 passed. LIVE manual open with `live_enabled_at=None` filled.
- Read-only Hyperliquid info: mainnet and testnet `metaAndAssetCtxs`, `meta` and `allMids` (scratchpad tr/meta.py, tr/cmp.py). 0 invalid rounded prices. 77 coins with different szDecimals/maxLeverage between networks. Testnet mid gaps up to +5.97% (HYPE).
- Docs read: Hyperliquid "Nonces and API wallets", "Order types", "Rate limits and user limits".
- No orders placed, no writes to any API, no Railway commands.
