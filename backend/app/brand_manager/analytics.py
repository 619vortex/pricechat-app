"""Analytics + the performance analyzer. Only numbers TikTok's API returns (video.list: views, likes, comments, shares;
user.info.stats: followers). Missing data stays missing: nothing is estimated or invented.

Matching: a direct post carries TikTok's post id. A drafts upload is posted by the creator in the TikTok app, so its
TikTok video is matched by the post id when TikTok's status reports one, else by caption similarity + time (marked
"matched by caption").

The analyzer compares categories, templates, lengths, posting hours and hooks (min. 3 videos per group) and turns that
into gentle category multipliers (0.7-1.4) for topic ranking, plus the hooks that worked best for the script writer. It also
compares videos that rode a trend word with the rest (trends.learn) and turns that into the trend boost's multiplier.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import brands, tiktok, trends
from app.brand_manager.queue import set_stage
from app.db.base import utcnow
from app.models.brand_manager import BmBrand, BmMetric, BmVideo


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


async def collect(db: Session, settings: Any, b: BmBrand, transport: Any = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    acc = tiktok.account(db, b.id)
    if acc.status != "connected" or "video.list" not in acc.scopes:
        return {"skipped": "video.list not granted"}
    out = {"videos": 0, "matched": 0}
    try:
        await tiktok.refresh_profile(db, settings, acc, transport)
        followers = (acc.profile or {}).get("follower_count")
        if followers is not None:
            db.add(BmMetric(brand_id=b.id, video_id=None, at=now, followers=int(followers)))
        items = await tiktok.list_videos(db, settings, acc, transport)
    except tiktok.TikTokError as exc:
        brands.log(db, b.id, f"Couldn't read TikTok analytics: {exc}", level="warn", stage="analytics")
        db.commit()
        return {"error": str(exc)}
    by_id = {str(i.get("id")): i for i in items if i.get("id")}
    ours = list(db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.status.in_(("published", "sent_to_drafts")))))
    for v in ours:
        item = by_id.get(v.post_id or "")
        how = "post id"
        if item is None and v.status == "sent_to_drafts":
            sent = v.updated_at - timedelta(hours=1)
            title = _words((v.script or {}).get("title") or v.topic)
            best, best_score = None, 0.0
            for i in items:
                created = datetime.fromtimestamp(int(i.get("create_time") or 0), tz=ZoneInfo("UTC"))
                if created < sent:
                    continue
                w = _words(f"{i.get('title') or ''} {i.get('video_description') or ''}")
                score = len(title & w) / max(1, len(title))
                if score > best_score:
                    best, best_score = i, score
            if best is not None and best_score >= 0.6:
                item, how = best, "caption"
                v.post_id, v.status, v.published_at = str(best["id"]), "published", now
                set_stage(v, "publish", "done", "Posted from your TikTok drafts (matched by caption).")
        if item is None:
            continue
        m = {k: item.get(f"{k}_count") for k in ("view", "like", "comment", "share")}
        v.metrics = {"views": m["view"], "likes": m["like"], "comments": m["comment"], "shares": m["share"], "matched_by": how,
                     "share_url": item.get("share_url")}
        v.metrics_at = now
        db.add(BmMetric(brand_id=b.id, video_id=v.id, at=now, views=m["view"], likes=m["like"], comments=m["comment"], shares=m["share"]))
        set_stage(v, "analytics", "done", f"{m['view'] or 0:,} views")
        out["matched"] += 1
    out["videos"] = len(items)
    db.commit()
    return out


def _eng(m: dict[str, Any]) -> float | None:
    views = m.get("views")
    if not views:
        return None
    return ((m.get("likes") or 0) + (m.get("comments") or 0) + (m.get("shares") or 0)) / views


def _bucket(d: float | None) -> str | None:
    if d is None:
        return None
    return "under 25s" if d < 25 else "25-35s" if d <= 35 else "over 35s"


def analyze(db: Session, b: BmBrand, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    st = brands.strategy(b)
    tz = ZoneInfo(st["timezone"])
    vids = [v for v in db.scalars(select(BmVideo).where(BmVideo.brand_id == b.id, BmVideo.metrics_at.is_not(None)))
            if (v.metrics or {}).get("views") is not None]
    out: dict[str, Any] = {"videos_measured": len(vids), "at": now.isoformat(), "notes": []}
    if not vids:
        out["notes"].append("No TikTok analytics yet: insights appear once published videos have views.")
        return out
    overall = sum(v.metrics["views"] for v in vids) / len(vids)

    def group(key: Any) -> list[dict[str, Any]]:
        g: dict[str, list[BmVideo]] = defaultdict(list)
        for v in vids:
            k = key(v)
            if k is not None:
                g[k].append(v)
        rows = [{"key": k, "videos": len(vs), "avg_views": round(sum(x.metrics["views"] for x in vs) / len(vs)),
                 "engagement": round(sum(e for x in vs if (e := _eng(x.metrics)) is not None) / max(1, len(vs)), 4)} for k, vs in g.items()]
        return sorted(rows, key=lambda r: -r["avg_views"])

    out["by_category"] = group(lambda v: v.category)
    out["by_template"] = group(lambda v: v.template)
    out["by_length"] = group(lambda v: _bucket(v.duration))
    out["by_hour"] = group(lambda v: f"{(v.published_at or v.slot_at or v.created_at).astimezone(tz):%H}:00")
    enough = [r for r in out["by_length"] if r["videos"] >= 3]
    out["best_length"] = enough[0]["key"] if enough else None
    mult = {}
    for r in out["by_category"]:
        if r["videos"] >= 3 and overall > 0:
            mult[r["key"]] = round(max(0.7, min(1.4, r["avg_views"] / overall)), 2)
    out["category_multipliers"] = mult
    tr = trends.learn(vids, overall)  # did riding a trend word pay off? (scales the trend boost in topic ranking)
    out["by_trend"], out["trend_multiplier"], out["trend_words"] = tr["rows"], tr["multiplier"], tr["words"]
    ranked = sorted(vids, key=lambda v: -v.metrics["views"])
    out["top_hooks"] = [str((v.script or {}).get("hook") or "")[:160] for v in ranked[:3] if (v.script or {}).get("hook")]
    out["best_video"] = {"id": ranked[0].id, "topic": ranked[0].topic, "views": ranked[0].metrics["views"]}
    out["underperforming"] = [{"id": v.id, "topic": v.topic, "views": v.metrics["views"]} for v in ranked[-3:] if v.metrics["views"] < overall * 0.5]
    if len(vids) < 9:
        out["notes"].append(f"Only {len(vids)} measured videos: recommendations get reliable after about 9.")
    for v in vids:
        set_stage(v, "improve", "done", "Included in the latest analysis")
    db.commit()
    return out
