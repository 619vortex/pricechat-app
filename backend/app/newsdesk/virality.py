"""How far a story will travel, from 0 to 100, with every point explained. One score for X and Telegram alike.

The score only ranks stories that already have their evidence (app/newsdesk/chaos). It never lets an unverified story through, and nothing
in it is a guess about the future: it adds up what the outlets wrote, what our own prices measured, who carries it, and how fresh it is.

  severity     0-38  what happened: a hack, an exchange failure, a scam or rug, liquidations, a crash or pump, a fight, a policy shock
  magnitude    0-40  how big: the dollars involved, the move's size against its alert line (our prices), the percent in the headline
  reach        0-15  who it touches: Bitcoin, Ether, the big indices, mega-cap stocks, gold and oil reach most people; famous names add a little
  evidence     0-15  official 15, our own prices or exchange data 12-14, two outlets 12 (more for each extra outlet), one attributed outlet 6
  spread       0-10  how many outlets picked it up within three hours: the nearest thing to engagement before we post
  engagement  -8..8  how this kind of story did on our own account lately (app/newsdesk/engagement); 0 until there is data
  freshness   x0.55-1  full marks for 45 minutes, then it fades

An allegation (a lawsuit, a charge) loses 6: it may be news, but it isn't a finding.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.newsdesk import brief

SEVERITY = {"hack": 38, "failure": 38, "scam": 34, "liquidations": 30, "move": 24, "controversy": 20, "macro": 20, "whale": 14, "etf": 6}
REACH = {"BTC": 12, "ETH": 12, "SOL": 8, "XRP": 8, "BNB": 8, "DOGE": 6, "SPX": 15, "IXIC": 15, "DJI": 13, "NVDA": 11, "TSLA": 11, "AAPL": 11, "MSFT": 11,
         "AMZN": 10, "META": 10, "GOOGL": 10, "AMD": 8, "COIN": 9, "MSTR": 9, "HOOD": 7, "GOLD": 11, "SILVER": 9, "OIL": 11, "BRENT": 10, "USDT": 9, "USDC": 8}
GROUP_REACH = {"crypto_major": 12, "crypto_alt": 8, "index": 15, "stock": 11, "commodity": 10}
FAMOUS = re.compile(r"\b(musk|elon|saylor|cz|changpeng zhao|sbf|bankman-fried|do kwon|justin sun|trump|buffett|dimon|powell|vitalik|binance|coinbase|tether|blackrock)\b", re.I)
EVIDENCE = {"official": 15, "exchange data": 12, "price data": 12, "two outlets": 12, "attributed": 6}
ALLEGATION_PENALTY = 6


@dataclass
class Score:
    total: int
    parts: dict[str, float] = field(default_factory=dict)

    def explain(self) -> str:
        return ", ".join(f"{k} {v:+g}" if k in ("engagement", "allegation") else f"{k} {v:g}" for k, v in self.parts.items() if v)


def _move_points(ratio: float) -> float:
    """A move's size against its alert line: just over it is 10, twice it 25, three times or more 40."""
    if ratio <= 0:
        return 0.0
    if ratio < 1:
        return round(ratio * 8, 1)
    return round(min(40.0, 10 + (ratio - 1) * 15), 1)


def _usd_points(usd: float) -> float:
    return 25 if usd >= 1e9 else 20 if usd >= 5e8 else 16 if usd >= 1e8 else 10 if usd >= 1e7 else 4 if usd >= 1e6 else 0


def _freshness(age_minutes: float) -> float:
    return 1.0 if age_minutes <= 45 else max(0.55, 1 - (age_minutes - 45) / 600)


def _finish(parts: dict[str, float], age_minutes: float) -> Score:
    base = sum(v for k, v in parts.items() if k not in ("engagement",))
    f = _freshness(age_minutes)
    parts["freshness"] = round(f, 2)
    total = base * f + parts.get("engagement", 0)
    return Score(max(0, min(100, int(round(total)))), parts)


def topics_in(text: str) -> list[str]:
    return [name for name, pattern, _, _ in brief.TOPICS if pattern.search(text or "")]


def news(*, title: str, summary: str, evidence: str, outlets: int, outlets_3h: int, age_minutes: float, alleged: bool, tickers: list[str],
         measured_ratio: float = 0.0, engagement: float = 0.0, lead_topic: str = "") -> Score:
    """A verified story. outlets: other outlets carrying it; outlets_3h: those within three hours of the first; measured_ratio: the move our
    prices measured for the headline's lead asset, against its alert line (0 if the headline makes no price claim we confirmed)."""
    in_title, in_summary = topics_in(title), [t for t in topics_in(summary) if t not in topics_in(title)]
    sev = max([SEVERITY.get(t, 0) for t in in_title] + [SEVERITY.get(t, 0) / 2 for t in in_summary] + [0])
    others = sorted([SEVERITY.get(t, 0) for t in in_title], reverse=True)[1:]
    severity = min(38.0, sev + sum(others) / 6)
    if lead_topic == "move" and "move" in in_title:  # "Bitcoin rebounds as markets shrug off the hack" is a price story: the hack is context
        price = max(SEVERITY[t] for t in in_title if t in ("move", "liquidations"))  # liquidations are price chaos: they keep their weight
        severity = min(severity, price + (sum(SEVERITY.get(t, 0) for t in in_title) - price) / 6)
    usd = brief._amount_usd(f"{title} {summary}")
    topic_move = any(t in ("move", "liquidations") for t in in_title)
    pct = max((float(p) for p in brief.PERCENT.findall(title)), default=0.0) if topic_move else 0.0
    pct_points = 15 if pct >= 20 else 10 if pct >= 10 else 5 if pct >= 5 else 0
    # dollars measure a loss, a flush or a flow; a deal's size ("a $28B lease") isn't chaos
    money_topic = any(t in ("hack", "scam", "failure", "liquidations", "whale", "controversy") for t in in_title)
    magnitude = max(_usd_points(usd) if money_topic else 0, pct_points, _move_points(measured_ratio))
    reach = max([REACH.get(t, 4) for t in tickers[:2]] + [0])
    if FAMOUS.search(title):
        reach += 6
    ev = EVIDENCE.get(evidence, 0) + (min(outlets - 1, 2) * 1.5 if evidence == "two outlets" and outlets > 1 else 0)
    parts = {"severity": round(severity, 1), "magnitude": magnitude, "reach": min(15, reach), "evidence": min(15, ev), "spread": min(10, outlets_3h * 3)}
    if alleged:
        parts["allegation"] = -ALLEGATION_PENALTY
    if engagement:
        parts["engagement"] = round(max(-8.0, min(8.0, engagement)), 1)
    return _finish(parts, age_minutes)


def market(*, kind: str, group: str, ratio: float, two_sources: bool, symbol_key: str = "", round_level: bool = False, engagement: float = 0.0) -> Score:
    """A move our prices measured. kind: move | wide (several assets at once) | high | low | milestone."""
    severity = {"wide": 24, "move": 20, "high": 16, "low": 18, "milestone": 18 if round_level else 14}.get(kind, 16)
    magnitude = _move_points(ratio) if kind in ("move", "wide") else 20 if round_level else 8  # Bitcoin through $100,000 travels; through $105,000 doesn't
    reach = 15 if kind == "wide" else REACH.get(symbol_key, GROUP_REACH.get(group, 6))
    parts = {"severity": float(severity), "magnitude": magnitude, "reach": float(min(15, reach)), "evidence": 14.0 if two_sources else 12.0}
    if engagement:
        parts["engagement"] = round(max(-8.0, min(8.0, engagement)), 1)
    return _finish(parts, 0)


def liquidations(*, usd: float, coin: str, engagement: float = 0.0) -> Score:
    """A flush the exchange feeds recorded: the dollars are measured, not reported."""
    parts = {"severity": 30.0, "magnitude": float(_usd_points(usd)), "reach": float(REACH.get(coin, 6)), "evidence": 12.0}
    if engagement:
        parts["engagement"] = round(max(-8.0, min(8.0, engagement)), 1)
    return _finish(parts, 0)


def label_for(score: int, *, topic: str, strong: bool, age_minutes: float, risk: bool) -> str:
    """Labels are earned: BREAKING needs 80+, strong evidence and under an hour; ALERT is a risk to people's money; JUST IN is fresh."""
    if score >= 80 and strong and age_minutes <= 60:
        return brief.BREAKING
    if risk and age_minutes <= 180:
        return brief.ALERT
    if age_minutes <= 90:
        return brief.JUST_IN
    return ""


__all__ = ["Score", "label_for", "liquidations", "market", "news"]


def describe(parts: dict[str, Any]) -> str:
    return Score(0, dict(parts)).explain()
