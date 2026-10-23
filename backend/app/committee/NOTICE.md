# Kiwi AI Investment Committee — attribution and design notes

## TradingAgents (Apache License 2.0)

The committee's structure is a port of the multi-agent pattern of **TradingAgents** by Tauric Research
(https://github.com/TauricResearch/TradingAgents), version 0.7.0, commit `e8f5503`, licensed under the
Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0). Audited 2026-10-23.

What was ported (the pattern, re-implemented in this package; no TradingAgents code is copied or imported):

- the role graph of `tradingagents/graph/setup.py` and `conditional_logic.py`: four analysts (market/technical,
  fundamentals, news, sentiment) → bull and bear researchers (at most one round each) → research manager → trader →
  aggressive / neutral / conservative risk analysts (one round) → portfolio manager;
- structured outputs for the decision makers (`agents/schemas.py`): typed JSON whose field descriptions are the
  instructions (here `schemas.py`, with no price fields at all — see below);
- the 5-tier rating vocabulary of `agents/rating.py` (Buy / Overweight / Hold / Underweight / Sell) and its rule that
  conflict alone is not a reason to Hold;
- scoring decisions from the first close AFTER the decision time (`backtest.py`), here `outcomes.py`.

What is different, on purpose:

- every prompt is rewritten for crypto perpetuals and for our evidence pack (`prompts.py`); none is copied verbatim;
- models may only use the evidence pack, cite its ref ids and copy its numbers; `grounding.py` replaces any role text
  with an ungrounded number (or probability talk) by that role's deterministic rules text, and records it;
- prices never come from a model: plans are copied from the evidence scenarios by code (`roles.plan_from`);
- every role has a deterministic writer (`roles.rules_*`), used by the rules backend and as each role's fallback;
- execution approval is not the portfolio manager's: it comes only from the existing deterministic Kiwi risk engine
  (`risk_gate.py` → `app.tradingos.risk` + `app.tradingos.riskteam`), and the committee never places an order;
- the three risk voices answer in ONE call (one round) and the two rebuttals in one call, so a full run stays within
  the 12-calls-per-run budget (11 calls; 10 on depth "quick"). TradingAgents runs them as separate sequential turns.

## Why the `tradingagents` package is not a dependency

- It pulls langchain / langgraph / pandas / yfinance / rich / typer into the backend for a pattern that is a few hundred
  lines here.
- Its data vendors are equity-focused (Yahoo, SEC EDGAR, FRED, StockTwits) and would bypass our Hyperliquid data and
  the point-in-time evidence pack.
- Its LLM clients bypass our AI router, budgets, Vortex channel and cost accounting.
- It writes state to `~/.tradingagents`, which a server must not do.

## ChatGPT subscriptions are not supported

TradingAgents can "Sign in with ChatGPT". That is a personal-plan OAuth flow: the calls count against one person's
ChatGPT plan. It is not an API key, its terms are for that person's own use, and it is not suitable for a server bot
serving many users. We don't use it. The OpenAI backend here takes an API key only (`OPENAI_API_KEY` or the encrypted
secret store), like the Anthropic backend (`ANTHROPIC_API_KEY`); with no key that backend is simply not selectable.
