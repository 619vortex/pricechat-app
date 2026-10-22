"""Test doubles for Telegram and for structured model output. No network, no real accounts."""

import time
import json
from datetime import UTC, datetime, timedelta
import re
from collections.abc import Callable
from typing import Any

import httpx

from app.community.classifier import HeuristicClassifier

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
BOT = {"id": 999, "is_bot": True, "first_name": "Price Chat Bot", "username": "pricechat_test_bot"}


class FakeTelegram:
    """In-memory Bot API. Updates below the requested offset are 'acknowledged' and dropped."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.next_update_id = 1000
        self.next_message_id: dict[int, int] = {}
        self.chats: dict[int, dict[str, Any]] = {}
        self.member_counts: dict[int, int] = {}  # getChatMemberCount answers
        self.error: tuple[int, dict[str, Any]] | None = None  # (status, body)
        self.network_down = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.seen_urls: list[str] = []
        self.handles: dict[str, int] = {}  # "@channel" -> chat id
        self.members: dict[tuple[int, int], str] = {}  # (chat id, user id) -> getChatMember status
        self.member_list_hidden: set[int] = set()  # channels where the bot is not an admin (Telegram refuses getChatMember)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def add_chat(self, chat_id: int, title: str, chat_type: str = "supergroup") -> None:
        self.chats[chat_id] = {"id": chat_id, "title": title, "type": chat_type}

    def message(
        self,
        chat_id: int,
        text: str,
        user_id: int = 1,
        name: str = "Alice",
        date: int | None = None,  # default: an hour ago (topic statistics only count recent messages, so a fixed date goes stale)
        reply_to: int | None = None,
        edited_of: int | None = None,
        extra: dict[str, Any] | None = None,
        kind: str = "message",
    ) -> int:
        date = int(time.time()) - 3600 if date is None else date
        chat = self.chats.get(chat_id) or {"id": chat_id, "title": f"Chat {chat_id}", "type": "supergroup"}
        if edited_of is None:
            mid = self.next_message_id.get(chat_id, 1)
            self.next_message_id[chat_id] = mid + 1
        else:
            mid = edited_of
        msg: dict[str, Any] = {
            "message_id": mid,
            "date": date,
            "chat": chat,
            "from": {"id": user_id, "is_bot": False, "first_name": name},
            "text": text,
        }
        if reply_to is not None:
            msg["reply_to_message"] = {"message_id": reply_to, "date": date, "chat": chat}
        if edited_of is not None:
            msg["edit_date"] = date + 60
            kind = "edited_message"
        msg.update(extra or {})
        self.updates.append({"update_id": self.next_update_id, kind: msg})
        self.next_update_id += 1
        return mid

    def raw(self, update: dict[str, Any]) -> None:
        update.setdefault("update_id", self.next_update_id)
        self.next_update_id = max(self.next_update_id, update["update_id"]) + 1
        self.updates.append(update)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.seen_urls.append(str(request.url))
        if self.network_down:
            raise httpx.ConnectError("connection refused", request=request)
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if self.error is not None:
            status, payload = self.error
            return httpx.Response(status, json=payload)
        if f"/bot{TOKEN}/" not in request.url.path:
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": BOT})
        if method == "getChatMemberCount":
            if body["chat_id"] not in self.member_counts:
                return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True, "result": self.member_counts[body["chat_id"]]})
        if isinstance(body.get("chat_id"), str) and body["chat_id"].startswith("@"):
            body["chat_id"] = self.handles.get(body["chat_id"].lower(), 0)
        if method == "getChatMember":
            if body["chat_id"] in self.member_list_hidden:
                return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: member list is inaccessible"})
            status = self.members.get((body["chat_id"], body["user_id"]), "left")
            return httpx.Response(200, json={"ok": True, "result": {"status": status, "user": {"id": body["user_id"]}}})
        if method == "getChat":
            chat = self.chats.get(body["chat_id"])
            if chat is None:
                return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True, "result": chat})
        if method == "sendMessage":  # only ever the sign-in confirmation (see TelegramBotClient.confirm_sign_in)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {"id": body.get("chat_id")}}})
        if method == "getUpdates":
            offset = body.get("offset")
            if offset is not None:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
            return httpx.Response(200, json={"ok": True, "result": self.updates[: body.get("limit", 100)]})
        return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})


# --- structured model output ---------------------------------------------------------------

_LINE = re.compile(r"^\[m(\d+)\] (Member \d+)(?: \(reply[^)]*\))?: (.*)$", re.M)


def _messages(prompt: str) -> list[tuple[str, str]]:
    return [(f"m{mid}", text) for mid, _, text in _LINE.findall(prompt)]


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text
        self.author_is_bot = False


def structured_reply(schema_title: str, prompt: str) -> dict[str, Any]:
    msgs = _messages(prompt)
    ids = [mid for mid, _ in msgs]
    first = msgs[0][1] if msgs else "the community"
    if schema_title == "_Batch" and re.search(r"^\[p\d+\]", prompt, re.M):  # X relevance filter
        items = []
        for pid, text in re.findall(r"^\[(p\d+)\] [^:]*: (.*)$", prompt, re.M):
            relevant = any(w in text.lower() for w in ("funding", "price chat", "pricechat", "alerts", "question"))
            items.append({"id": pid, "relevant": relevant, "kind": "conversation", "topic": "funding" if "funding" in text.lower() else "", "relevance": 0.8 if relevant else 0.1})
        return {"items": items}
    if schema_title == "_Advice":
        return {"worthwhile": "giveaway" not in prompt.lower(), "topic": "Funding rates", "why": "A large account is asking about funding rates.",
                "contribution": "Explain what high funding means, without predictions.", "suggested_action": "reply", "relevance": 8,
                "like": True, "follow_author": "bigtrader" in prompt.lower(), "engagement_reason": "Relevant market discussion"}
    if schema_title == "_Variants":
        return {"worth_it": True, "confidence": "high", "why": "adds the mechanics",
                "variants": [
                    {"style": "direct", "text": "Funding above zero means longs pay shorts. When it stays high, longs are crowded."},
                    {"style": "conversational", "text": "Yeah, funding this high usually means a lot of people are leaning long at once."},
                    {"style": "insight-driven", "text": "High funding plus rising open interest is the combo that tends to end in a squeeze."},
                ]}
    if schema_title == "_Draft":
        if "number-bait" in prompt and "rejected because" not in prompt:
            return {"text": "Over $2B left ETH funds last week, per CoinShares.", "worth_it": True, "confidence": "high", "why": "data"}
        if "placeholder-bait" in prompt and "rejected because" not in prompt:
            return {"text": "Here is the recap: [link to recap]", "worth_it": True, "confidence": "medium", "why": "share"}
        if "self-claim" in prompt:
            return {"text": "Our team is fixing the Android alerts right now.", "worth_it": True, "confidence": "high", "why": "support"}
        if "nothing-to-add" in prompt:
            return {"text": "", "worth_it": False, "confidence": "low", "why": "Just a meme"}
        if "praise-bait" in prompt and "rejected because" not in prompt:
            return {"text": "Great insight! Absolutely agree with this.", "worth_it": True, "confidence": "high", "why": "agreement"}
        return {"text": "High funding means longs pay shorts to keep positions open; when it stays elevated, crowded longs "
                        "are the usual reason. Open interest is worth watching alongside it.",
                "worth_it": True, "confidence": "high", "why": "Explains what the number means without predicting price"}
    if schema_title == "_Synthesis":
        return {"recommendation": "Both audiences are discussing this; consider an explainer."}
    if schema_title == "_Verdict":
        same = "btc" in prompt.lower() and "bitcoin" in prompt.lower()
        return {"verdict": "same" if same else "related", "reason": "Both describe the same coin" if same else "Connected but distinct"}
    if schema_title == "_Drafts":
        styles = re.search(r"one for each style: ([^.]*)\.", prompt)
        names = [s.strip() for s in styles.group(1).split(",")] if styles else ["best fit"]
        return {"variants": [{"style": s, "title": f"Draft ({s})", "text": f"Funding rates explained simply ({s}). This is not financial advice.", "notes": s} for s in names]}
    if schema_title == "_Batch":
        items = []
        for mid, text in msgs:
            label = HeuristicClassifier.one(_Msg(text))  # type: ignore[arg-type]
            items.append({"id": mid, "category": label.category.value, "is_question": label.is_question})
        return {"items": items}
    if schema_title == "_TopicName":
        words = [w for w in re.findall(r"[a-zA-Z]{4,}", first)][:3]
        return {"name": " ".join(words).title() or "Topic", "description": f"Members discuss {first[:60]}", "keywords": [w.lower() for w in words]}
    if schema_title == "_Canonical":
        return {"question": first.rstrip("?") + "?"}
    if schema_title == "SummaryOutput":
        point = {"text": f"Members talked about {first[:50]}", "message_ids": ids[:3] + ["m999999"]}  # one bogus id
        return {
            "headline": "Members discussed recent topics",
            "overview": "The community was active.",
            "themes": [point], "emerging": [], "questions": [{"text": "How does it work?", "message_ids": ids[:1]}],
            "concerns": [], "ideas": [], "content_suggestions": [{"text": "Explainer", "message_ids": ids[:2]}],
        }
    if schema_title == "_Insights":
        return {
            "insights": [
                {"type": "recurring_question", "title": "Members ask how it works", "content": "Members frequently ask how the product works.",
                 "importance": 0.95, "confidence": 0.8, "message_ids": ids[:2]},
                {"type": "observation", "title": "Unsupported claim", "content": "This has no evidence.",
                 "importance": 0.5, "confidence": 0.2, "message_ids": ["m424242"]},
            ]
        }
    if schema_title == "_Opportunity":
        return {
            "worthwhile": "funding" in prompt.lower() or "work" in prompt.lower(),
            "reason": "Members keep discussing it",
            "title": "Explain the trending topic",
            "why": "Discussion increased today.",
            "angle": "Why members are suddenly talking about this",
            "platforms": ["x", "youtube_shorts"],
            "urgency": "high",
            "confidence": 0.7,
            "message_ids": ids[:3],
        }
    if schema_title == "_Answer":
        cited = re.findall(r"^\[([mstk]\d+)\]", prompt, re.M)
        return {"answer": f"Members discussed this [{cited[0]}]." if cited else "Not enough evidence.", "cited": cited[:2] + ["m999999"], "sufficient": bool(cited)}
    raise AssertionError(f"no fake for schema {schema_title}")


Responder = Callable[[str, str], dict[str, Any]]


# --- fake X API v2 ---------------------------------------------------------------------------

X_BEARER = "AAAAAAAAAAAAAAAAAAAAAFakeBearerForTests0000000000"
X_USER_KEYS = {"api_key": "fakeconsumerkey123", "api_secret": "fakeconsumersecret1234567890",
               "access_token": "1600-fakeaccesstoken12345", "access_token_secret": "fakeaccesssecret1234567890"}
OUR_ID = "1600"


def _an_hour_ago() -> str:
    """A fresh post time: screening only looks at the last 48 hours, so a fixed date goes stale."""
    return (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeX:
    """In-memory X API v2. Endpoints can be forbidden (access level), rate limited or usage capped."""

    def __init__(self) -> None:
        self.users = {
            "1600": {"id": "1600", "username": "pricechat", "name": "Price Chat", "public_metrics": {"followers_count": 1200, "following_count": 80, "tweet_count": 300}},
            "2001": {"id": "2001", "username": "bigtrader", "name": "Big Trader", "public_metrics": {"followers_count": 250000}},
            "2002": {"id": "2002", "username": "newsdesk", "name": "News Desk", "public_metrics": {"followers_count": 90000}},
            "2244994945": {"id": "2244994945", "username": "XDevelopers", "name": "Developers", "public_metrics": {"followers_count": 600000}},
        }
        self.tweets: list[dict[str, Any]] = []
        self.forbidden: set[str] = set()
        self.rate_limited: set[str] = set()
        self.usage_capped = False
        self.no_credit = False
        self.access_level = "read-write"  # what X reports for user-context requests (x-access-level)
        self.write_status: int | None = None  # force an error status for write requests
        self.writes: list[tuple[str, dict[str, Any]]] = []  # every write request received
        self.like_confirms = True  # False: X answers a like without confirming it
        self.calls: list[tuple[str, dict[str, str], str]] = []
        self.next_id = 9000

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def tweet(self, author: str, text: str, kind: str = "search", reply_to_us: bool = False, metrics: dict | None = None,
              created_at: str | None = None) -> str:
        self.next_id += 1
        tid = str(self.next_id)
        self.tweets.append({
            "id": tid, "text": text, "author_id": author, "created_at": created_at or _an_hour_ago(),
            "conversation_id": tid, "lang": "en", "_kind": kind,
            "public_metrics": metrics or {"like_count": 10, "reply_count": 2, "retweet_count": 1, "quote_count": 0, "impression_count": 500},
            **({"in_reply_to_user_id": OUR_ID, "referenced_tweets": [{"type": "replied_to", "id": "1"}]} if reply_to_us else {}),
        })
        return tid

    def _page(self, tweets: list[dict[str, Any]], params: dict[str, str]) -> dict[str, Any]:
        since = int(params.get("since_id") or 0)
        limit = int(params.get("max_results", 10))
        rows = [t for t in tweets if int(t["id"]) > since][:limit]
        clean = [{k: v for k, v in t.items() if not k.startswith("_")} for t in rows]
        authors = {t["author_id"] for t in rows}
        body: dict[str, Any] = {"meta": {"result_count": len(rows)}}
        if rows:
            body["data"] = clean
            body["includes"] = {"users": [self.users[a] for a in authors if a in self.users]}
            body["meta"]["newest_id"] = max(t["id"] for t in rows)
        return body

    def _handle(self, request: httpx.Request) -> httpx.Response:
        response = self._dispatch(request)
        if request.headers.get("authorization", "").startswith("OAuth ") and self.access_level:
            response.headers["x-access-level"] = self.access_level
        return response

    def _write(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("OAuth ") or f'oauth_consumer_key="{X_USER_KEYS["api_key"]}"' not in auth:
            return httpx.Response(403, json={"title": "Unsupported Authentication", "type": "https://api.x.com/2/problems/client-forbidden"})
        body = json.loads(request.content or b"{}")
        self.writes.append((request.url.path, body))
        if self.access_level == "read":
            return httpx.Response(403, json={"title": "Forbidden", "detail": "Your client app is not configured with the appropriate oauth1 app permissions for this endpoint."})
        if self.write_status:
            return httpx.Response(self.write_status, json={"title": "Error", "detail": "forced failure"})
        path = request.url.path
        if path == "/2/tweets":
            tid = self.tweet(OUR_ID, body.get("text", ""), kind="own",
                             metrics={"like_count": 3, "reply_count": 1, "retweet_count": 0, "quote_count": 0, "impression_count": 120})
            return httpx.Response(201, json={"data": {"id": tid, "text": body.get("text", ""), "edit_history_tweet_ids": [tid]}})
        if path.endswith("/likes"):
            return httpx.Response(200, json={"data": {"liked": self.like_confirms}})
        if path.endswith("/retweets"):
            return httpx.Response(200, json={"data": {"retweeted": True}})
        if path.endswith("/following"):
            return httpx.Response(200, json={"data": {"following": True, "pending_follow": False}})
        return httpx.Response(404, json={"title": "Not Found Error"})

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return self._write(request)
        path = request.url.path
        params = dict(request.url.params)
        auth = request.headers.get("authorization", "")
        self.calls.append((path, params, "user" if auth.startswith("OAuth ") else "app"))
        if auth.startswith("Bearer ") and auth != f"Bearer {X_BEARER}":
            return httpx.Response(401, json={"title": "Unauthorized", "type": "about:blank", "status": 401, "detail": "Unauthorized"})
        if auth.startswith("OAuth ") and f'oauth_consumer_key="{X_USER_KEYS["api_key"]}"' not in auth:
            return httpx.Response(401, json={"title": "Unauthorized", "status": 401})
        key = path.split("/2/", 1)[-1]
        family = "search" if "search" in key else "mentions" if key.endswith("mentions") else "users/tweets" if key.endswith("/tweets") else "me" if key == "users/me" else "lookup" if key.startswith("users/by") else "tweets"
        if self.no_credit:
            return httpx.Response(402, json={"title": "CreditsDepleted", "detail": "Your enrolled account does not have any credits", "type": "https://api.x.com/2/problems/credits"})
        if self.usage_capped:
            return httpx.Response(429, json={"title": "Usage Capped", "type": "https://api.x.com/2/problems/usage-capped", "detail": "UsageCapExceeded"})
        if family in self.rate_limited:
            return httpx.Response(429, json={"title": "Too Many Requests", "type": "https://api.x.com/2/problems/rate-limit-exceeded"},
                                  headers={"x-rate-limit-limit": "60", "x-rate-limit-remaining": "0", "x-rate-limit-reset": str(int(time.time()) + 900)})
        if family in self.forbidden:
            return httpx.Response(403, json={"title": "Forbidden", "type": "https://api.x.com/2/problems/client-forbidden", "detail": "Your access does not include this endpoint"})
        headers = {"x-rate-limit-limit": "300", "x-rate-limit-remaining": "299", "x-rate-limit-reset": "1790000900"}
        if family == "me":
            if not auth.startswith("OAuth "):
                return httpx.Response(403, json={"title": "Unsupported Authentication", "type": "https://api.x.com/2/problems/client-forbidden"})
            return httpx.Response(200, json={"data": self.users[OUR_ID]}, headers=headers)
        if family == "lookup":
            name = key.rsplit("/", 1)[-1].lower()
            user = next((u for u in self.users.values() if u["username"].lower() == name), None)
            if user is None:
                return httpx.Response(200, json={"errors": [{"title": "Not Found Error", "type": "https://api.x.com/2/problems/resource-not-found", "detail": "Could not find user"}]})
            return httpx.Response(200, json={"data": user}, headers=headers)
        if family == "search":
            q = params.get("query", "").lower().replace('"', "").replace("-is:retweet", "").strip()
            if q.startswith("from:"):
                rows = [t for t in self.tweets if self.users.get(t["author_id"], {}).get("username", "").lower() == q[5:].strip()]
            else:
                rows = [t for t in self.tweets if t["_kind"] == "search" and all(w in t["text"].lower() for w in q.split())]
            return httpx.Response(200, json=self._page(rows, params), headers=headers)
        if family == "mentions":
            return httpx.Response(200, json=self._page([t for t in self.tweets if t["_kind"] == "mention"], params), headers=headers)
        if family == "users/tweets":
            uid = key.split("/")[1]
            return httpx.Response(200, json=self._page([t for t in self.tweets if t["author_id"] == uid], params), headers=headers)
        if family == "tweets":
            ids = params.get("ids", "").split(",")
            if ids == ["20"]:
                return httpx.Response(200, json={"data": [{"id": "20", "text": "just setting up my twttr", "author_id": "12"}]}, headers=headers)
            rows = []
            for t in self.tweets:
                if t["id"] in ids:
                    row = {k: v for k, v in t.items() if not k.startswith("_")}
                    row["non_public_metrics"] = {"impression_count": 1500, "url_link_clicks": 12, "user_profile_clicks": 7}
                    rows.append(row)
            return httpx.Response(200, json={"data": rows}, headers=headers)
        return httpx.Response(404, json={"title": "Not Found Error"})


# --- fake Telegram account (client API) -------------------------------------------------------------

TG_API_ID = 1234567
TG_API_HASH = "0123456789abcdef0123456789abcdef"
TG_PHONE = "+10000000042"
TG_CODE = "24680"


class FakeTelegramAccount:
    """In-memory stand-in for a Telegram user session. Records every chat it is asked to read."""

    def __init__(self) -> None:
        from datetime import datetime, timezone

        from app.telegram.account import DialogInfo

        self.password: str | None = None  # set to enable two-step verification
        self.valid_session: str | None = None
        self.sessions = 0
        self.logged_out = False
        self.flood: int | None = None
        self.read_chats: list[int] = []
        self.codes_sent = 0
        self.qr_scanned = False  # the member scanned the QR in their Telegram app (QR login)
        self.now = datetime.now(timezone.utc)
        self.dialog_list = [
            DialogInfo(-1001111, "Price Chat Community", "supergroup", "pricechatcommunity", 1200),
            DialogInfo(-1002222, "Price Chat Announcements", "channel", "pricechat_news", 5000),
            DialogInfo(-1003333, "Unrelated Group", "supergroup", None, 40),
        ]
        self.history: dict[int, list] = {d.chat_id: [] for d in self.dialog_list}
        self.next_id = 100

    def add(self, chat_id: int, text: str, minutes_ago: float = 0, user_id: int = 7, name: str = "Member", **kw: Any):  # type: ignore[no-untyped-def]
        from datetime import timedelta

        from app.telegram.parser import ChatInfo, ParsedMessage

        d = next(x for x in self.dialog_list if x.chat_id == chat_id)
        self.next_id += 1
        msg = ParsedMessage(0, ChatInfo(chat_id, d.title, d.chat_type, d.username), self.next_id, False, kw.get("thread_id"),
                            kw.get("reply_to"), user_id, name, False, self.now - timedelta(minutes=minutes_ago), None, text,
                            kw.get("media"), None, None)
        self.history[chat_id].append(msg)
        return msg

    def factory(self, api_id: int, api_hash: str, session: str | None) -> "FakeAccountGateway":
        return FakeAccountGateway(self, api_id, api_hash, session)


class FakeAccountGateway:
    def __init__(self, tg: FakeTelegramAccount, api_id: int, api_hash: str, session: str | None) -> None:
        self.tg, self.api_ok = tg, (api_id == TG_API_ID and api_hash == TG_API_HASH)
        self.session = session
        self.state = "authorized" if session and session == tg.valid_session else "new"

    def _check(self) -> None:
        from app.telegram.account import FloodWait, InvalidApiCredentials

        if not self.api_ok:
            raise InvalidApiCredentials("Telegram rejected the API ID / API Hash")
        if self.tg.flood:
            raise FloodWait(self.tg.flood)

    async def connect(self) -> None:
        self._check()

    async def disconnect(self) -> None:
        return None

    async def is_authorized(self) -> bool:
        return self.state == "authorized" and self.session == self.tg.valid_session

    async def send_code(self, phone: str) -> str:
        from app.telegram.account import InvalidPhone

        self._check()
        if phone != TG_PHONE:
            raise InvalidPhone("Telegram did not accept this phone number")
        self.tg.codes_sent += 1
        return "hash-abc"

    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> None:
        from app.telegram.account import InvalidCode, PasswordNeeded

        if code != TG_CODE or phone_code_hash != "hash-abc":
            raise InvalidCode("That code is not correct")
        if self.tg.password:
            self.state = "password"
            raise PasswordNeeded("two-step verification")
        self._authorize()

    async def sign_in_password(self, password: str) -> None:
        from app.telegram.account import InvalidPassword

        if self.state != "password" or password != self.tg.password:
            raise InvalidPassword("That two-step verification password is not correct")
        self._authorize()

    async def qr_check(self) -> tuple[bool, str | None]:
        from app.telegram.account import PasswordNeeded

        self._check()
        if not self.tg.qr_scanned:
            return False, f"tg://login?token=fake{self.tg.sessions}"
        if self.tg.password and self.state != "password":
            self.state = "password"
            raise PasswordNeeded("two-step verification")
        if self.state != "password":
            self._authorize()
            return True, None
        return False, None

    def _authorize(self) -> None:
        self.tg.sessions += 1
        self.session = f"1Aa-fake-session-{self.tg.sessions}"
        self.tg.valid_session = self.session
        self.state = "authorized"

    async def me(self):  # type: ignore[no-untyped-def]
        from app.telegram.account import AccountIdentity

        return AccountIdentity(555, "pricechat_admin", "Price Chat Admin", "••••42")

    async def dialogs(self, limit: int = 200) -> list:
        self._check()
        return list(self.tg.dialog_list)

    async def messages(self, chat, *, min_id: int = 0, since=None, limit: int = 500) -> list:  # type: ignore[no-untyped-def]
        self._check()
        self.tg.read_chats.append(chat.chat_id)
        rows = [m for m in self.tg.history[chat.chat_id] if m.message_id > min_id and (since is None or m.sent_at >= since)]
        return rows[-limit:]

    async def estimate_since(self, chat, since) -> int:  # type: ignore[no-untyped-def]
        return len([m for m in self.tg.history[chat.chat_id] if m.sent_at >= since])

    async def log_out(self) -> None:
        self.tg.logged_out = True
        self.tg.valid_session = None

    def session_string(self) -> str:
        return self.session or ""


# --- YouTube Data API v3 + Google OAuth (in memory) -------------------------------------------------------

YT_KEY = "AIzaFakeYouTubeKeyForTestsOnly0000000000"
YT_CHANNEL = "UCpricechat0000000000000"  # UC + 22 characters
YT_OTHER_CHANNEL = "UCotherchannel0000000000"
YT_CLIENT_ID = "1234-fake.apps.googleusercontent.com"
YT_CLIENT_SECRET = "GOCSPX-fake-secret"


class FakeYouTube:
    """Channels, an uploads playlist (newest first) and videos. Records every request (quota)."""

    def __init__(self) -> None:
        self.channels: dict[str, dict[str, Any]] = {
            YT_CHANNEL: {"title": "Price Chat", "handle": "@pricechat", "subscribers": 1500, "videos": 0},
            YT_OTHER_CHANNEL: {"title": "Someone Else", "handle": "@someoneelse", "subscribers": 99, "videos": 0},
        }
        self.uploads: dict[str, list[str]] = {YT_CHANNEL: [], YT_OTHER_CHANNEL: []}
        self.videos: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.error: tuple[int, dict[str, Any]] | None = None
        self.mine: list[str] = [YT_CHANNEL]  # channels of the signed-in Google account
        self.token_error: str | None = None
        self.tokens_issued = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def upload(self, vid: str, title: str, seconds: int = 45, size: tuple[int, int] | None = (1080, 1920), channel: str = YT_CHANNEL,
               published: str = "2026-10-03T09:00:00Z", privacy: str = "public", description: str = "", tags: list[str] | None = None,
               views: int | None = 100, thumbs: bool = True) -> None:
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        dur = "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "") + (f"{s}S" if s or not (h or m) else "")
        self.videos[vid] = {"title": title, "duration": dur, "size": size, "channel": channel, "published": published,
                            "privacy": privacy, "description": description, "tags": tags or [], "views": views, "thumbs": thumbs}
        self.uploads[channel].insert(0, vid)
        self.channels[channel]["videos"] += 1

    def delete(self, vid: str) -> None:
        v = self.videos.pop(vid)
        self.uploads[v["channel"]].remove(vid)

    def _visible(self, vid: str, oauth: bool) -> bool:
        return oauth or self.videos[vid]["privacy"] != "private"  # an API key never sees private videos

    def _video(self, vid: str) -> dict[str, Any]:
        v = self.videos[vid]
        thumbs = {"default": {"url": f"https://i.ytimg.com/vi/{vid}/default.jpg", "width": 120, "height": 90},
                  "high": {"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "width": 480, "height": 360}} if v["thumbs"] else {}
        item: dict[str, Any] = {
            "id": vid,
            "snippet": {"channelId": v["channel"], "title": v["title"], "description": v["description"], "publishedAt": v["published"],
                        "tags": v["tags"], "thumbnails": thumbs},
            "contentDetails": {"duration": v["duration"]},
            "status": {"privacyStatus": v["privacy"], "uploadStatus": "processed"},
            "statistics": {k: str(n) for k, n in (("viewCount", v["views"]), ("likeCount", 7), ("commentCount", 2)) if n is not None},
            "player": {"embedHtml": "<iframe></iframe>"},
        }
        if v["size"]:
            w, h = v["size"]
            item["player"].update({"embedWidth": str(int(8192 * w / h)), "embedHeight": "8192"})
        return item

    def _channel(self, cid: str) -> dict[str, Any]:
        c = self.channels[cid]
        return {"id": cid, "snippet": {"title": c["title"], "customUrl": c["handle"], "thumbnails": {"default": {"url": "https://yt3.test/c.jpg"}}},
                "statistics": {"subscriberCount": str(c["subscribers"]), "videoCount": str(c["videos"]), "viewCount": "5000", "hiddenSubscriberCount": False},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}}}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return self._token(request)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        self.calls.append((endpoint, params))
        if self.error:
            return httpx.Response(self.error[0], json=self.error[1])
        auth = request.headers.get("authorization", "")
        oauth = auth.startswith("Bearer ")
        if oauth and not auth.startswith("Bearer access-"):
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials", "errors": [{"reason": "authError"}]}})
        if not oauth and params.get("key") != YT_KEY:
            return httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                                       "errors": [{"reason": "badRequest"}]}})
        if endpoint == "channels":
            if params.get("mine") == "true":
                if not oauth:
                    return httpx.Response(401, json={"error": {"code": 401, "message": "Login Required", "errors": [{"reason": "required"}]}})
                ids = self.mine
            elif params.get("forHandle"):
                ids = [cid for cid, c in self.channels.items() if c["handle"].lower() == params["forHandle"].lower()]
            else:
                ids = [i for i in params.get("id", "").split(",") if i in self.channels]
            return httpx.Response(200, json={"items": [self._channel(i) for i in ids]})
        if endpoint == "playlistItems":
            cid = "UC" + params["playlistId"][2:]
            ids = [v for v in self.uploads.get(cid, []) if self._visible(v, oauth)]
            start = int(params.get("pageToken") or 0)
            body: dict[str, Any] = {"items": [{"contentDetails": {"videoId": v}} for v in ids[start:start + 50]]}
            if start + 50 < len(ids):
                body["nextPageToken"] = str(start + 50)
            return httpx.Response(200, json=body)
        if endpoint == "videos":
            ids = [i for i in params.get("id", "").split(",") if i in self.videos and self._visible(i, oauth)]
            return httpx.Response(200, json={"items": [self._video(i) for i in ids]})
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})

    def _token(self, request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs

        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if self.token_error:
            return httpx.Response(400, json={"error": self.token_error})
        if form.get("client_id") != YT_CLIENT_ID or form.get("client_secret") != YT_CLIENT_SECRET:
            return httpx.Response(401, json={"error": "invalid_client"})
        self.tokens_issued += 1
        body = {"access_token": f"access-{self.tokens_issued}", "expires_in": 3599, "token_type": "Bearer"}
        if form.get("grant_type") == "authorization_code":
            if form.get("code") != "good-code" or not form.get("code_verifier"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            body["refresh_token"] = "refresh-token-1"
        return httpx.Response(200, json=body)
