"""The editor's shortlist: the five strongest pending Originals, ranked by the owner's criteria (2026-11-27): shareability, originality,
visual quality and relevance to a broad financial audience (not just crypto traders).

The first three are priors per detector (how the format performed in the owner's G2 gallery review); relevance comes from who the subject
speaks to (markets and Bitcoin first, then Ether, then other major coins). When G3 has measured enough posted Originals of a format
(performance.format_lift, at least MIN_MEASURED posts), the measured engagement replaces the shareability prior. Only drafts that could be
approved now are eligible: current generator, data verified, figures gate clean. At most one per detector and one per story.
"""

from __future__ import annotations

from typing import Any

# detector: (shareability, originality, visual quality), 1-5 each
PRIORS: dict[str, tuple[int, int, int]] = {
    "weekly_streak": (5, 5, 5), "cross_asset": (5, 4, 5), "fear_aftermath": (4, 5, 4), "btc_in_gold": (4, 5, 4), "drawdown_table": (4, 4, 5),
    "year_paths": (4, 4, 4), "sentiment": (4, 3, 4), "then_now": (4, 3, 3), "breadth_count": (3, 4, 4), "seasonality": (3, 3, 4),
    "rarity": (3, 3, 3), "streak": (3, 3, 3), "gold_ratio": (3, 4, 3), "volatility_regime": (2, 3, 3), "leaderboard": (3, 2, 4),
    "divergence": (2, 3, 3), "volume_spike": (2, 2, 3), "record": (4, 3, 3), "drawdown_cycle": (3, 4, 4), "ratio_record": (3, 3, 3),
}
# subject: relevance to a broad financial audience, 1-5
AUDIENCE: dict[str, int] = {"cross-asset": 5, "BTC-gold": 5, "BTC": 5, "FNG-BTC": 5, "FNG": 4, "drawdown-majors": 4, "ETH": 3}
DEFAULT_PRIOR = (2, 2, 2)
DEFAULT_AUDIENCE = 2
TOP = 5
MIN_MEASURED = 3


def story(d: dict[str, Any]) -> str:
    """Two drafts about the same reading are one story (Fear & Greed 11 as a rarity and as an aftermath)."""
    subj = str(d.get("subject") or (d.get("finding") or {}).get("subject") or "")
    return "fng" if subj.startswith("FNG") else f"{d.get('detector')}:{subj}"


def score(d: dict[str, Any], lift: dict[str, float] | None = None) -> dict[str, Any]:
    """{total (max 20), shareability, originality, visual, relevance, measured: bool} for one draft view (drafts.view + finding)."""
    share, orig, visual = PRIORS.get(str(d.get("detector")), DEFAULT_PRIOR)
    measured = False
    if lift and str(d.get("detector")) in lift:  # measured: engagement lift vs all Price Chat posts, 1.0 = average -> 3 points
        share = max(1, min(5, round(3 * lift[str(d["detector"])])))
        measured = True
    subj = str(d.get("subject") or (d.get("finding") or {}).get("subject") or "")
    rel = AUDIENCE.get(subj, DEFAULT_AUDIENCE)
    return {"total": share + orig + visual + rel, "shareability": share, "originality": orig, "visual": visual, "relevance": rel,
            "measured": measured}


def eligible(d: dict[str, Any], generator: str) -> bool:
    v = d.get("verification") or {}
    return (d.get("status") == "pending" and d.get("generator") == generator and bool(v.get("ok")) and not d.get("gate_problems")
            and not d.get("writer_pending"))


def shortlist(views: list[dict[str, Any]], generator: str, lift: dict[str, float] | None = None, top: int = TOP) -> list[dict[str, Any]]:
    """[{id, rank, editorial: {...}}] best first. views: drafts.view() dicts with "subject" (the finding's)."""
    ranked = sorted((v for v in views if eligible(v, generator)), key=lambda v: (-score(v, lift)["total"], -float(v.get("score") or 0)))
    out: list[dict[str, Any]] = []
    seen_det: set[str] = set()
    seen_story: set[str] = set()
    for v in ranked:
        if v.get("detector") in seen_det or story(v) in seen_story:
            continue
        seen_det.add(str(v.get("detector")))
        seen_story.add(story(v))
        out.append({"id": v["id"], "rank": len(out) + 1, "editorial": score(v, lift)})
        if len(out) >= top:
            break
    return out
