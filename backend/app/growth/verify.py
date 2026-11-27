"""G2 source verification: before a finding becomes a draft, every stored input it was computed from is read again from its source and
must match (owner, 2026-11-27: "using verified data"). A draft whose inputs can't be re-read, or differ, is not queued.

What is re-read, per series key the finding lists:
- px:COIN:close / px:COIN:vol: Hyperliquid daily candles (candleSnapshot). One or two coins: the whole stored history (rarity claims are
  measured over all of it). Many coins (rankings): the last RECENT_DAYS, which covers every window a ranking uses (7 days to year to date).
- fund:COIN: Hyperliquid fundingHistory for the last FUNDING_DAYS days, averaged per day exactly as the collector does.
- fng: alternative.me's full Fear & Greed history.
- breadth:*: derived from stored closes; recomputed from the stored closes (the derivation) and a sample of those closes re-read from
  Hyperliquid (the inputs).
Nothing here writes to the database. Network errors fail the check (the hourly job tries again).
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.growth import collectors
from app.growth.findings import Finding
from app.growth.memory import Memory

log = logging.getLogger(__name__)

REL_TOL = 1e-4  # 0.01%: the same source must give the same number
FUND_TOL = 1e-9  # absolute, per hourly rate
RECENT_DAYS = 400
FULL_HISTORY_MAX_COINS = 3
FUNDING_DAYS = 7
BREADTH_SAMPLE = 12


class Verifier:
    """One verification run: caches what it fetched, so several findings on the same coin cost one request."""

    def __init__(self, mem: Memory, http: collectors.Http | None = None, now: datetime | None = None) -> None:
        self.mem = mem
        self.http = http or collectors.Http(retries=2)
        self.now = now or datetime.now(timezone.utc)
        self._candles: dict[tuple[str, date], dict[date, tuple[float, float]]] = {}
        self._fng: dict[date, float] | None = None
        self._fund: dict[str, dict[date, float]] = {}

    # --- fetchers (cached) ---------------------------------------------------------------------------------------------------------

    def candles(self, coin: str, start: date, end: date) -> dict[date, tuple[float, float]]:
        for (c, s), rows in self._candles.items():
            if c == coin and s <= start:
                return rows
        rows = {d: (close, vol) for d, close, vol in collectors.hl_daily_candles(self.http, coin, start, end)}
        self._candles[(coin, start)] = rows
        return rows

    def fng(self) -> dict[date, float]:
        if self._fng is None:
            self._fng = dict(collectors.fng_history(self.http, 0))
        return self._fng

    def funding(self, coin: str, start: date, end: date) -> dict[date, float]:
        if coin not in self._fund:
            self._fund[coin] = dict(collectors.hl_funding_daily(self.http, coin, start, end))
        return self._fund[coin]

    # --- checks --------------------------------------------------------------------------------------------------------------------

    def _px(self, key: str, as_of: date, many: bool, out: dict[str, Any]) -> list[str]:
        _, coin, field = key.split(":", 2)
        coin = collectors.XYZ_COIN.get(coin, coin)  # px:XYZ-GOLD:close is read from Hyperliquid as xyz:GOLD
        s = self.mem.get(key)
        if s is None:
            return [f"{key}: not stored"]
        s = s.upto(as_of)
        start = s.first if not many else max(s.first, as_of - timedelta(days=RECENT_DAYS))
        live = self.candles(coin, start, as_of + timedelta(days=1))
        idx = 0 if field == "close" else 1
        checked, worst, problems = 0, 0.0, []
        for d, v in zip(s.dates, s.values):
            if d < start:
                continue
            got = live.get(d)
            if got is None:
                problems.append(f"{key} {d.isoformat()}: Hyperliquid has no candle")
                continue
            ref = got[idx]
            diff = abs(v - ref) / abs(ref) if ref else (0.0 if v == 0 else 1.0)
            worst = max(worst, diff)
            checked += 1
            if diff > (REL_TOL if field == "close" else 0.02):  # volume: the USD notional is rebuilt from base volume × close
                problems.append(f"{key} {d.isoformat()}: stored {v:g} vs Hyperliquid {ref:g}")
        if checked == 0:
            problems.append(f"{key}: nothing to compare")
        out[key] = {"checked": checked, "from": start.isoformat(), "max_diff_pct": round(worst * 100, 6), "source": "Hyperliquid candleSnapshot"}
        return problems[:5]

    def _fund_check(self, key: str, as_of: date, out: dict[str, Any]) -> list[str]:
        coin = key.split(":", 1)[1]
        s = self.mem.get(key)
        if s is None:
            return [f"{key}: not stored"]
        start = as_of - timedelta(days=FUNDING_DAYS - 1)
        live = self.funding(coin, start, as_of + timedelta(days=1))
        checked, problems = 0, []
        for d, v in zip(s.dates, s.values):
            if d < start or d > as_of:
                continue
            ref = live.get(d)
            if ref is None or abs(v - ref) > FUND_TOL:
                problems.append(f"{key} {d.isoformat()}: stored {v:.8f} vs Hyperliquid {ref if ref is None else f'{ref:.8f}'}")
            checked += 1
        out[key] = {"checked": checked, "from": start.isoformat(), "source": "Hyperliquid fundingHistory"}
        return problems[:5] or ([] if checked else [f"{key}: nothing to compare"])

    def _fng_check(self, as_of: date, out: dict[str, Any]) -> list[str]:
        s = self.mem.get("fng")
        if s is None:
            return ["fng: not stored"]
        live = self.fng()
        checked, problems = 0, []
        for d, v in zip(s.dates, s.values):
            if d > as_of:
                continue
            ref = live.get(d)
            if ref is None or abs(v - ref) > 0.5:
                problems.append(f"fng {d.isoformat()}: stored {v:g} vs alternative.me {ref}")
            checked += 1
        out["fng"] = {"checked": checked, "source": "alternative.me"}
        return problems[:5]

    def _breadth_check(self, key: str, as_of: date, out: dict[str, Any]) -> list[str]:
        if key in out:
            return []
        closes = {k[3:-6]: list(zip(s.dates, s.values)) for k in self.mem.keys("px:") if k.endswith(":close") and (s := self.mem.get(k))}
        derived = collectors.compute_breadth(closes, since=as_of - timedelta(days=3))
        s = self.mem.get(key)
        mine = dict(derived.get(key, []))
        stored = s.value(as_of) if s else None
        problems = []
        if stored is None or as_of not in mine or abs(mine[as_of] - stored) > 1e-9:
            problems.append(f"{key} {as_of.isoformat()}: stored {stored} vs recomputed {mine.get(as_of)}")
        # the inputs: a sample of the closes it was computed from, re-read from Hyperliquid
        coins = sorted(closes)
        rng = random.Random(f"{key}:{as_of.isoformat()}")
        sample = rng.sample(coins, min(BREADTH_SAMPLE, len(coins)))
        sub: dict[str, Any] = {}
        for c in sample:
            problems += self._px(f"px:{c}:close", as_of, True, sub)
        out[key] = {"recomputed": stored is not None and not problems, "sampled_closes": sorted(sample),
                    "checked": sum(v["checked"] for v in sub.values()), "source": "derived from Hyperliquid closes"}
        return problems[:5]

    def _universe_check(self, as_of: date, out: dict[str, Any]) -> list[str]:
        coins = sorted(k[3:-6] for k in self.mem.keys("px:") if k.endswith(":close"))
        rng = random.Random(f"universe:{as_of.isoformat()}")
        sample = rng.sample(coins, min(BREADTH_SAMPLE, len(coins)))
        sub: dict[str, Any] = {}
        problems: list[str] = []
        for c in sample:
            problems += self._px(f"px:{c}:close", as_of, True, sub)
        out["px:*:close"] = {"sampled_closes": sorted(sample), "checked": sum(v["checked"] for v in sub.values()),
                             "source": "Hyperliquid candleSnapshot (sample)"}
        return problems[:5]

    def check(self, f: Finding) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        problems: list[str] = []
        keys = list(dict.fromkeys(f.series))
        px_coins = {k.split(":")[1] for k in keys if k.startswith("px:")}
        many = len(px_coins) > FULL_HISTORY_MAX_COINS
        for key in keys:
            try:
                if key == "px:*:close":  # a whole-universe count (breadth_count): a sample of the stored closes, re-read
                    problems += self._universe_check(f.as_of, detail)
                elif key.startswith("px:"):
                    problems += self._px(key, f.as_of, many, detail)
                elif key.startswith("fund:"):
                    problems += self._fund_check(key, f.as_of, detail)
                elif key == "fng":
                    problems += self._fng_check(f.as_of, detail)
                elif key.startswith("breadth:"):
                    problems += self._breadth_check(key, f.as_of, detail)
                else:
                    problems.append(f"{key}: no verifier for this series")
            except collectors.CollectError as e:
                problems.append(f"{key}: the source couldn't be read ({e})")
            except Exception as e:  # noqa: BLE001 - a verifier bug refuses the draft, never queues it unchecked
                log.exception("growth verify %s failed", key)
                problems.append(f"{key}: {type(e).__name__}")
        values = sum(int(v.get("checked") or 0) for v in detail.values())
        return {"ok": not problems and bool(keys), "at": self.now.isoformat(timespec="seconds"), "values_checked": values,
                "series": detail, "problems": problems[:12]}
