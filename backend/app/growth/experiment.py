"""The 7-day organic growth experiment (owner, 2026-11-27): 2-3 approved Original posts a day, posted by hand, measured against the News Desk.

The question: which kind of Original wins on X, and do Originals beat generic news? Formats are compared at the family level (five families),
because with 14-21 posts in a week each detector alone would rarely reach the 3 measured posts a comparison needs.

Rules that keep the comparison fair:
- Every post carries its graphic (pictures are held constant; the picture effect is measured on the News Desk already).
- Posting slots rotate: a family is not always posted at the same hour (DAILY_SLOTS, and the slate prefers families not yet posted in the
  day's next slot).
- The News Desk runs as usual: it is the control.
- Nothing here posts. The slate is a suggestion in the approval queue; the owner approves and posts each one.

The decision after the week: a family "wins" with at least MIN_N measured posts and an impressions and engagement lift above 1 vs all posts;
G4 (a learner) waits for MEANINGFUL: at least 15 measured Originals and 2 families with MIN_N each.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.growth import editorial

START = date(2026, 11, 28)
DAYS = 7
PER_DAY = (2, 3)
DAILY_SLOTS = ("13:30", "17:00", "21:00")  # UTC: US morning, US afternoon / EU evening, US evening
MIN_N = 3
MEANINGFUL = {"measured_originals": 15, "families_with_min_n": 2}

FAMILY: dict[str, str] = {
    "weekly_streak": "Records & streaks", "streak": "Records & streaks", "rarity": "Records & streaks", "record": "Records & streaks",
    "volatility_regime": "Records & streaks",
    "cross_asset": "Cross-market", "btc_in_gold": "Cross-market", "gold_ratio": "Cross-market", "ratio_record": "Cross-market",
    "sentiment": "Sentiment & history", "fear_aftermath": "Sentiment & history", "then_now": "Sentiment & history",
    "drawdown_table": "Rankings", "leaderboard": "Rankings", "breadth_count": "Rankings", "breadth_ma": "Rankings", "divergence": "Rankings",
    "year_paths": "Calendar comparisons", "seasonality": "Calendar comparisons", "drawdown_cycle": "Calendar comparisons",
}
FAMILIES = ("Records & streaks", "Cross-market", "Sentiment & history", "Rankings", "Calendar comparisons")


def family(detector: str) -> str:
    return FAMILY.get(detector, "Other")


def window(now: datetime) -> dict[str, Any]:
    day = now.astimezone(timezone.utc).date()
    k = (day - START).days
    return {"start": START.isoformat(), "end": (START + timedelta(days=DAYS - 1)).isoformat(), "days": DAYS,
            "day": k + 1 if 0 <= k < DAYS else None, "state": "not started" if k < 0 else "running" if k < DAYS else "finished",
            "per_day": list(PER_DAY), "slots_utc": list(DAILY_SLOTS)}


def slate(views: list[dict[str, Any]], generator: str, posted_by_family: dict[str, int], lift: dict[str, float] | None = None,
          n: int = PER_DAY[1]) -> list[dict[str, Any]]:
    """Today's suggested posts: from the editor's shortlist, one per family, families with the fewest posts so far first (so each family
    reaches MIN_N during the week), then the editor's score. [{id, family, slot, editorial}]"""
    top = editorial.shortlist(views, generator, lift, top=len(views))
    by_id = {v["id"]: v for v in views}
    seen: set[str] = set()
    picks = []
    for t in sorted(top, key=lambda t: (posted_by_family.get(family(str(by_id[t["id"]]["detector"])), 0), t["rank"])):
        fam = family(str(by_id[t["id"]]["detector"]))
        if fam in seen:
            continue
        seen.add(fam)
        picks.append({"id": t["id"], "family": fam, "editorial": t["editorial"]})
        if len(picks) >= n:
            break
    for k, p in enumerate(picks):
        p["slot"] = DAILY_SLOTS[k % len(DAILY_SLOTS)]
    return picks


def summary(rep: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The experiment's progress from a performance.report: posts per day against 2-3, posts and measurements per family, and whether the
    data is meaningful enough for G4."""
    from app.growth.performance import _lift, _summary

    w = window(now)
    start = datetime(START.year, START.month, START.day, tzinfo=timezone.utc)
    end = start + timedelta(days=DAYS)
    posts = [p for p in rep.get("all_posts", []) if start <= datetime.fromisoformat(p["published_at"]) < end]
    originals = [p for p in posts if p["group"] == "original"]
    news = [p for p in posts if p["group"] == "news"]
    per_day: dict[str, int] = {(START + timedelta(days=i)).isoformat(): 0 for i in range(DAYS)}
    for p in originals:
        d = datetime.fromisoformat(p["published_at"]).date().isoformat()
        per_day[d] = per_day.get(d, 0) + 1
    overall = _summary(posts) if posts else _summary([])
    fams = []
    for f in FAMILIES:
        rows = [p for p in originals if p.get("family") == f]
        s = _summary(rows)
        fams.append({"family": f, **s, **_lift(s, overall), "enough": s["measured_24h"] >= MIN_N})
    measured = sum(1 for p in originals if p["at_24h"])
    ready = measured >= MEANINGFUL["measured_originals"] and sum(1 for f in fams if f["enough"]) >= MEANINGFUL["families_with_min_n"]
    winners = sorted([f for f in fams if f["enough"] and (f["impressions_lift"] or 0) > 1 and (f["engagement_lift"] or 0) > 1],
                     key=lambda f: -(f["score"] or 0))
    return {**w, "posted_per_day": per_day, "originals": _summary(originals), "news": _summary(news), "families": fams,
            "winners": [f["family"] for f in winners], "measured_originals": measured, "g4_ready": ready, "g4_needs": MEANINGFUL}
