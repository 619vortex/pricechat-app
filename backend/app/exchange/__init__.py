"""Pokémon Paper Exchange: PAPER TRADING of synthetic contracts on 10 graded cards (see docs/EXCHANGE.md).

  specs    the 10 markets (exact card, edition, language, grader, grade) and the contract rules
  ledger   paper accounts, double-entry journal, balances with holds (no negative balances, idempotent postings)
  oracle   the real-card reference price: verified sales + Scrydex, outliers rejected, damped, staleness checked
  engine   the price-time-priority limit order book and matching (market / limit, partial fills, fees, holds)
  maker    the SIMULATED MARKET MAKER: bounded, inventory-aware quotes around the reference, own funded account
  views    tickers, book depth, trades, candles from real paper trades, accounts, P&L, leaderboards

No real money anywhere: paper USDT can't be deposited, withdrawn or redeemed; contracts convey no ownership of any card.
"""
