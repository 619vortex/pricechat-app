"""Platform-neutral social data. Connectors convert their API payloads into these."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SocialProfileData:
    platform: str
    external_id: str
    username: str
    display_name: str | None = None
    followers_count: int | None = None
    following_count: int | None = None
    post_count: int | None = None
    verified: bool | None = None


@dataclass(slots=True)
class SocialMetricData:
    """None means 'not provided by the API' and is stored as NULL, never as 0."""

    impressions: int | None = None
    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    quotes: int | None = None
    bookmarks: int | None = None
    profile_visits: int | None = None
    url_clicks: int | None = None
    followers_attributed: int | None = None
    source: str = "public"

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in (
            ("impressions", self.impressions), ("views", self.views), ("likes", self.likes), ("replies", self.replies),
            ("reposts", self.reposts), ("quotes", self.quotes), ("bookmarks", self.bookmarks),
            ("profile_visits", self.profile_visits), ("url_clicks", self.url_clicks),
        ) if v is not None}

    def engagement(self) -> int | None:
        parts = [v for v in (self.likes, self.replies, self.reposts, self.quotes) if v is not None]
        return sum(parts) if parts else None


@dataclass(slots=True)
class SocialPostData:
    platform: str
    external_id: str
    text: str
    author_external_id: str | None
    created_at: datetime | None
    url: str | None = None
    conversation_id: str | None = None
    in_reply_to_user_external_id: str | None = None
    referenced: list[dict[str, str]] = field(default_factory=list)
    lang: str | None = None
    metrics: SocialMetricData = field(default_factory=SocialMetricData)
    media: list[dict[str, Any]] = field(default_factory=list)  # attached photos / video previews (X's media expansion)

    @property
    def content_type(self) -> str:
        kinds = {r.get("type") for r in self.referenced}
        if "replied_to" in kinds:
            return "reply"
        if "quoted" in kinds:
            return "quote"
        if "retweeted" in kinds:
            return "repost"
        return "post"


@dataclass(slots=True)
class SocialPage:
    posts: list[SocialPostData]
    profiles: dict[str, SocialProfileData]
    newest_id: str | None
