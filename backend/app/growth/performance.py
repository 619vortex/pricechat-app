"""Growth G3: performance intelligence. How every Price Chat X post did (impressions, reposts, bookmarks, replies, likes, quotes, profile
visits, link clicks, engagement rate and an estimate of the followers it brought), Originals against the generic News Desk and Trending
posts, and which content formats win.

Nothing here reads X or spends anything. The numbers are the ones social/x_history.py already collects (a curve per post at 1 h, 6 h,
24 h, 72 h and 7 d inside its own $0.50 a day cap, and the account's follower count once an hour). Originals are posted by hand, so a
posted Original is linked to its X post either automatically (the own-timeline reader x.own stores every Price Chat post; the text is
matched against approved drafts) or by the owner pasting the post's link. The link lives in the draft's finding JSON ("posted"): no new
table or migration.

Fair comparison: posts of different ages aren't compared on their latest numbers. Each post is measured at its 24 h checkpoint (the
snapshot closest to 24 h, 18-36 h after posting); a post without one counts in the post list but not in the medians.

Follower attribution: X does not say which post brought a follower. The estimate is the account's follower change over the 24 h after the
post, split between the posts published in that window by their 24 h impressions. It is labelled an estimate everywhere it is shown.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.market_memory import GrDraft

WINDOW_H = 24
NEAR_24H = (18.0, 36.0)
MIN_N = 3  # a format needs this many measured posts before it can be called a winner
MATCH_MIN = 0.72  # text similarity for an automatic link (after links, whitespace and case are normalised)
ENGAGEMENT = ("likes", "replies", "reposts", "quotes", "bookmarks")
GROUP_LABEL = {"original": "Originals", "news": "News Desk", "trend": "Trending Topics", "other": "Other posts"}
_URL = re.compile(r"https?://\S+")
_STATUS = re.compile(r"(?:x|twitter)\.com/(?:[A-Za-z0-9_]+|i(?:/web)?)/status(?:es)?/(\d{5,25})|^(\d{5,25})$")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _URL.sub("", text or "")).strip().lower()


# --- linking posted Originals ----------------------------------------------------------------------------------------------------


def parse_post_id(url_or_id: str) -> str | None:
    """The X post id from a post link (x.com/.../status/123, twitter.com, x.com/i/web/status/123) or a bare id."""
    m = _STATUS.search((url_or_id or "").strip())
    return (m.group(1) or m.group(2)) if m else None


def posted_of(d: GrDraft) -> dict[str, Any] | None:
    p = (d.finding or {}).get("posted")
    return p if isinstance(p, dict) and p.get("x_id") else None


def _set_posted(d: GrDraft, x_id: str, how: str, at: datetime | None, similarity: float | None = None) -> None:
    d.finding = {**(d.finding or {}), "posted": {"x_id": x_id, "url": f"https://x.com/i/status/{x_id}", "at": at.isoformat() if at else None,
                                                 "how": how, **({"similarity": round(similarity, 3)} if similarity is not None else {})}}


def link(db: Session, draft_id: int, url_or_id: str, now: datetime | None = None) -> GrDraft:
    """The owner says which X post an approved draft became."""
    from app.growth.drafts import DraftError
    from app.models.enums import Platform
    from app.models.social import SocialPost

    d = db.get(GrDraft, draft_id)
    if d is None:
        raise DraftError(404, "No such draft.")
    if d.status != "approved":
        raise DraftError(409, "Approve the draft first: only an approved Original can be linked to its X post.")
    x_id = parse_post_id(url_or_id)
    if not x_id:
        raise DraftError(422, "That isn't an X post link (x.com/<account>/status/<id>) or a post id.")
    other = next((o for o in _linked(db) if posted_of(o)["x_id"] == x_id and o.id != d.id), None)  # type: ignore[index]
    if other is not None:
        raise DraftError(409, f"That post is already linked to draft #{other.id}.")
    sp = db.scalar(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id == x_id))
    _set_posted(d, x_id, "owner", _aware(sp.posted_at) if sp is not None else (now or utcnow()))
    db.commit()
    return d


def unlink(db: Session, draft_id: int) -> GrDraft:
    from app.growth.drafts import DraftError

    d = db.get(GrDraft, draft_id)
    if d is None:
        raise DraftError(404, "No such draft.")
    d.finding = {k: v for k, v in (d.finding or {}).items() if k != "posted"}
    db.commit()
    return d


def _linked(db: Session) -> list[GrDraft]:
    return [d for d in db.scalars(select(GrDraft).where(GrDraft.status == "approved")) if posted_of(d)]


def auto_link(db: Session, now: datetime | None = None, days: int = 30) -> list[int]:
    """Approved drafts with no X post yet, matched to Price Chat's own X posts (stored by x.own) by their text. One post per draft; the best
    match wins; a post already linked (or posted by the News Desk / Trending lanes) is never taken. Returns the draft ids linked."""
    from app.models.enums import Platform
    from app.models.newsdesk import NdPost
    from app.models.social import SocialPost
    from app.models.trending import TtPost

    now = _aware(now) or utcnow()
    todo = [d for d in db.scalars(select(GrDraft).where(GrDraft.status == "approved")) if not posted_of(d)]
    if not todo:
        return []
    taken = {posted_of(d)["x_id"] for d in _linked(db)}  # type: ignore[index]
    taken |= {str(x) for (x,) in db.execute(select(NdPost.x_id).where(NdPost.x_id.is_not(None)))}
    taken |= {str(x) for (x,) in db.execute(select(TtPost.x_id).where(TtPost.x_id.is_not(None)))}
    since = now - timedelta(days=days)
    posts = [p for p in db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                                                            SocialPost.external_id.is_not(None), SocialPost.posted_at >= since))
             if str(p.external_id) not in taken and not any((r or {}).get("type") == "retweeted" for r in p.referenced or [])]
    out: list[int] = []
    for d in sorted(todo, key=lambda d: _aware(d.decided_at) or now):
        made = _aware(d.created_at) or since
        best, best_r = None, 0.0
        for p in posts:
            if str(p.external_id) in taken or (_aware(p.posted_at) or now) < made - timedelta(hours=1):
                continue
            r = SequenceMatcher(None, _norm(d.text), _norm(p.text or "")).ratio()
            if r > best_r:
                best, best_r = p, r
        if best is not None and best_r >= MATCH_MIN:
            _set_posted(d, str(best.external_id), "matched", _aware(best.posted_at), best_r)
            taken.add(str(best.external_id))
            out.append(d.id)
    if out:
        db.commit()
    return out


# --- measuring -------------------------------------------------------------------------------------------------------------------


def _engagement(m: dict[str, Any]) -> int | None:
    vals = [m.get(k) for k in ENGAGEMENT]
    return None if all(v is None for v in vals) else int(sum(int(v or 0) for v in vals))


def _measure(m: dict[str, Any]) -> dict[str, Any]:
    imp = m.get("impressions")
    eng = _engagement(m)
    out = {k: m.get(k) for k in ("impressions", "likes", "replies", "reposts", "quotes", "bookmarks", "profile_visits", "url_clicks")}
    out["engagement"] = eng
    out["engagement_rate"] = round(eng / imp, 5) if eng is not None and imp else None
    return out


def _at_24h(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    near = [p for p in points if p.get("age_hours") is not None and NEAR_24H[0] <= p["age_hours"] <= NEAR_24H[1] and p.get("impressions")]
    if not near:
        return None
    return min(near, key=lambda p: (p.get("checkpoint") != "24h", abs(p["age_hours"] - 24)))


def _latest(row: dict[str, Any]) -> dict[str, Any]:
    pts = [p for p in row.get("points") or [] if p.get("impressions") is not None]
    if pts:
        return pts[-1]
    lat = row.get("latest") or {}
    return {k: lat.get(k) for k in ("impressions", "likes", "replies", "reposts", "quotes", "bookmarks", "profile_visits", "url_clicks")}


def _followers(db: Session, since: datetime) -> list[tuple[datetime, int]]:
    from app.models.social_intel import GrowthSnapshot

    rows = db.execute(select(GrowthSnapshot.taken_at, GrowthSnapshot.followers).where(
        GrowthSnapshot.kind == "x_account", GrowthSnapshot.followers.is_not(None), GrowthSnapshot.taken_at >= since - timedelta(days=2))
        .order_by(GrowthSnapshot.taken_at))
    return [(_aware(t), int(f)) for t, f in rows]  # type: ignore[misc]


def _follower_change(snaps: list[tuple[datetime, int]], start: datetime, end: datetime) -> int | None:
    """Followers at the last snapshot at or before `end` (within 3 h of it) minus the last one at or before `start` (within 3 h)."""
    before = [f for t, f in snaps if start - timedelta(hours=3) <= t <= start]
    after = [f for t, f in snaps if end - timedelta(hours=3) <= t <= end]
    if not before or not after:
        return None
    return after[-1] - before[-1]


def _fmt_of(group: str, features: dict[str, Any], draft: GrDraft | None) -> str:
    from app.growth.graphics import DETECTOR_LABEL

    if group == "original" and draft is not None:
        return DETECTOR_LABEL.get(draft.detector, draft.detector)
    if group == "news":
        kind = features.get("kind") or "news"
        return "Market move" if kind == "market" else ("Breaking news" if features.get("priority") in ("breaking", "urgent") else "News")
    if group == "trend":
        return "Trending topic"
    return "Reply" if (features.get("text") or "").lstrip().startswith("@") else "Other post"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [r for r in rows if r["at_24h"] is not None]
    imps = [r["at_24h"]["impressions"] for r in measured if r["at_24h"].get("impressions")]
    rates = [r["at_24h"]["engagement_rate"] for r in measured if r["at_24h"].get("engagement_rate") is not None]
    est = [r["followers_est"] for r in measured if r["followers_est"] is not None]
    total_imp = sum(imps)
    total_eng = sum(int(r["at_24h"].get("engagement") or 0) for r in measured if r["at_24h"].get("impressions"))

    def tot(k: str) -> int:
        return int(sum(int((r["latest"].get(k) or 0)) for r in rows))

    return {"posts": len(rows), "measured_24h": len(measured), "median_impressions_24h": statistics.median(imps) if imps else None,
            "median_engagement_rate_24h": round(statistics.median(rates), 5) if rates else None,
            # pooled: all engagements / all impressions at 24 h (most small-account posts have none, so the median is often 0)
            "engagement_rate_24h": round(total_eng / total_imp, 5) if total_imp else None,
            "reposts": tot("reposts"), "bookmarks": tot("bookmarks"), "replies": tot("replies"), "likes": tot("likes"), "quotes": tot("quotes"),
            "profile_visits": tot("profile_visits"), "impressions": tot("impressions"),
            "followers_est_24h": round(sum(est), 1) if est else None,
            "followers_per_1k_impressions": round(sum(est) / total_imp * 1000, 2) if est and total_imp else None}


def report(db: Session, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """Everything the Originals dashboard's Performance panel shows. No X reads: stored snapshots only."""
    from app.social import x_history

    now = _aware(now) or utcnow()
    auto_link(db, now)
    curves = x_history.curves(db, days=days, now=now)
    linked = {posted_of(d)["x_id"]: d for d in _linked(db)}  # type: ignore[index]
    snaps = _followers(db, now - timedelta(days=days))
    posts: list[dict[str, Any]] = []
    for c in curves:
        draft = linked.get(c["x_id"])
        lane = c["features"].get("lane")
        group = "original" if draft is not None else {"newsdesk": "news", "trending": "trend"}.get(str(lane), "other")
        pt = _at_24h(c.get("points") or [])
        posts.append({"x_id": c["x_id"], "url": c["url"], "published_at": c["published_at"], "text": (c.get("text") or "")[:280], "group": group,
                      "format": _fmt_of(group, c["features"] | {"text": c.get("text") or ""}, draft),
                      "family": _family(draft.detector) if draft is not None else None, "draft_id": draft.id if draft is not None else None,
                      "has_image": c["features"].get("has_image"), "at_24h": _measure(pt) if pt else None, "latest": _measure(_latest(c)),
                      "followers_est": None, "followers_window": None})
    # follower attribution: the change over each post's first 24 h, split by 24 h impressions among the posts published in that window
    by_time = sorted(((datetime.fromisoformat(p["published_at"]), p) for p in posts), key=lambda x: x[0])
    for t, p in by_time:
        if p["at_24h"] is None or now - t < timedelta(hours=WINDOW_H):
            continue
        change = _follower_change(snaps, t, t + timedelta(hours=WINDOW_H))
        if change is None:
            continue
        peers = [q for u, q in by_time if t <= u < t + timedelta(hours=WINDOW_H)]
        imp_all = sum(int((q["at_24h"] or {}).get("impressions") or 0) for q in peers)
        mine = int(p["at_24h"].get("impressions") or 0)
        p["followers_window"] = {"change": change, "posts_in_window": len(peers)}
        p["followers_est"] = round(change * mine / imp_all, 2) if imp_all else None
    groups = {g: _summary([p for p in posts if p["group"] == g]) | {"label": GROUP_LABEL[g]} for g in GROUP_LABEL}
    overall = _summary(posts)
    formats = []
    for (g, f) in sorted({(p["group"], p["format"]) for p in posts}):
        s = _summary([p for p in posts if p["group"] == g and p["format"] == f])
        formats.append({"group": g, "group_label": GROUP_LABEL[g], "format": f, **s, **_lift(s, overall)})
    ranked = [f for f in formats if f["measured_24h"] >= MIN_N and f["score"] is not None]
    ranked.sort(key=lambda f: -f["score"])
    for k, f in enumerate(ranked):
        f["rank"] = k + 1
    images = {label: _summary([p for p in posts if p["has_image"] is val]) for label, val in (("with picture", True), ("no picture", False))}
    originals = groups["original"]
    news = groups["news"]
    return {"days": days, "as_of": now.isoformat(), "overall": overall, "groups": groups, "formats": formats,
            "winners": [f for f in ranked[:3]], "pictures": images,
            "originals_vs_news": _vs(originals, news),
            "originals": [p for p in posts if p["group"] == "original"],
            "top_posts": sorted([p for p in posts if p["at_24h"] and p["at_24h"].get("engagement_rate") is not None],
                                key=lambda p: -p["at_24h"]["engagement_rate"])[:10],
            "followers_now": snaps[-1][1] if snaps else None,
            "experiment": _experiment({"all_posts": posts}, now),
            "notes": _notes(originals, posts),
            "method": {"window_hours": WINDOW_H, "measured_between_hours": list(NEAR_24H), "min_posts_for_a_winner": MIN_N, "ranking": "the geometric mean of the impressions lift (median at 24 h vs all posts) and the engagement lift (pooled rate at 24 h vs all posts)",
                       "engagement": " + ".join(ENGAGEMENT), "followers": "estimate: the account's follower change in the 24 h after a post, split "
                       "by 24 h impressions among the posts published in that window (X does not say which post brought a follower)",
                       "cost": "no X reads: the snapshots newsdesk.x_history already takes inside its own daily cap"}}


def _family(detector: str) -> str:
    from app.growth.experiment import family

    return family(detector)


def _experiment(rep: dict[str, Any], now: datetime) -> dict[str, Any]:
    from app.growth import experiment

    return experiment.summary(rep, now)


def posted_by_family(db: Session, start: datetime, end: datetime) -> dict[str, int]:
    """Originals posted between start and end, per experiment family (for the daily slate). No X reads."""
    out: dict[str, int] = {}
    for d in _linked(db):
        at = (posted_of(d) or {}).get("at")
        if at and start <= datetime.fromisoformat(at) < end:
            f = _family(d.detector)
            out[f] = out.get(f, 0) + 1
    return out


def _lift(s: dict[str, Any], overall: dict[str, Any]) -> dict[str, Any]:
    ir, oi = s["median_impressions_24h"], overall["median_impressions_24h"]
    er, oe = s["engagement_rate_24h"], overall["engagement_rate_24h"]
    il = round(ir / oi, 3) if ir is not None and oi else None
    el = round(er / oe, 3) if er is not None and oe else None
    score = round((il * el) ** 0.5, 3) if il is not None and el is not None and il >= 0 and el >= 0 else None
    return {"impressions_lift": il, "engagement_lift": el, "score": score}


def _vs(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("median_impressions_24h", "engagement_rate_24h", "followers_per_1k_impressions"):
        x, y = a.get(k), b.get(k)
        out[k] = {"originals": x, "news": y, "ratio": round(x / y, 3) if x is not None and y else None}
    out["enough_data"] = a["measured_24h"] >= MIN_N and b["measured_24h"] >= MIN_N
    return out


def _notes(originals: dict[str, Any], posts: list[dict[str, Any]]) -> list[str]:
    out = []
    if originals["posts"] == 0:
        out.append("No Original has been posted yet. Post an approved draft from X and it is linked automatically when its text matches "
                   "(or paste the post's link on the draft); its numbers then appear here within the hour.")
    elif originals["measured_24h"] < MIN_N:
        out.append(f"{originals['measured_24h']} posted Original(s) with 24 h numbers so far: comparisons need at least {MIN_N}.")
    if not any(p["at_24h"] for p in posts):
        out.append("No post has a 24 h snapshot yet in this window.")
    return out


def format_lift(rep: dict[str, Any]) -> dict[str, float]:
    """Measured engagement lift per Original detector (for editorial.score), only for formats with MIN_N measured posts."""
    from app.growth.graphics import DETECTOR_LABEL

    by_label = {v: k for k, v in DETECTOR_LABEL.items()}
    return {by_label[f["format"]]: f["engagement_lift"] for f in rep.get("formats", [])
            if f["group"] == "original" and f["measured_24h"] >= MIN_N and f["engagement_lift"] and f["format"] in by_label}


def draft_metrics(db: Session, ds: list[GrDraft]) -> dict[int, dict[str, Any]]:
    """{draft id: latest measured numbers} for the posted drafts among `ds` (the list view's cards)."""
    from app.models.enums import Platform
    from app.models.social import SocialPost

    ids = {posted_of(d)["x_id"]: d.id for d in ds if posted_of(d)}  # type: ignore[index]
    if not ids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for p in db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.external_id.in_(list(ids)))):
        m = _measure(p.metrics or {})
        m["updated_at"] = _aware(p.metrics_updated_at).isoformat() if p.metrics_updated_at else None
        out[ids[str(p.external_id)]] = m
    return out
