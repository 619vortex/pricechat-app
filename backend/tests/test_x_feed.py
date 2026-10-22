"""Read & Earn: Price Chat's own X posts in the Kiwi Passport. Reading here earns (server clock, once per post, daily
cap); actions on X never do. Parent posts travel with our replies."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.x.normalize import page
from app.db.base import utcnow
from app.members import x_feed
from app.models.enums import ContentStatus, Platform
from app.models.members import Member, MemberAction, ReputationEvent, XPRule
from app.models.social import SocialPost
from app.social.store import upsert_post
from tests.test_members import app_and_client, fake_tg, google_sign_in, mc, providers  # noqa: F401  (fixtures)
from tests.test_missions import link, member


def post(db: Session, pid: str, text: str, hours: float = 2, own: bool = True, ref: list | None = None) -> SocialPost:
    p = SocialPost(platform=Platform.X, external_id=pid, text=text, is_own=own, url=f"https://x.com/pricechat/status/{pid}",
                   status=ContentStatus.PUBLISHED if own else ContentStatus.DRAFT, posted_at=utcnow() - timedelta(hours=hours),
                   referenced=ref or [], source_kinds=["own"], rule_ids=[], metrics={"likes": 3})
    db.add(p)
    db.commit()
    return p


def x_member(db: Session, name: str = "kiwi") -> object:
    """A member who connected X (Read & Earn needs it)."""
    m = member(db, name)
    link(db, m, "x", f"x-{name}")
    return m


def rewind(db: Session, member_id: int, pid: str, seconds: int) -> None:
    a = db.scalar(select(MemberAction).where(MemberAction.member_id == member_id, MemberAction.kind == x_feed.SHOWN,
                                             MemberAction.target == f"x:{pid}"))
    assert a is not None
    a.created_at = utcnow() - timedelta(seconds=seconds)
    db.commit()


def test_feed_has_only_worthwhile_recent_price_chat_posts(db: Session) -> None:
    post(db, "1", "Pokémon cards are outperforming your portfolio. 😭")
    post(db, "2", "@IOHK_Charles Bro went to a Solana conference to make sure Cardano still exists 😭",
         ref=[{"type": "replied_to", "id": "90", "text": "Great to be here at the Solana summit", "username": "IOHK_Charles", "name": "Charles Hoskinson"}],
         hours=3)
    post(db, "3", "@Driztcg yo", ref=[{"type": "replied_to", "id": "91"}])  # too thin to feature
    post(db, "4", "@someone https://t.co/abcdef", ref=[{"type": "replied_to", "id": "92"}])  # a bare link
    post(db, "5", "RT @pricechat: Pokémon cards are outperforming your portfolio.", ref=[{"type": "retweeted", "id": "1"}])
    post(db, "6", "An older post about markets and prices", hours=24 * 20)  # older than the feed reaches
    post(db, "7", "Someone else's post that we only read", own=False)
    post(db, "8", "@MasonVersluis We built a channel around prices", ref=[{"type": "replied_to", "id": "93"}], hours=1)
    ids = [p.external_id for p in x_feed.candidates(db)]
    assert ids == ["8", "1", "2"]  # newest first
    reply = x_feed.card(db.scalar(select(SocialPost).where(SocialPost.external_id == "2")))
    assert reply["kind"] == "reply" and reply["text"] == "Bro went to a Solana conference to make sure Cardano still exists 😭"
    assert reply["parent"] == {"text": "Great to be here at the Solana summit", "name": "Charles Hoskinson", "username": "IOHK_Charles"}
    unknown = x_feed.card(db.scalar(select(SocialPost).where(SocialPost.external_id == "8")))
    assert unknown["parent"] is None and unknown["replying_to"] == "MasonVersluis"  # honest: no invented parent text


def test_reading_pays_once_after_the_read_time_and_never_for_x_actions(db: Session) -> None:
    mem = x_member(db)
    post(db, "1", "Pokémon cards are outperforming your portfolio. 😭")
    assert x_feed.read(db, mem, "1")["code"] == "not_started"
    x_feed.shown(db, mem, "1")
    assert x_feed.read(db, mem, "1")["code"] == "too_soon"  # the server's clock, not the browser's
    rewind(db, mem.id, "1", x_feed.READ_SECONDS)
    r = x_feed.read(db, mem, "1")
    assert r["code"] == "paid" and r["xp"] == 1
    assert x_feed.read(db, mem, "1")["code"] == "already"
    events = list(db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == mem.id, ReputationEvent.event_type == x_feed.RULE)))
    assert len(events) == 1 and events[0].reason.startswith("Read Price Chat on X")
    assert x_feed.feed(db, mem)["items"] == []  # done: never shown again
    x_feed.like_opened(db, mem, "1")  # opening it on X to like it: recorded for the dashboard, never XP
    assert db.scalar(select(ReputationEvent).where(ReputationEvent.member_id == mem.id, ReputationEvent.event_type.like("x.%"))) is None


def test_daily_cap_is_the_dashboard_rule(db: Session) -> None:
    mem = x_member(db)
    rule = db.scalar(select(XPRule).where(XPRule.event_type == x_feed.RULE))
    assert rule is not None and rule.xp == 1 and rule.daily_cap == 10
    rule.daily_cap = 2
    db.commit()
    for i in range(3):
        post(db, str(10 + i), f"Price Chat post number {i} about markets", hours=1 + i)
    codes = []
    for i in range(3):
        pid = str(10 + i)
        x_feed.shown(db, mem, pid)
        rewind(db, mem.id, pid, 10)
        codes.append(x_feed.read(db, mem, pid)["code"])
    assert codes == ["paid", "paid", "refused"]  # still marked read, but no more XP today
    f = x_feed.feed(db, mem)
    assert f["today"] == {"earned": 2, "cap": 2, "read": 3, "per_post": 1} and f["items"] == []


def test_parent_posts_ride_along_with_our_replies() -> None:
    body = {
        "data": [{"id": "2", "text": "@IOHK_Charles Bro went to a Solana conference", "author_id": "100",
                  "referenced_tweets": [{"type": "replied_to", "id": "90"}]}],
        "includes": {"users": [{"id": "100", "username": "pricechat", "name": "Price Chat"},
                               {"id": "200", "username": "IOHK_Charles", "name": "Charles Hoskinson"}],
                     "tweets": [{"id": "90", "text": "Great to be here at the Solana summit", "author_id": "200"}]},
    }
    ref = page(body).posts[0].referenced[0]
    assert ref == {"type": "replied_to", "id": "90", "text": "Great to be here at the Solana summit", "author_id": "200",
                   "username": "IOHK_Charles", "name": "Charles Hoskinson"}


def test_known_context_is_kept_when_x_sends_the_post_again(db: Session) -> None:
    with_ctx = page({"data": [{"id": "2", "text": "@a hello there friends", "referenced_tweets": [{"type": "replied_to", "id": "90"}]}],
                     "includes": {"tweets": [{"id": "90", "text": "the parent"}]}}).posts[0]
    bare = page({"data": [{"id": "2", "text": "@a hello there friends", "referenced_tweets": [{"type": "replied_to", "id": "90"}]}]}).posts[0]
    upsert_post(db, bare, "own", is_own=True)
    row, _ = upsert_post(db, with_ctx, "own", is_own=True)
    assert row.referenced[0]["text"] == "the parent"
    row, _ = upsert_post(db, bare, "own", is_own=True)  # a later read without includes never erases it
    assert row.referenced[0]["text"] == "the parent"


def test_api_needs_a_member_and_a_feed_post(mc: TestClient, db: Session) -> None:  # noqa: F811
    assert mc.get("/api/members/me/x-feed").status_code == 401
    google_sign_in(mc)
    post(db, "1", "Pokémon cards are outperforming your portfolio. 😭")
    post(db, "7", "Someone else's post that we only read", own=False)
    f = mc.get("/api/members/me/x-feed").json()
    assert f["items"] == [] and f["needs_x"] is True and f["x"]["connected"] is False  # no feed before Connect X
    assert mc.post("/api/members/me/x-feed/shown", json={"post_id": "1"}).json()["code"] == "connect_x"
    assert mc.post("/api/members/me/x-feed/read", json={"post_id": "1"}).json()["code"] == "connect_x"
    m = db.scalar(select(Member).order_by(Member.id.desc()))
    link(db, m, "x", "x-api")
    f = mc.get("/api/members/me/x-feed").json()
    assert [i["id"] for i in f["items"]] == ["1"] and f["items"][0]["xp"] == 1 and f["read_seconds"] == x_feed.READ_SECONDS
    assert f["x"]["connected"] is True and f["needs_x"] is False and f["items"][0]["done"] == []
    assert mc.post("/api/members/me/x-feed/shown", json={"post_id": "7"}).status_code == 404  # not ours: not in the feed
    assert mc.post("/api/members/me/x-feed/shown", json={"post_id": "abc"}).status_code == 422
    assert mc.post("/api/members/me/x-feed/shown", json={"post_id": "1"}).json()["ok"] is True
    assert mc.post("/api/members/me/x-feed/read", json={"post_id": "1"}).json()["code"] == "too_soon"
    stats = mc.get("/api/members-admin/x-feed").json()
    assert stats["shown"] == 1 and stats["read"] == 0 and stats["posts_in_feed"] == 1


def test_media_rides_along_from_xs_own_image_host_only(db: Session) -> None:
    body = {"data": [{"id": "5", "text": "Look at this chart, Bitcoin is waking up", "author_id": "100",
                      "attachments": {"media_keys": ["3_1", "3_2", "7_9"]}}],
            "includes": {"users": [{"id": "100", "username": "pricechat", "name": "Price Chat"}],
                         "media": [{"media_key": "3_1", "type": "photo", "url": "https://pbs.twimg.com/media/a.jpg", "width": 1200, "height": 675},
                                   {"media_key": "3_2", "type": "photo", "url": "https://evil.example/b.jpg"},
                                   {"media_key": "7_9", "type": "video", "preview_image_url": "https://pbs.twimg.com/ext_tw_video_thumb/v.jpg"}]}}
    data = page(body).posts[0]
    assert [m["url"] for m in data.media] == ["https://pbs.twimg.com/media/a.jpg", "https://pbs.twimg.com/ext_tw_video_thumb/v.jpg"]
    row, _ = upsert_post(db, data, "own", is_own=True)
    bare = page({"data": [{"id": "5", "text": "Look at this chart, Bitcoin is waking up", "author_id": "100"}]}).posts[0]
    row, _ = upsert_post(db, bare, "own", is_own=True)  # a later read without media never erases it
    row.status, row.posted_at = ContentStatus.PUBLISHED, utcnow()
    db.commit()
    c = x_feed.card(row)
    assert [m["type"] for m in c["media"]] == ["photo", "video"] and c["author"]["name"] == "Price Chat"


def test_each_post_says_what_the_member_already_did_from_here(db: Session) -> None:
    mem = x_member(db)
    post(db, "1", "Pokémon cards are outperforming your portfolio. 😭")
    db.add(MemberAction(member_id=mem.id, kind="x_repost", platform="x", target="1", day="2026-10-17"))
    db.commit()
    x_feed.like_opened(db, mem, "1")
    item = x_feed.feed(db, mem)["items"][0]
    assert item["done"] == ["like", "repost"]  # from the server (X confirmed the repost), never the browser's word
    assert db.scalar(select(ReputationEvent).where(ReputationEvent.member_id == mem.id, ReputationEvent.event_type.like("x.%"))) is None


def test_unlinking_x_stops_read_and_earn(db: Session) -> None:
    from app.members import service

    mem = x_member(db)
    link(db, mem, "telegram", "tg-1")  # another way to sign in, so X can be removed
    post(db, "1", "Pokémon cards are outperforming your portfolio. 😭")
    assert x_feed.feed(db, mem)["items"]
    service.disconnect(db, mem, "x")
    assert x_feed.feed(db, mem)["needs_x"] is True and x_feed.read(db, mem, "1")["code"] == "connect_x"
