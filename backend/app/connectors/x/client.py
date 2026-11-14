"""Client for the official X API v2 (https://docs.x.com/x-api).

- App-only Bearer token for public data (search, user lookup, timelines, mentions).
- OAuth 1.0a user context (optional) for the account's own data: /2/users/me,
  private metrics of own posts, the cheaper "owned reads", and write actions.
- Write methods (post, reply, quote, like, repost, follow) refuse to run without a single-use
  `ExecutionPermit` from the SocialActionGuard, which is only issued for a human-approved
  request that passed every safety check. Nothing else in the code can write to X.

X bills per resource returned (pay-per-use). Every call is pre-checked against the
local budget and its estimated cost is reported through `on_usage`.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from app.connectors.x.errors import (
    XAuthError,
    XBadRequestError,
    XBudgetExceededError,
    XCreditsError,
    XForbiddenError,
    XNotConfiguredError,
    XNotFoundError,
    XRateLimitError,
    XUnavailableError,
    XUsageCapError,
)
from app.connectors.x.oauth1 import authorization_header
from app.security.credentials import XCredentials

if TYPE_CHECKING:
    from app.social.guard import ExecutionPermit

logger = logging.getLogger(__name__)

# Published pay-per-use rates (USD per resource returned), https://docs.x.com/x-api/getting-started/pricing
PRICE_POST_READ = 0.005
PRICE_USER_READ = 0.010
PRICE_OWNED_READ = 0.001
PRICE_TRENDS_REQUEST = 0.010  # per request, whatever it returns
# Writes: Price Chat's own configurable estimate (settings.x_write_cost_estimate_usd); X does not report it per request.
DEFAULT_WRITE_COST_ESTIMATE = 0.01

TWEET_FIELDS = "created_at,author_id,conversation_id,in_reply_to_user_id,referenced_tweets,public_metrics,lang"
USER_FIELDS = "username,name,public_metrics,verified"
PRIVATE_FIELDS = "non_public_metrics,organic_metrics"

UsageCallback = Callable[[str, int, int, float], None]  # endpoint, posts, users, est_cost
BudgetCheck = Callable[[float], None]  # raises XBudgetExceededError


class XClient:
    def __init__(
        self,
        creds: XCredentials,
        api_base: str = "https://api.x.com",
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        on_usage: UsageCallback | None = None,
        budget_check: BudgetCheck | None = None,
        write_cost_estimate: float = DEFAULT_WRITE_COST_ESTIMATE,
    ) -> None:
        if not creds.configured:
            raise XNotConfiguredError("X is not configured: add a Bearer token on the X › Connection page.")
        self._creds = creds
        self.api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=10.0), transport=transport)
        self.on_usage = on_usage
        self.budget_check = budget_check
        self.rate_limits: dict[str, dict[str, Any]] = {}
        self.write_cost_estimate = write_cost_estimate
        # OAuth 1.0a access level X reports for user-context requests ("read", "read-write", ...), if any.
        self.access_level: str | None = None

    def __repr__(self) -> str:
        return f"XClient(api_base={self.api_base!r}, {self._creds!r})"

    @property
    def has_user_context(self) -> bool:
        return self._creds.user_context

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- endpoints (all GET) ---------------------------------------------------------------

    async def user_by_username(self, username: str) -> dict[str, Any]:
        body = await self._get(f"/2/users/by/username/{username.lstrip('@')}", {"user.fields": USER_FIELDS},
                               est_cost=PRICE_USER_READ, endpoint="users/by/username")
        return body["data"]

    async def me(self) -> dict[str, Any]:
        if not self.has_user_context:
            raise XNotConfiguredError("Account (user-context) keys are needed for this; add them on the Connection page.")
        body = await self._get("/2/users/me", {"user.fields": USER_FIELDS}, est_cost=PRICE_USER_READ, endpoint="users/me", user=True)
        return body["data"]

    async def search_recent(self, query: str, max_results: int = 10, since_id: str | None = None, *, rich: bool = False,
                            until_id: str | None = None) -> dict[str, Any]:
        """`rich` also asks for author avatars and attached media (same billing: posts + users returned)."""
        params = {"query": query, "max_results": str(max(10, min(100, max_results))), "tweet.fields": TWEET_FIELDS + (",attachments,entities" if rich else ""),
                  "expansions": "author_id" + (",attachments.media_keys" if rich else ""),
                  "user.fields": USER_FIELDS + (",profile_image_url" if rich else "")}
        if rich:
            params["media.fields"] = "type,url,preview_image_url,width,height"
        if since_id:
            params["since_id"] = since_id
        if until_id:
            params["until_id"] = until_id  # older than this post (backfill)
        return await self._get("/2/tweets/search/recent", params, est_cost=PRICE_POST_READ * int(params["max_results"]),
                               endpoint="tweets/search/recent", count_posts=True)

    async def user_posts(self, user_id: str, max_results: int = 10, since_id: str | None = None, own: bool = False) -> dict[str, Any]:
        # our own timeline also brings the posts we replied to / quoted (and their authors) in the same response: the
        # context the Kiwi Passport shows above a Price Chat reply
        # and its attached photos / video previews (same billing: X prices the posts returned, not their fields)
        expansions = "author_id,referenced_tweets.id,referenced_tweets.id.author_id,attachments.media_keys" if own else "author_id"
        params = {"max_results": str(max(5, min(100, max_results))), "tweet.fields": TWEET_FIELDS + (",attachments" if own else ""),
                  "expansions": expansions, "user.fields": USER_FIELDS}
        if own:
            params["media.fields"] = "type,url,preview_image_url,width,height,alt_text"
        if since_id:
            params["since_id"] = since_id
        use_user = own and self.has_user_context  # owned reads are cheaper with user context
        price = PRICE_OWNED_READ if use_user else PRICE_POST_READ
        return await self._get(f"/2/users/{user_id}/tweets", params, est_cost=price * int(params["max_results"]),
                               endpoint="users/tweets", count_posts=True, user=use_user, owned=use_user)

    async def mentions(self, user_id: str, max_results: int = 10, since_id: str | None = None) -> dict[str, Any]:
        params = {"max_results": str(max(5, min(100, max_results))), "tweet.fields": TWEET_FIELDS,
                  "expansions": "author_id", "user.fields": USER_FIELDS}
        if since_id:
            params["since_id"] = since_id
        return await self._get(f"/2/users/{user_id}/mentions", params, est_cost=PRICE_POST_READ * int(params["max_results"]),
                               endpoint="users/mentions", count_posts=True, user=self.has_user_context)

    async def reposted_by(self, post_id: str, expected: int) -> dict[str, Any]:
        """Accounts that reposted a post (GET /2/tweets/:id/retweeted_by). Billed per account returned."""
        n = max(1, min(100, expected))
        return await self._get(f"/2/tweets/{post_id}/retweeted_by", {"max_results": str(n), "user.fields": "username"},
                               est_cost=PRICE_USER_READ * n, endpoint="tweets/retweeted_by", count_users=True)

    async def public_posts(self, ids: list[str]) -> dict[str, Any]:
        """Look up public posts by id (app-only Bearer is enough)."""
        params = {"ids": ",".join(ids[:100]), "tweet.fields": "created_at,public_metrics", "expansions": "author_id", "user.fields": "username,name"}
        return await self._get("/2/tweets", params, est_cost=PRICE_POST_READ * len(ids[:100]), endpoint="tweets/lookup", count_posts=True)

    async def own_post_metrics(self, ids: list[str]) -> dict[str, Any]:
        """Public + private metrics for our own recent posts (user context; X provides private metrics for ~30 days)."""
        if not self.has_user_context:
            raise XNotConfiguredError("Account (user-context) keys are needed for private metrics.")
        params = {"ids": ",".join(ids[:100]), "tweet.fields": f"public_metrics,{PRIVATE_FIELDS},created_at"}
        return await self._get("/2/tweets", params, est_cost=PRICE_OWNED_READ * len(ids[:100]), endpoint="tweets/lookup",
                               count_posts=True, user=True, owned=True)

    async def trends(self, woeid: int = 1, max_trends: int = 30) -> dict[str, Any]:
        """X's trends for a place (GET /2/trends/by/woeid/:woeid; 1 = worldwide). Billed per request ($0.010 on pay-per-use, 2026-11).
        A plan without access answers 403 (XForbiddenError): callers show "not available on your X plan" and stop asking."""
        params = {"max_trends": str(max(1, min(50, max_trends))), "trend.fields": "trend_name,tweet_count"}
        return await self._get(f"/2/trends/by/woeid/{int(woeid)}", params, est_cost=PRICE_TRENDS_REQUEST, endpoint="trends/by/woeid",
                               flat_cost=PRICE_TRENDS_REQUEST)

    # --- writes (only with a SocialActionGuard permit) ---------------------------------------------

    async def upload_image(self, data: bytes, *, permit: "ExecutionPermit") -> str:
        """One image for a post (X API v2 media upload, simple upload). Returns the media id to attach."""
        from app.models.enums import SocialAction
        from app.social.guard import require_permit

        require_permit(permit, SocialAction.POST, None)
        c = self._creds
        if not c.user_context:
            raise XNotConfiguredError("X write actions need user-context credentials (Access Token and Access Token Secret).")
        url = f"{self.api_base}/2/media/upload"
        headers = {"User-Agent": "PriceChat/0.5",  # multipart bodies are not part of the OAuth 1.0a signature base string
                   "Authorization": authorization_header("POST", url, {}, c.api_key, c.api_secret, c.access_token, c.access_token_secret)}  # type: ignore[arg-type]
        mime = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
        try:
            response = await self._client.post(url, headers=headers, files={"media": ("news.jpg", data, mime)}, data={"media_category": "tweet_image", "media_type": mime})
        except httpx.TimeoutException:
            raise XUnavailableError("X did not answer the media upload in time") from None
        except httpx.TransportError as exc:
            raise XUnavailableError(f"Cannot reach X ({type(exc).__name__})") from None
        self._record_rate_limit("media/upload", response)
        out = self._parse(response, "media/upload")
        media_id = str((out.get("data") or {}).get("id") or out.get("media_id_string") or "")
        if not media_id:
            raise XUnavailableError("X accepted the upload but returned no media id")
        return media_id

    async def create_post(self, text: str, *, permit: "ExecutionPermit", reply_to: str | None = None,
                          quote_of: str | None = None, media_ids: list[str] | None = None) -> dict[str, Any]:
        """Post, reply (reply_to = post id) or quote (quote_of = post id). Returns {"id", "text"}."""
        from app.models.enums import SocialAction
        from app.social.guard import require_permit

        action = SocialAction.REPLY if reply_to else SocialAction.POST
        require_permit(permit, action, reply_to)  # quote posts are proposed as posts that quote a post
        body: dict[str, Any] = {"text": text}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        if quote_of:
            body["quote_tweet_id"] = quote_of
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        out = await self._post("/2/tweets", body, endpoint="posts/create")
        return out.get("data") or {}

    async def like(self, user_id: str, post_id: str, *, permit: "ExecutionPermit") -> dict[str, Any]:
        from app.models.enums import SocialAction
        from app.social.guard import require_permit

        require_permit(permit, SocialAction.LIKE, post_id)
        out = await self._post(f"/2/users/{user_id}/likes", {"tweet_id": post_id}, endpoint="likes/create")
        return out.get("data") or {}

    async def repost(self, user_id: str, post_id: str, *, permit: "ExecutionPermit") -> dict[str, Any]:
        from app.models.enums import SocialAction
        from app.social.guard import require_permit

        require_permit(permit, SocialAction.REPOST, post_id)
        out = await self._post(f"/2/users/{user_id}/retweets", {"tweet_id": post_id}, endpoint="reposts/create")
        return out.get("data") or {}

    async def follow(self, user_id: str, target_user_id: str, *, permit: "ExecutionPermit") -> dict[str, Any]:
        from app.models.enums import SocialAction
        from app.social.guard import require_permit

        require_permit(permit, SocialAction.FOLLOW, target_user_id)
        out = await self._post(f"/2/users/{user_id}/following", {"target_user_id": target_user_id}, endpoint="follows/create")
        return out.get("data") or {}

    async def _post(self, path: str, body: dict[str, Any], *, endpoint: str) -> dict[str, Any]:
        c = self._creds
        if not c.user_context:
            raise XNotConfiguredError("X write actions need user-context credentials (Access Token and Access Token Secret).")
        if self.budget_check is not None:
            self.budget_check(self.write_cost_estimate)
        url = f"{self.api_base}{path}"
        headers = {
            "User-Agent": "PriceChat/0.5",
            "Content-Type": "application/json",
            # JSON bodies are not part of the OAuth 1.0a signature base string.
            "Authorization": authorization_header("POST", url, {}, c.api_key, c.api_secret, c.access_token, c.access_token_secret),  # type: ignore[arg-type]
        }
        try:
            response = await self._client.post(url, json=body, headers=headers)
        except httpx.TimeoutException:
            # The request may or may not have reached X; callers must not assume it failed or succeeded.
            raise XUnavailableError(f"X did not answer the {endpoint} request in time; check X before retrying") from None
        except httpx.TransportError as exc:
            raise XUnavailableError(f"Cannot reach X ({type(exc).__name__})") from None
        self._record_rate_limit(endpoint, response)
        self._note_access_level(response)
        out = self._parse(response, endpoint)
        if self.on_usage is not None:
            self.on_usage(f"write/{endpoint}", 0, 0, self.write_cost_estimate)
        return out

    def _note_access_level(self, response: httpx.Response) -> None:
        level = response.headers.get("x-access-level")
        if level:
            self.access_level = level.lower()

    # --- transport ---------------------------------------------------------------------------

    async def _get(
        self, path: str, params: dict[str, str], *, est_cost: float, endpoint: str,
        count_posts: bool = False, user: bool = False, owned: bool = False, count_users: bool = False, flat_cost: float | None = None,
    ) -> dict[str, Any]:
        if self.budget_check is not None:
            self.budget_check(est_cost)  # may raise XBudgetExceededError before anything is spent
        url = f"{self.api_base}{path}"
        headers = {"User-Agent": "PriceChat/0.4"}
        if user or not self._creds.bearer_token:
            c = self._creds
            if not c.user_context:
                raise XNotConfiguredError("X credentials are incomplete for this request.")
            headers["Authorization"] = authorization_header(
                "GET", url, params, c.api_key, c.api_secret, c.access_token, c.access_token_secret  # type: ignore[arg-type]
            )
        else:
            headers["Authorization"] = f"Bearer {self._creds.bearer_token}"
        try:
            response = await self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException:
            raise XUnavailableError(f"X did not respond in time ({endpoint})") from None
        except httpx.TransportError as exc:
            raise XUnavailableError(f"Cannot reach X ({type(exc).__name__})") from None

        self._record_rate_limit(endpoint, response)
        if user or not self._creds.bearer_token:
            self._note_access_level(response)
        body = self._parse(response, endpoint)

        posts = len(body.get("data") or []) if count_posts and isinstance(body.get("data"), list) else (1 if count_posts and body.get("data") else 0)
        users = len((body.get("includes") or {}).get("users") or []) + (1 if endpoint.startswith("users/") and not count_posts else 0)
        if count_users and isinstance(body.get("data"), list):
            users = len(body["data"])
        cost = posts * (PRICE_OWNED_READ if owned else PRICE_POST_READ) + users * PRICE_USER_READ if flat_cost is None else flat_cost
        if self.on_usage is not None:
            self.on_usage(endpoint, posts, users, round(cost, 4))
        return body

    def _record_rate_limit(self, endpoint: str, response: httpx.Response) -> None:
        h = response.headers
        if "x-rate-limit-remaining" in h:
            reset = h.get("x-rate-limit-reset")
            self.rate_limits[endpoint] = {
                "limit": int(h.get("x-rate-limit-limit", 0) or 0),
                "remaining": int(h.get("x-rate-limit-remaining", 0) or 0),
                "reset_at": datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat() if reset else None,
            }

    def _parse(self, response: httpx.Response, endpoint: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = {}
        status = response.status_code
        if 200 <= status < 300:
            if not body.get("data") and body.get("errors") and status == 200 and "meta" not in body:
                err = body["errors"][0]
                if "not found" in str(err.get("title", "")).lower() or err.get("type", "").endswith("resource-not-found"):
                    raise XNotFoundError(str(err.get("detail") or "Not found"))
            return body

        problem = str(body.get("type") or "")
        detail = str(body.get("detail") or body.get("title") or response.reason_phrase or "error")[:300]
        if status == 401:
            raise XAuthError("X rejected the credentials (401). Check or regenerate them in the X Developer Console.")
        if status == 403:
            raise XForbiddenError(f"Not available to this app or access level ({endpoint}): {detail}")
        if status == 404:
            raise XNotFoundError(detail)
        if status == 429:
            reset = response.headers.get("x-rate-limit-reset")
            reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc) if reset and reset.isdigit() else None
            if "usage-capped" in problem or "UsageCap" in detail:
                raise XUsageCapError(f"X usage cap or spending limit reached: {detail}", reset_at)
            raise XRateLimitError(f"X rate limit reached for {endpoint}", reset_at)
        if status == 402:
            raise XCreditsError(
                "Your X developer account has no API credit left (X: credits depleted). "
                "Add credit at console.x.com (Billing); Price Chat retries automatically.",
                datetime.now(timezone.utc) + timedelta(hours=1),
            )
        if status == 400:
            raise XBadRequestError(f"X rejected the request ({endpoint}): {detail}")
        if status >= 500:
            raise XUnavailableError(f"X server error {status} ({endpoint})")
        raise XBadRequestError(f"Unexpected X response {status} ({endpoint}): {detail}")


def raise_budget(message: str) -> None:
    raise XBudgetExceededError(message)
