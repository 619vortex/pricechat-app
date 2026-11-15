"""Real chart posts from t.me/Pricechatupdates (2026-11-12..15), rebuilt from exactly what they published, for the chart-caption rules
(owner, 2026-11-15: "the caption repeats nearly every number already visible in the chart", "Tesla and Solana are mentioned twice", "too
many cashtags and a giant wall of text"). BEFORE holds each post's caption as it went out; build() makes the event the new code captions."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

NOW = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)

# post 181, 2026-11-15 08:03 UTC: the weekly ranking (every figure the card showed)
RANKING_7D = {"TSLA": 8.9, "AAPL": 4.1, "^GSPC": 2.5, "^IXIC": 2.3, "CL=F": -2.5, "GC=F": -4.6, "COIN": -5.3, "SI=F": -5.4, "NVDA": -12.7, "BTC-USD": -13.2,
              "MSTR": -13.4, "ETH-USD": -14.3, "SOL-USD": -17.4}

BEFORE = {
    "ranking": "The week's best and worst: Tesla +8.9%, Solana $SOL −17.4%. Tesla +8.9%, Apple +4.1%, The S&P 500 +2.5%, The Nasdaq +2.3%, WTI crude −2.5%, "
               "Gold −4.6%, Coinbase −5.3%, Silver −5.4%, Nvidia −12.7%, Bitcoin $BTC −13.2%, Strategy −13.4%, Ether $ETH −14.3%, Solana $SOL −17.4% (7 days).\n\n"
               "🔗 Yahoo Finance\n\n@thepricechat",
    "btc_gold": "Bitcoin $BTC vs gold over the past year: −31.2% against −7.4%. Gold leads by 23.7 percentage points.\n\n🔗 Yahoo Finance\n\n@thepricechat",
    "tsla_nvda": "Tesla vs Nvidia over the past 30 days: +3.4% against −23.2%\n\nTesla leads by 26.6 percentage points.\n\n🔗 Source: Yahoo Finance\n\n"
                 "🥝 Price Chat News · Measured on our price data (Yahoo Finance)",
    "btc70k": "JUST IN: Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs ($BTC -5.4% in 24H). More than $1B in crypto positions "
              "were forced shut in 12 hours as bitcoin $BTC broke below $70,000.\n\n🔗 Unchained\n\n@thepricechat",
    "btc64k": "JUST IN: Bitcoin $BTC drops below $64,000 as bullish bets get wiped out over the weekend. CoinGecko showed BTC near $65,500 on the afternoon of "
              "Nov. 14, while Newhedge recorded long-liquidation events below $66,000. Read the full story on CryptoSlate Understand the crypto stories that "
              "matter and what to watch next.\n\n🔗 CryptoSlate\n\n@thepricechat",
}
POSTS = {"ranking": 181, "btc_gold": 175, "tsla_nvda": 165, "btc70k": 173, "btc64k": 179}
# the stories' own rows (title, summary as the feed gave it)
STORIES = {
    "btc70k": ("Unchained", "https://unchainedcrypto.com/bitcoin-breaks-below-70000-as-liquidations-sweep-through-leveraged-longs/",
               "Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs ($BTC -5.4% in 24H)",
               "More than $1 billion in crypto positions were forced shut in 12 hours as bitcoin broke below $70,000."),
    "btc64k": ("CryptoSlate", "https://cryptoslate.com/bitcoin-drops-below-64000-as-bullish-bets-get-wiped-out-over-the-weekend/",
               "Bitcoin drops below $64,000 as bullish bets get wiped out over the weekend",
               "CoinGecko showed BTC near $65,500 on the afternoon of Nov. 14, while Newhedge recorded long-liquidation events below $66,000. Read the full "
               "story on CryptoSlate Understand the crypto stories that matter and what to watch next."),
}


def series(ret_pct: float, days: int, end: datetime = NOW, start: float = 100.0) -> list[tuple[datetime, float]]:
    """Daily closes with exactly ret_pct over the last `days` days (a flat history before)."""
    out = [(end - timedelta(days=days + 40 - i), start) for i in range(40)]
    step = (1 + ret_pct / 100) ** (1 / days)
    return out + [(end - timedelta(days=days - i), start * step ** i) for i in range(1, days + 1)]


def data_event(name: str, NdEvent: Any) -> Any:
    from app.newsdesk import datapost

    if name == "ranking":
        data = {s: series(r, 7) for s, r in RANKING_7D.items()}
        post = datapost.ranking(data, NOW)
    elif name == "btc_gold":
        post = datapost.battle("BTC-USD", "GC=F", {"BTC-USD": series(-31.2, 365), "GC=F": series(-7.4, 365)}, NOW, 365)
    else:
        post = datapost.battle("TSLA", "NVDA", {"TSLA": series(3.4, 30), "NVDA": series(-23.2, 30)}, NOW, 30)
    assert post is not None, name
    ev = datapost._event(post, f"data:{name}", NOW, 60, NOW + timedelta(hours=8))
    ev.id, ev.released_at, ev.slot = 950 + list(POSTS).index(name), NOW, "slot"
    return ev


def story_event(name: str, NdEvent: Any) -> tuple[Any, list[Any]]:
    src, url, title, summary = STORIES[name]
    rows = [SimpleNamespace(source=src, link=url, title=title, summary=summary)]
    first = NOW - timedelta(minutes=30)
    ev = NdEvent(key=f"case:{name}", kind="news", title=title, headline=title, summary=summary, topic="", label="⚡ JUST IN", evidence="attributed",
                 category="crypto", slot="slot", released_at=NOW, first_seen=first, sources=[{"name": src, "url": url, "title": title}],
                 facts={"told": title, "published_at": first.isoformat(), "outlets": [src]})
    ev.id = 950 + list(POSTS).index(name)
    return ev, rows
