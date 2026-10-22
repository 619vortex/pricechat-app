"""X API v2 payloads → platform-neutral social data."""

import re
from datetime import datetime
from typing import Any

from app.social.types import SocialMetricData, SocialPage, SocialPostData, SocialProfileData


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def profile(user: dict[str, Any]) -> SocialProfileData:
    pm = user.get("public_metrics") or {}
    return SocialProfileData(
        platform="x",
        external_id=str(user["id"]),
        username=str(user.get("username") or user["id"]),
        display_name=user.get("name"),
        followers_count=pm.get("followers_count"),
        following_count=pm.get("following_count"),
        post_count=pm.get("tweet_count"),
        verified=user.get("verified"),
    )


def metrics(tweet: dict[str, Any]) -> SocialMetricData:
    pub = tweet.get("public_metrics") or {}
    priv = tweet.get("non_public_metrics") or {}
    org = tweet.get("organic_metrics") or {}
    has_private = bool(priv or org)
    return SocialMetricData(
        impressions=priv.get("impression_count", org.get("impression_count", pub.get("impression_count"))),
        likes=pub.get("like_count"),
        replies=pub.get("reply_count"),
        reposts=pub.get("retweet_count"),
        quotes=pub.get("quote_count"),
        bookmarks=pub.get("bookmark_count"),
        profile_visits=priv.get("user_profile_clicks", org.get("user_profile_clicks")),
        url_clicks=priv.get("url_link_clicks", org.get("url_link_clicks")),
        source="non_public" if has_private else "public",
    )


def _context(ref: dict[str, Any], tweets: dict[str, dict[str, Any]], users: dict[str, SocialProfileData]) -> dict[str, Any]:
    """A referenced post ({"type", "id"}), enriched with its text and author when X included it in the same response
    (the post we replied to or quoted: shown as context in the Kiwi Passport)."""
    out = {"type": ref.get("type"), "id": str(ref.get("id"))}
    t = tweets.get(out["id"])
    if t is not None and out["type"] in ("replied_to", "quoted"):
        author = users.get(str(t.get("author_id") or ""))
        out.update({"text": str(t.get("text") or "")[:600], "author_id": str(t.get("author_id") or "") or None,
                    "username": author.username if author else None, "name": author.display_name if author else None})
    return out


_MEDIA_HOST = re.compile(r"^https://pbs\.twimg\.com/")


def _media(t: dict[str, Any], media: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """A post's photos / video previews (X's own image host only), at most 4, as X sent them."""
    out = []
    for key in ((t.get("attachments") or {}).get("media_keys") or [])[:4]:
        m = media.get(str(key))
        if not m:
            continue
        src = m.get("url") or m.get("preview_image_url")
        if not src or not _MEDIA_HOST.match(str(src)):
            continue
        out.append({"type": m.get("type") or "photo", "url": str(src), "width": m.get("width"), "height": m.get("height"),
                    "alt": str(m.get("alt_text") or "")[:400]})
    return out


def page(body: dict[str, Any]) -> SocialPage:
    users = {str(u["id"]): profile(u) for u in (body.get("includes") or {}).get("users", []) if "id" in u}
    media = {str(m["media_key"]): m for m in (body.get("includes") or {}).get("media", []) if "media_key" in m}
    included = {str(t["id"]): t for t in (body.get("includes") or {}).get("tweets", []) if "id" in t}
    data = body.get("data") or []
    if isinstance(data, dict):
        data = [data]
    posts = []
    for t in data:
        if "id" not in t:
            continue
        author = str(t["author_id"]) if t.get("author_id") else None
        username = users[author].username if author and author in users else None
        posts.append(
            SocialPostData(
                platform="x",
                external_id=str(t["id"]),
                text=str(t.get("text") or ""),
                author_external_id=author,
                created_at=_dt(t.get("created_at")),
                url=f"https://x.com/{username or 'i'}/status/{t['id']}",
                conversation_id=t.get("conversation_id"),
                in_reply_to_user_external_id=str(t["in_reply_to_user_id"]) if t.get("in_reply_to_user_id") else None,
                referenced=[_context(r, included, users) for r in t.get("referenced_tweets") or []],
                lang=t.get("lang"),
                metrics=metrics(t),
                media=_media(t, media),
            )
        )
    return SocialPage(posts, users, (body.get("meta") or {}).get("newest_id"))
