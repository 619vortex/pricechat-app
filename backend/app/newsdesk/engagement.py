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


# --- the media engine (owner, 2026-11-13): both platforms, normalized, learned per beat -----------------------------------------------
#
# Telegram: views, forwards, comments and reactions of the channel's posts, read with the owner's Telegram account (MTProto, Admin >
# Telegram > Account) when it is logged in. The Bot API gives none of these for channel posts, so without that login Telegram is
# "not connected" and contributes nothing. X: impressions, reposts, quotes, replies, likes and bookmarks from engine.collect_metrics.
# Each post is stored once per platform (NdEngagement) as the platform reported it; its rate is (2 x shares + 2 x replies + reactions) /
# views, and its index is that rate against the platform's own median over DAYS, so a Telegram post and an X post compare fairly. The
# learner turns the average index per beat (scorecard.BEATS) and per hook strategy (hooks.STRATEGIES) into -6..+6 points, pulled towards
# 1.0 by PRIOR pseudo-posts, and only once LEARN_MIN posts are measured. Nothing is ever estimated: no data, no adjustment.

MIN_VIEWS = {"telegram": 20, "x": 50}
LEARN_MIN = 8
BEAT_SPAN = 6.0
TG_NOT_CONNECTED = ("not connected: Telegram's Bot API gives no view, forward or reaction counts for channel posts. Log the owner's Telegram "
                    "account in (Admin > Telegram > Account, MTProto) and the desk reads them once a day.")


def norm_rate(platform: str, views: int, shares: int, replies: int, reactions: int) -> float | None:
    if views < MIN_VIEWS.get(platform, 50):
        return None
    return round((2 * shares + 2 * replies + reactions) / views, 5)


def features_for(ev: Any) -> dict[str, Any]:
    """What a post was made of, for the learner: beat and editorial total (scorecard), hook strategy, picture kind, question, writer."""
    f = getattr(ev, "facts", None) or {}
    card, hk, pkg = f.get("editorial") or {}, f.get("hooks") or {}, f.get("package") or {}
    return {"beat": str(card.get("beat") or ""), "editorial": card.get("total"), "hook_strategy": str(hk.get("chosen_strategy") or ""),
            "format": str((f.get("visual") or {}).get("kind") or ""), "question": bool((pkg.get("copy") or {}).get("question")),
            "writer": str((pkg.get("copy") or {}).get("writer") or ""), "kind": str(getattr(ev, "kind", "") or ""), "topic": str(getattr(ev, "topic", "") or "")}


def record(db: Session, *, platform: str, post_ref: str, views: int, shares: int, replies: int, reactions: int, now: datetime,
           event_id: int | None = None, published_at: datetime | None = None, features: dict[str, Any] | None = None,
           raw: dict[str, Any] | None = None) -> Any:
    from app.models.newsdesk import NdEngagement

    row = db.scalar(select(NdEngagement).where(NdEngagement.platform == platform, NdEngagement.post_ref == str(post_ref)))
    if row is None:
        row = NdEngagement(platform=platform, post_ref=str(post_ref), features={}, raw={})
        db.add(row)
    row.event_id = event_id if event_id is not None else row.event_id
    row.published_at = published_at or row.published_at
    row.measured_at = now
    row.views, row.shares, row.replies, row.reactions = int(views), int(shares), int(replies), int(reactions)
    row.rate = norm_rate(platform, row.views, row.shares, row.replies, row.reactions)
    row.features = features if features is not None else (row.features or {})
    row.raw = raw or {}
    return row


def ingest_x(db: Session, now: datetime) -> int:
    """X metrics already collected on NdPost (engine.collect_metrics) -> NdEngagement."""
    from app.models.newsdesk import NdEvent

    n = 0
    for p in db.scalars(select(NdPost).where(NdPost.status == "published", NdPost.x_id.is_not(None), NdPost.published_at >= now - timedelta(days=DAYS))):
        m = p.metrics or {}
        if "impression_count" not in m:
            continue
        eid = (p.facts or {}).get("event_id")
        ev = db.get(NdEvent, eid) if eid else None
        record(db, platform="x", post_ref=str(p.x_id), event_id=eid, published_at=p.published_at, now=now,
               views=int(m.get("impression_count") or 0), shares=int(m.get("retweet_count") or 0) + int(m.get("quote_count") or 0),
               replies=int(m.get("reply_count") or 0), reactions=int(m.get("like_count") or 0) + int(m.get("bookmark_count") or 0),
               features=features_for(ev) if ev is not None else {"kind": p.kind, "topic": str((p.facts or {}).get("topic") or "")},
               raw={k: m.get(k) for k in ("impression_count", "retweet_count", "quote_count", "reply_count", "like_count", "bookmark_count")})
        n += 1
    db.commit()
    return n


def telegram_connection(settings: Any) -> dict[str, Any]:
    try:
        from app.telegram import account_service

        if settings is not None and account_service.has_session(settings):
            return {"connected": True, "why": "the owner's Telegram account (MTProto) is logged in: views, forwards, comments and reactions are read daily"}
    except Exception:  # noqa: BLE001 - no credential store: not connected
        pass
    return {"connected": False, "why": TG_NOT_CONNECTED}


async def collect_telegram(db: Session, settings: Any, account_rt: Any, now: datetime, fetch: Any = None) -> dict[str, Any]:
    """Telegram counters for the channel posts of the last DAYS days. `fetch(channel, ids)` is for tests; otherwise the owner's logged-in
    Telegram account is used, read-only. Not logged in: nothing is read and the result says so."""
    from app.models.newsdesk import NdEvent, NdTelegramPost

    if fetch is None:
        conn = telegram_connection(settings)
        if not conn["connected"]:
            return {"connected": False, "updated": 0, "why": conn["why"]}
        from app.telegram import account_service

        async def fetch(channel: str, ids: list[int]) -> list[dict[str, Any]]:  # the real reader
            async with account_service.session(db, settings, account_rt) as gw:
                return await gw.message_stats(channel, ids)
    evs = list(db.scalars(select(NdEvent).where(NdEvent.tg_message_id.is_not(None), NdEvent.released_at >= now - timedelta(days=DAYS))))
    by_chat: dict[str, list[Any]] = {}
    for e in evs:
        tp = db.get(NdTelegramPost, e.tg_post_id) if e.tg_post_id else None
        chat = (tp.chat if tp is not None else "") or ""
        if chat:
            by_chat.setdefault(chat, []).append(e)
    n = 0
    for chat, items in by_chat.items():
        stats = {int(s["id"]): s for s in await fetch(chat, [int(e.tg_message_id) for e in items])}
        for e in items:
            s = stats.get(int(e.tg_message_id))
            if not s:
                continue
            record(db, platform="telegram", post_ref=f"{chat}/{e.tg_message_id}", event_id=e.id, published_at=e.released_at, now=now,
                   views=int(s.get("views") or 0), shares=int(s.get("forwards") or 0), replies=int(s.get("replies") or 0),
                   reactions=int(s.get("reactions") or 0), features=features_for(e), raw=dict(s))
            n += 1
    db.commit()
    return {"connected": True, "updated": n}


def _rows(db: Session, now: datetime) -> list[Any]:
    from app.models.newsdesk import NdEngagement

    return list(db.scalars(select(NdEngagement).where(NdEngagement.measured_at >= now - timedelta(days=DAYS))))


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def indexed(db: Session, now: datetime) -> list[tuple[Any, float]]:
    """(row, index) for every measured row: its rate against its own platform's median (1.0 = a typical post on that platform)."""
    rows = [r for r in _rows(db, now) if r.rate is not None]
    med = {p: _median([r.rate for r in rows if r.platform == p]) for p in {r.platform for r in rows}}
    return [(r, round(r.rate / med[r.platform], 3)) for r in rows if med.get(r.platform)]


def _learn(groups: dict[str, list[float]]) -> dict[str, float]:
    out = {}
    for k, xs in groups.items():
        shrunk = (sum(xs) + PRIOR * 1.0) / (len(xs) + PRIOR)
        out[k] = round(max(-BEAT_SPAN, min(BEAT_SPAN, BEAT_SPAN * (shrunk - 1))), 1)
    return out


def _grouped(db: Session, now: datetime, name: str) -> dict[str, float]:
    ix = indexed(db, now)
    if len(ix) < LEARN_MIN:
        return {}
    by: dict[str, list[float]] = {}
    for r, i in ix:
        v = str((r.features or {}).get(name) or "")
        if v:
            by.setdefault(v, []).append(i)
    return _learn(by)


def beat_points(db: Session, now: datetime) -> dict[str, float]:
    """{beat: points} for the scorecard's audience part; {} until LEARN_MIN posts are measured across both platforms."""
    return _grouped(db, now, "beat")


def strategy_points(db: Session, now: datetime) -> dict[str, float]:
    """{hook strategy: points} for hooks.engagement; {} until LEARN_MIN posts are measured."""
    return _grouped(db, now, "hook_strategy")


def performance(db: Session, now: datetime, settings: Any = None) -> dict[str, Any]:
    """Admin's analytics: per platform (posts, median rate, views), the normalized index per beat, hook strategy, picture format and
    question, the best and worst posts, what the learner currently adds, and which sources are connected."""
    rows = _rows(db, now)
    ix = indexed(db, now)
    plat = {}
    for p in ("telegram", "x"):
        mine = [r for r in rows if r.platform == p]
        rated = [r.rate for r in mine if r.rate is not None]
        plat[p] = {"posts": len(mine), "rated": len(rated), "median_rate": round(_median(rated), 5) if rated else None,
                   "median_views": int(_median([float(r.views) for r in mine])) if mine else None,
                   "views": sum(r.views for r in mine), "shares": sum(r.shares for r in mine), "replies": sum(r.replies for r in mine),
                   "reactions": sum(r.reactions for r in mine)}
    tables: dict[str, dict[str, dict[str, Any]]] = {}
    for name in ("beat", "hook_strategy", "format", "question"):
        by: dict[str, list[float]] = {}
        for r, i in ix:
            v = (r.features or {}).get(name)
            if v not in ("", None):
                by.setdefault(str(v), []).append(i)
        tables[name] = {k: {"posts": len(v), "index": round(sum(v) / len(v), 2)} for k, v in sorted(by.items())}
    ranked = sorted(ix, key=lambda x: -x[1])

    def row_out(r: Any, i: float) -> dict[str, Any]:
        return {"platform": r.platform, "event": r.event_id, "index": i, "views": r.views, "shares": r.shares, "replies": r.replies,
                "reactions": r.reactions, "beat": (r.features or {}).get("beat") or ""}

    has_x = any(r.platform == "x" for r in rows)
    x_conn = {"connected": has_x, "why": "impressions, reposts, replies and likes from X's API (engine.collect_metrics, daily)" if has_x
              else "no measured X posts in the window (X metrics are read daily for published posts; dry-run posts have none)"}
    return {"days": DAYS, "measured": len(ix), "learning": len(ix) >= LEARN_MIN, "learn_min": LEARN_MIN, "platforms": plat, "tables": tables,
            "top": [row_out(r, i) for r, i in ranked[:5]], "bottom": [row_out(r, i) for r, i in ranked[-5:][::-1]] if len(ranked) > 5 else [],
            "beat_points": beat_points(db, now), "strategy_points": strategy_points(db, now),
            "connections": {"telegram": telegram_connection(settings), "x": x_conn}}
