"""What our own audience engaged with lately, as a small adjustment to the virality score (-8 to +8 points).

From X's own metrics for the desk's published posts (collected once a day by engine.collect_metrics): engagements per impression
(likes, reposts x2, replies, quotes, bookmarks) for each kind of story, against the account's average. A kind with few posts is pulled
towards the average, so one lucky post changes little. Telegram's Bot API gives no view counts for channel posts, so Telegram can't feed
this yet; both platforms still use the result, since they post the same stories.

Nothing is adjusted until there are at least MIN_POSTS measured posts: with no data the score is the plain virality.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdPost

DAYS = 21
MIN_POSTS = 8
PRIOR = 5  # pseudo-posts at the average for each kind
SPAN = 8.0  # points at twice (or half) the average rate


def rate(metrics: dict[str, Any]) -> float | None:
    seen = float(metrics.get("impression_count") or 0)
    if seen < 50:
        return None
    acts = sum(float(metrics.get(k) or 0) for k in ("like_count", "reply_count", "quote_count", "bookmark_count")) + 2 * float(metrics.get("retweet_count") or 0)
    return acts / seen


def kind_of(facts: dict[str, Any]) -> str:
    return str(facts.get("topic") or ("move" if facts.get("symbol") else "other"))


def _measured(db: Session, now: datetime) -> list[tuple[dict[str, Any], float]]:
    rows = db.scalars(select(NdPost).where(NdPost.status == "published", NdPost.published_at >= now - timedelta(days=DAYS)))
    return [(p.facts or {}, r) for p in rows if (r := rate(p.metrics or {})) is not None]


def _points(by: dict[str, list[float]], mean: float) -> dict[str, float]:
    out = {}
    for kind, rs in by.items():
        shrunk = (sum(rs) + PRIOR * mean) / (len(rs) + PRIOR)
        ratio = shrunk / mean  # 1.0 is average
        points = SPAN * (ratio - 1) if ratio >= 1 else -SPAN * (1 / ratio - 1)
        out[kind] = round(max(-SPAN, min(SPAN, points)), 1)
    return out


def adjustments(db: Session, now: datetime) -> dict[str, float]:
    """{topic: points, "hook:<hook>": points}. Empty until there is enough measured data. The hook points steer which of a story's
    factual hooks wins (app/newsdesk/viral.hook_score), never whether a story is posted."""
    measured = _measured(db, now)
    if len(measured) < MIN_POSTS:
        return {}
    mean = sum(r for _, r in measured) / len(measured)
    if mean <= 0:
        return {}
    by: dict[str, list[float]] = {}
    hooks: dict[str, list[float]] = {}
    for facts, r in measured:
        by.setdefault(kind_of(facts), []).append(r)
        hook = str((facts.get("viral") or {}).get("hook") or "")
        if hook:
            hooks.setdefault(hook, []).append(r)
    return _points(by, mean) | {f"hook:{k}": v for k, v in _points(hooks, mean).items()}


# --- Viral Engine 2.0: what each feature of a post did, and the experiments ---------------------------------------------------------

FEATURES = ("hook", "format", "question", "links", "writer")
MIN_ARM = 5  # posts per arm before an experiment's result is called


def features(ev: Any) -> dict[str, Any]:
    """What a post was made of, copied onto the X post (NdPost.facts["viral"]) so its metrics can be attributed."""
    f = ev.facts or {}
    v, pkg = f.get("viral") or {}, f.get("package") or {}
    exp = v.get("experiment") or {}
    return {"hook": v.get("hook") or "", "exploring": bool(exp.get("exploring")), "control": exp.get("control") or "",
            "format": str((f.get("visual") or {}).get("kind") or ""), "question": bool((pkg.get("copy") or {}).get("question")),
            "links": bool(pkg.get("links")), "writer": str((pkg.get("copy") or {}).get("writer") or ""), "kind": getattr(ev, "kind", "")}


def report(db: Session, now: datetime) -> dict[str, Any]:
    """Admin's view: engagement per hook, format, question and links, and each experiment (runner-up hook vs the hook that would have won).
    Rates are engagements per impression on X. Telegram's Bot API gives no view counts for channel posts, so Telegram can't be measured here."""
    measured = _measured(db, now)
    mean = sum(r for _, r in measured) / len(measured) if measured else 0.0
    table: dict[str, dict[str, dict[str, float]]] = {}
    for name in FEATURES:
        by: dict[str, list[float]] = {}
        for facts, r in measured:
            v = facts.get("viral") or {}
            if name in v and v[name] not in ("", None):
                by.setdefault(str(v[name]), []).append(r)
        table[name] = {k: {"posts": len(rs), "rate": round(sum(rs) / len(rs), 5), "vs_average": round((sum(rs) / len(rs)) / mean, 2) if mean else 0.0}
                       for k, rs in sorted(by.items())}
    tests: dict[str, dict[str, list[float]]] = {}
    for facts, r in measured:
        v = facts.get("viral") or {}
        if v.get("hook") and v.get("control"):
            pair = tests.setdefault(f"{v['hook']} vs {v['control']}" if v.get("exploring") else f"{v['control']}", {"arm": [], "control": []})
            pair["arm" if v.get("exploring") else "control"].append(r)
    experiments = []
    for hook in sorted({k.split(" vs ")[0] for k in tests if " vs " in k}):
        for key, pair in tests.items():
            if not key.startswith(f"{hook} vs "):
                continue
            control = key.split(" vs ")[1]
            base = [r for facts, r in measured if (facts.get("viral") or {}).get("hook") == control and not (facts.get("viral") or {}).get("exploring")]
            arm = pair["arm"]
            done = len(arm) >= MIN_ARM and len(base) >= MIN_ARM
            lift = round((sum(arm) / len(arm)) / (sum(base) / len(base)) - 1, 3) if arm and base and sum(base) > 0 else None
            experiments.append({"arm": hook, "control": control, "arm_posts": len(arm), "control_posts": len(base), "lift": lift,
                                "verdict": ("arm better" if lift and lift > 0.1 else "control better" if lift is not None and lift < -0.1 else "no clear difference")
                                if done else f"collecting: needs {MIN_ARM} posts in each"})
    return {"days": DAYS, "measured_posts": len(measured), "average_rate": round(mean, 5), "learning": len(measured) >= MIN_POSTS,
            "features": table, "experiments": experiments, "hook_points": {k.split(":", 1)[1]: v for k, v in adjustments(db, now).items() if k.startswith("hook:")},
            "telegram": "Telegram's Bot API gives no view counts for channel posts: X metrics drive the learning."}
