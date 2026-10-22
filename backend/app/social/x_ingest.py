"""X read-only ingestion: own posts, mentions, monitoring rules and watched accounts.

Only explicitly configured things are read. Every request is budget-checked and
incremental (since_id), and a run makes at most MAX_REQUESTS calls. Rate limits
and usage caps pause X reads until the reset time instead of retrying.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.snapshots import record_x_account, x_snapshot_due
from app.community.vectors import normalise, to_blob
from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.connectors.x.client import XClient
from app.connectors.x.connector import XRuntime
from app.connectors.x.errors import XBudgetExceededError, XError, XForbiddenError, XRateLimitError
from app.connectors.x.normalize import page as to_page
from app.db.base import utcnow
from app.models.enums import InteractionType, MonitorRuleType, Platform
from app.models.social import SocialAccount, SocialPost
from app.models.social_intel import MonitorRule, SocialPostVector, SocialProfile, WatchedAccount
from app.providers.base import EmbeddingProvider
from app.providers.errors import ProviderError
from app.social.store import record_interaction, upsert_post, upsert_profile
from app.social.types import SocialPage
from app.social.x_service import make_x_client

logger = logging.getLogger(__name__)

MAX_REQUESTS = 20
METRICS_REFRESH_HOURS = 6


def _aware(dt):  # type: ignore[no-untyped-def]
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


ACTION_METRICS_DAYS = 7
PRIVATE_METRICS_DAYS = 30
CONTEXT_DAYS = 14  # replies / quotes whose parent post is looked up for the Kiwi Passport feed
CONTEXT_BATCH = 25  # parents looked up per run (one request)


@dataclass(slots=True)
class XIngestReport:
    requests: int = 0
    new_posts: int = 0
    mentions: int = 0
    rules_checked: int = 0
    accounts_checked: int = 0
    metrics_updated: int = 0
    stopped: str | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {k: getattr(self, k) for k in ("requests", "new_posts", "mentions", "rules_checked", "accounts_checked", "metrics_updated", "stopped", "errors")}


def rule_query(rule: MonitorRule) -> str:
    q = rule.query.strip()
    if rule.type == MonitorRuleType.SEARCH:
        return q
    if rule.type == MonitorRuleType.ACCOUNT:
        return f"from:{q.lstrip('@')} -is:retweet"
    term = f'"{q}"' if " " in q and not q.startswith('"') else q
    return f"{term} -is:retweet"


PERFORMANCE_CHECKPOINTS = (1, 6, 24)  # hours after Price Chat published something


class XIngestor:
    def __init__(self, db: Session, settings: Settings, runtime: XRuntime, embedder: EmbeddingProvider | None) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.embedder = embedder
        self.state = get_connector_state(db, Platform.X)
        self.report = XIngestReport()
        self.new_rows: list[SocialPost] = []
        self.own_snapshot = False

    def _store_page(self, pg: SocialPage, kind: str, rule_id: int | None = None, own: bool = False) -> list[SocialPost]:
        profiles = {pid: upsert_profile(self.db, p, is_self=(pid == self.state.account_external_id)) for pid, p in pg.profiles.items()}
        if self.state.account_external_id and self.state.account_external_id in pg.profiles:
            # Our own user object came back with this response: free follower snapshot for the growth dashboard.
            record_x_account(self.db, pg.profiles[self.state.account_external_id], "x_response")
            self.own_snapshot = True
        rows = []
        for post in pg.posts:
            author = profiles.get(post.author_external_id or "")
            row, created = upsert_post(self.db, post, kind, author, rule_id, is_own=own)
            rows.append(row)
            if created:
                self.report.new_posts += 1
                self.new_rows.append(row)
            if author is not None and not own and post.author_external_id != self.state.account_external_id:
                if kind == "mention":
                    itype = InteractionType.REPLY if post.in_reply_to_user_external_id == self.state.account_external_id else InteractionType.MENTION
                    record_interaction(self.db, author, row, itype, "inbound", post.created_at)
            if own and post.in_reply_to_user_external_id:
                target = self.db.scalar(select(SocialProfile).where(
                    SocialProfile.platform == Platform.X, SocialProfile.external_id == post.in_reply_to_user_external_id))
                if target is not None:
                    record_interaction(self.db, target, row, InteractionType.REPLY, "outbound", post.created_at)
        return rows

    async def _backfill_context(self, client: XClient, extra: dict) -> None:  # type: ignore[type-arg]
        """Our recent replies / quotes whose parent post we don't know yet (stored before X included it with our
        timeline): one lookup of at most CONTEXT_BATCH posts per run. A post X can't return (deleted, protected) is
        never asked for again."""
        tried = set(extra.get("context_tried") or [])
        want: dict[str, list[SocialPost]] = {}
        for p in self.db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                                                          SocialPost.posted_at >= utcnow() - timedelta(days=CONTEXT_DAYS))):
            for r in p.referenced or []:
                if r.get("type") in ("replied_to", "quoted") and "text" not in r and r.get("id") and r["id"] not in tried:
                    want.setdefault(str(r["id"]), []).append(p)
        ids = list(want)[:CONTEXT_BATCH]
        if not ids:
            return
        body = await self._call(lambda: client.public_posts(ids))
        pg = to_page(body)
        for post in pg.posts:
            author = pg.profiles.get(post.author_external_id or "")
            ctx = {"text": post.text[:600], "author_id": post.author_external_id,
                   "username": author.username if author else None, "name": author.display_name if author else None}
            for own in want.get(post.external_id, []):
                own.referenced = [{**r, **ctx} if r.get("id") == post.external_id else r for r in own.referenced or []]
        extra["context_tried"] = [*tried, *ids][-500:]

    async def _call(self, fn):  # type: ignore[no-untyped-def]
        if self.report.requests >= MAX_REQUESTS:
            raise _RunLimit()
        self.report.requests += 1
        return await fn()

    def _checkpoint_due(self) -> list[SocialPost]:
        """Our published posts whose ~1 h / ~6 h / ~24 h snapshot is due and not yet taken."""
        from app.models.social_intel import SocialPostMetric

        now = utcnow()
        due: list[SocialPost] = []
        self._checkpoints: dict[int, int] = {}
        for p in self.db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                                                          SocialPost.posted_at >= now - timedelta(hours=48))):
            if "price_chat_action" not in (p.source_kinds or []) or not p.posted_at:
                continue
            age = (now - _aware(p.posted_at)).total_seconds() / 3600
            for cp in reversed(PERFORMANCE_CHECKPOINTS):
                if age >= cp:
                    taken = self.db.scalar(select(SocialPostMetric.id).where(
                        SocialPostMetric.post_id == p.id, SocialPostMetric.captured_at >= _aware(p.posted_at) + timedelta(hours=cp)))
                    if taken is None:
                        due.append(p)
                        self._checkpoints[p.id] = cp
                    break
        return due

    def _record_checkpoints(self, posts: list[SocialPost]) -> None:
        """Make sure each due checkpoint has a snapshot, even if the numbers did not change since the last one."""
        from app.models.social_intel import SocialPostMetric

        now = utcnow()
        for p in posts:
            cp = getattr(self, "_checkpoints", {}).get(p.id)
            if cp is None:
                continue
            exists = self.db.scalar(select(SocialPostMetric.id).where(
                SocialPostMetric.post_id == p.id, SocialPostMetric.captured_at >= _aware(p.posted_at) + timedelta(hours=cp)))
            if exists is None and p.metrics:
                m = p.metrics
                self.db.add(SocialPostMetric(post_id=p.id, captured_at=now, source=f"checkpoint_{cp}h",
                                             impressions=m.get("impressions"), views=m.get("views"), likes=m.get("likes"),
                                             replies=m.get("replies"), reposts=m.get("reposts"), quotes=m.get("quotes"),
                                             bookmarks=m.get("bookmarks"), profile_visits=m.get("profile_visits"), url_clicks=m.get("url_clicks")))
        self.db.flush()

    async def run(self, client: XClient, only_own: bool = False) -> XIngestReport:
        """only_own: just Price Chat's own new posts (+ the posts they reply to), the cheap read Read & Earn needs."""
        account_id = self.state.account_external_id
        account = self.db.scalar(select(SocialAccount).where(SocialAccount.platform == Platform.X))
        extra = dict(account.extra or {}) if account else {}
        caps = {k: v.get("status") for k, v in (self.state.capabilities or {}).items()}
        try:
            if account_id and caps.get("own_posts") == "available":
                body = await self._call(lambda: client.user_posts(account_id, 10, extra.get("since_own"), own=True))
                pg = to_page(body)
                self._store_page(pg, "own", own=True)
                if pg.newest_id:
                    extra["since_own"] = pg.newest_id
                await self._backfill_context(client, extra)
            if only_own:
                raise _OwnOnly
            if account_id and caps.get("mentions") == "available":
                body = await self._call(lambda: client.mentions(account_id, 20, extra.get("since_mentions")))
                pg = to_page(body)
                self._store_page(pg, "mention")
                self.report.mentions += len(pg.posts)
                if pg.newest_id:
                    extra["since_mentions"] = pg.newest_id

            # Follower history (before the budget-heavy searches): only if no response carried our account
            # recently, one small lookup.
            if account_id and not self.own_snapshot and x_snapshot_due(self.db, account_id):
                if client.has_user_context:
                    me = await self._call(lambda: client.me())
                elif self.state.account_username:
                    me = await self._call(lambda: client.user_by_username(self.state.account_username))
                else:
                    me = None
                if me:
                    record_x_account(self.db, me, "users_me")

            rules = list(self.db.scalars(select(MonitorRule).where(MonitorRule.platform == Platform.X, MonitorRule.enabled.is_(True)).order_by(MonitorRule.priority, MonitorRule.id)))
            for rule in rules:
                try:
                    body = await self._call(lambda rule=rule: client.search_recent(rule_query(rule), rule.max_results, rule.since_id))
                    pg = to_page(body)
                    self._store_page(pg, "search", rule_id=rule.id)
                    rule.last_result_count = len(pg.posts)
                    rule.posts_matched += len(pg.posts)
                    rule.since_id = pg.newest_id or rule.since_id
                    rule.last_error = None
                    rule.last_success_at = utcnow()
                except XForbiddenError as exc:
                    rule.last_error = str(exc)
                    self.report.errors.append(f"rule {rule.name}: {exc}")
                rule.last_checked_at = utcnow()
                self.report.rules_checked += 1

            watched = list(self.db.scalars(select(WatchedAccount).where(WatchedAccount.enabled.is_(True)).order_by(WatchedAccount.priority, WatchedAccount.id)))
            for w in watched:
                profile = self.db.get(SocialProfile, w.profile_id)
                if profile is None or profile.platform != Platform.X:
                    continue
                try:
                    body = await self._call(lambda: client.user_posts(profile.external_id, 5, w.since_id))
                    pg = to_page(body)
                    self._store_page(pg, "watchlist")
                    w.since_id = pg.newest_id or w.since_id
                    w.last_error = None
                    w.last_success_at = utcnow()
                except XForbiddenError as exc:
                    w.last_error = str(exc)
                w.last_checked_at = utcnow()
                self.report.accounts_checked += 1

            # Performance of our own posts. Cached: each post is refreshed at most every METRICS_REFRESH_HOURS,
            # plus one snapshot at ~1 h, ~6 h and ~24 h after Price Chat published something.
            checkpoint_posts = self._checkpoint_due()
            stale = utcnow() - timedelta(hours=METRICS_REFRESH_HOURS)
            due = (SocialPost.metrics_updated_at.is_(None)) | (SocialPost.metrics_updated_at < stale)
            if account_id and caps.get("private_metrics") == "available" and client.has_user_context:
                # Due: never had private metrics yet (url_clicks only comes from them), or last refresh is stale.
                recent = [p for p in self.db.scalars(select(SocialPost).where(
                    SocialPost.platform == Platform.X, SocialPost.is_own.is_(True),
                    SocialPost.posted_at >= utcnow() - timedelta(days=PRIVATE_METRICS_DAYS),
                ).order_by(SocialPost.posted_at.desc()).limit(50))
                    if (p.metrics or {}).get("url_clicks") is None or p.metrics_updated_at is None or _aware(p.metrics_updated_at) < stale][:20]
                recent += [p for p in checkpoint_posts if p not in recent]
                if recent:
                    body = await self._call(lambda: client.own_post_metrics([p.external_id for p in recent if p.external_id]))
                    self._store_page(to_page(body), "own", own=True)
                    self.report.metrics_updated = len(recent)
            else:
                # Without private metrics: public metrics for what Price Chat itself published in the last week.
                ours = [p for p in self.db.scalars(select(SocialPost).where(
                    SocialPost.platform == Platform.X, SocialPost.is_own.is_(True), due,
                    SocialPost.posted_at >= utcnow() - timedelta(days=ACTION_METRICS_DAYS),
                ).order_by(SocialPost.posted_at.desc()).limit(20)) if "price_chat_action" in (p.source_kinds or []) and p.external_id]
                ours += [p for p in checkpoint_posts if p not in ours and p.external_id]
                if ours:
                    body = await self._call(lambda: client.public_posts([p.external_id for p in ours]))
                    self._store_page(to_page(body), "own", own=True)
                    self.report.metrics_updated += len(ours)
            self._record_checkpoints(checkpoint_posts)
        except _OwnOnly:
            pass
        except _RunLimit:
            self.report.stopped = f"request limit per run ({MAX_REQUESTS}) reached; continuing next run"
        except XBudgetExceededError as exc:
            self.report.stopped = str(exc)
        except XRateLimitError as exc:
            self.state.blocked_until = exc.reset_at if exc.reset_at and exc.reset_at > utcnow() else utcnow() + timedelta(minutes=15)
            self.report.stopped = f"{exc} (paused until {self.state.blocked_until:%H:%M} UTC)"
        finally:
            if account is not None:
                account.extra = extra
            self.state.rate_limits = {**(self.state.rate_limits or {}), **client.rate_limits}
            self.db.flush()

        await self._embed_new()
        self.state.status = "connected"
        self.state.last_success_at = utcnow()
        self.state.last_error = None
        self.runtime.status = "connected"
        self.db.commit()
        return self.report

    async def _embed_new(self) -> None:
        if self.embedder is None or not self.new_rows:
            return
        try:
            for start in range(0, len(self.new_rows), 32):
                batch = self.new_rows[start : start + 32]
                vecs = await self.embedder.embed([p.text[:2000] for p in batch], purpose="document")
                for p, v in zip(batch, vecs, strict=True):
                    arr = normalise(v)
                    self.db.add(SocialPostVector(post_id=p.id, model=self.embedder.model, dimensions=int(arr.shape[0]), vector=to_blob(arr)))
        except ProviderError as exc:
            logger.warning("X posts stored without embeddings: %s", exc)


class _RunLimit(Exception):
    pass


class _OwnOnly(Exception):
    pass


async def ingest_x(db: Session, settings: Settings, runtime: XRuntime, embedder: EmbeddingProvider | None,
                   only_own: bool = False) -> XIngestReport:
    client = make_x_client(db, settings, runtime)
    try:
        return await XIngestor(db, settings, runtime, embedder).run(client, only_own=only_own)
    finally:
        await client.aclose()


__all__ = ["XError", "XIngestReport", "ingest_x", "rule_query"]
