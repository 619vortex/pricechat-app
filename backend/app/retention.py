"""Data retention (job `system.retention`, daily): deletes old rows of tables that only ever grow, oldest first, at most BATCH
rows per table per run (an id subquery, so it runs the same on Postgres and SQLite), committing per table. Rows still in use are
never touched. A backlog drains over a few days.

Deliberately NOT pruned (they hold all-time numbers the product shows, or are already bounded):
  arb_lunch_trades / arb_lunch_graduations: Lunch Bot's all-time stats (summary) and all-time gap stats count every row;
  ts_signal_ideas TARGET/STOP/EXPIRED: /record is over everything graded; tb_events: the affiliate view counts every ref_click;
  owner_alerts / scheduled_jobs: one row per alert key / per job (there is no job-run history table);
  *_audit tables: audit trails; bm_trend_days, pokemon pulse posts, decisions: they prune themselves (KEEP_DAYS).
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.brand_manager import BmAutomation, BmEvent, BmMetric
from app.models.enums import ActivityLevel
from app.models.tradingbot import TbReferralClick
from app.models.tradingos import TsPriceAlert, TsSignalIdea

BATCH = 5000  # rows per table per run

WINDOWS = {  # days kept
    "bm_events": 30,  # Brand Manager log: the UI shows the newest; daily caps look back 24 h. The worker-bootstrap line is kept (it makes that once-only)
    "bm_metrics": 180,  # per-video readings (the latest is also on bm_videos.metrics); follower readings: analytics charts 60 d.
                        # Older follower rows go too, except each brand's first and its first since AUTO started ("followers gained")
    "tb_referral_clicks": 400,  # admin shows 24 h / 7 d / 30 d; a year+ for comparisons
    "ts_signal_ideas": 365,  # only SKIPPED ideas (never graded, counted nowhere); OPEN/FILLED are being followed, graded ones feed /record
    "ts_price_alerts": 30,  # fired / off / expired / blocked, by last change; active alerts are never touched
    "activity_logs": 90,  # the dashboard feed and rate-limit windows are recent; audit-level rows and the last AI success are kept
}


def _purge(db: Session, model: Any, cond: Any) -> int:
    ids = select(model.id).where(cond).order_by(model.id).limit(BATCH).scalar_subquery()
    n = db.execute(delete(model).where(model.id.in_(ids)), execution_options={"synchronize_session": False}).rowcount or 0
    db.commit()
    return int(n)


def _bm_metrics_keep(db: Session) -> list[int]:
    """Follower readings the overview's 'followers gained' is measured from."""
    f = and_(BmMetric.video_id.is_(None), BmMetric.followers.is_not(None))
    keep = set(db.scalars(select(func.min(BmMetric.id)).where(f).group_by(BmMetric.brand_id)))
    for a in db.scalars(select(BmAutomation).where(BmAutomation.started_at.is_not(None))):
        keep.add(db.scalar(select(BmMetric.id).where(f, BmMetric.brand_id == a.brand_id, BmMetric.at >= a.started_at).order_by(BmMetric.at, BmMetric.id).limit(1)))
    return sorted(k for k in keep if k is not None) or [0]


def run(db: Session, now: datetime) -> dict[str, int]:
    from app.brand_manager.queue import BOOTSTRAP_LOG
    from app.services.provider_monitor import SUCCESS_EVENTS

    cut = {t: now - timedelta(days=d) for t, d in WINDOWS.items()}
    last_ok = db.scalar(select(func.max(ActivityLog.id)).where(ActivityLog.event.in_(SUCCESS_EVENTS), ActivityLog.level == ActivityLevel.INFO)) or 0
    return {
        "bm_events": _purge(db, BmEvent, and_(BmEvent.at < cut["bm_events"], BmEvent.message != BOOTSTRAP_LOG)),
        "bm_metrics": _purge(db, BmMetric, and_(BmMetric.at < cut["bm_metrics"], BmMetric.id.not_in(_bm_metrics_keep(db)))),
        "tb_referral_clicks": _purge(db, TbReferralClick, TbReferralClick.at < cut["tb_referral_clicks"]),
        "ts_signal_ideas": _purge(db, TsSignalIdea, and_(TsSignalIdea.status == "SKIPPED", TsSignalIdea.created_at < cut["ts_signal_ideas"])),
        "ts_price_alerts": _purge(db, TsPriceAlert, and_(TsPriceAlert.status != "active", TsPriceAlert.updated_at < cut["ts_price_alerts"])),
        "activity_logs": _purge(db, ActivityLog, and_(ActivityLog.created_at < cut["activity_logs"], ActivityLog.level != ActivityLevel.AUDIT,
                                                      ActivityLog.id != last_ok)),
    }
