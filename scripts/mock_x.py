"""Local mock of the X API v2 (read endpoints only) for development and demos. NOT used in production.

Serves a synthetic set of accounts and posts through the same endpoints Price Chat calls
(users/by/username, users/me, tweets/search/recent, users/:id/tweets, users/:id/mentions, tweets),
so the real X pipeline can be exercised without an X developer account or any cost.
Write endpoints accept supervised demo actions and publish nothing.

Run:  backend\\.venv\\Scripts\\python.exe scripts\\mock_x.py   (listens on 127.0.0.1:8082)
Then start a backend with X_API_BASE=http://127.0.0.1:8082 (see scripts\\run-demo.ps1) and on
X › Connection paste the demo keys printed below.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DEMO_BEARER = "AAAAAAAAAAAAAAAAAAAAAMockBearerForLocalDemoOnly000000"
DEMO_KEYS = {"api_key": "mockconsumerkey0001", "api_secret": "mockconsumersecret000000001",
             "access_token": "1600-mockaccesstoken0001", "access_token_secret": "mockaccesssecret000000001"}
OUR_ID = "1600"

USERS = {
    "1600": {"id": "1600", "username": "pricechat", "name": "Price Chat", "public_metrics": {"followers_count": 1200, "following_count": 80, "tweet_count": 300}},
    "2001": {"id": "2001", "username": "bigtrader", "name": "Big Trader", "verified": True, "public_metrics": {"followers_count": 250000, "following_count": 900, "tweet_count": 41000}},
    "2002": {"id": "2002", "username": "cryptonewsdesk", "name": "Crypto News Desk", "public_metrics": {"followers_count": 90000, "following_count": 120, "tweet_count": 88000}},
    "2003": {"id": "2003", "username": "chartlearner", "name": "Chart Learner", "public_metrics": {"followers_count": 640, "following_count": 410, "tweet_count": 2100}},
    "2244994945": {"id": "2244994945", "username": "XDevelopers", "name": "Developers", "public_metrics": {"followers_count": 600000, "following_count": 1, "tweet_count": 4000}},
    "2004": {"id": "2004", "username": "defidaily", "name": "DeFi Daily", "public_metrics": {"followers_count": 31000, "following_count": 300, "tweet_count": 9000}},
}

# (author, hours ago, kind, text, likes, replies, reposts)
POSTS: list[tuple[str, float, str, str, int, int, int]] = [
    ("2001", 1.0, "public", "Bitcoin funding rates are spiking hard today. Longs are crowded, be careful with leverage.", 820, 140, 95),
    ("2002", 1.5, "public", "BTC perpetual funding rates hit the highest level in weeks across major exchanges.", 310, 40, 60),
    ("2003", 2.0, "public", "Can someone explain what high funding rates actually mean for bitcoin price? Trying to learn.", 12, 9, 0),
    ("2004", 3.0, "public", "ETH funding rates are rising too. Leverage is building up on both majors.", 150, 22, 18),
    ("2001", 5.0, "public", "Funding rates explained: when funding is positive, longs pay shorts. Elevated funding = crowded longs.", 1200, 88, 210),
    ("2003", 4.0, "mention", "@pricechat do your alerts cover funding rate spikes? Would love that.", 4, 1, 0),
    ("2002", 6.0, "mention", "@pricechat great community recap on ETH ETF flows this week", 25, 3, 2),
    ("2004", 8.0, "public", "Fake admin accounts are DMing people about airdrops again. Never click airdrop links from DMs.", 400, 30, 120),
    ("2003", 9.0, "mention", "@pricechat the Android price alerts stopped working for me after the update", 2, 1, 0),
    ("1600", 20.0, "own", "Weekly recap: ETH ETF flows, a scam warning about fake admins, and the next AMA on Thursday.", 45, 6, 8),
    ("1600", 44.0, "own", "New in Price Chat: set price alerts for any coin in two taps.", 80, 12, 15),
]


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_tweets() -> list[dict[str, Any]]:
    out = []
    for i, (author, hours, kind, text, likes, replies, reposts) in enumerate(sorted(POSTS, key=lambda p: -p[1])):
        tid = str(1_900_000_000_000_000_000 + i)
        out.append({
            "id": tid, "text": text, "author_id": author, "created_at": _iso(hours), "conversation_id": tid, "lang": "en", "_kind": kind,
            "public_metrics": {"like_count": likes, "reply_count": replies, "retweet_count": reposts, "quote_count": reposts // 4,
                               "bookmark_count": likes // 10, "impression_count": likes * 40},
            **({"entities": {"mentions": [{"username": "pricechat", "id": OUR_ID}]}} if kind == "mention" else {}),
        })
    return out


TWEETS = build_tweets()
app = FastAPI(title="Mock X API v2 (demo only)")


def _rl() -> dict[str, str]:
    return {"x-rate-limit-limit": "300", "x-rate-limit-remaining": "299", "x-rate-limit-reset": str(int(time.time()) + 900)}


def _page(rows: list[dict[str, Any]], params: dict[str, str]) -> JSONResponse:
    since = int(params.get("since_id") or 0)
    limit = int(params.get("max_results", 10))
    rows = sorted([t for t in rows if int(t["id"]) > since], key=lambda t: -int(t["id"]))[:limit]
    body: dict[str, Any] = {"meta": {"result_count": len(rows)}}
    if rows:
        body["data"] = [{k: v for k, v in t.items() if not k.startswith("_")} for t in rows]
        body["includes"] = {"users": [USERS[a] for a in {t["author_id"] for t in rows}]}
        body["meta"]["newest_id"] = max(t["id"] for t in rows)
    return JSONResponse(body, headers=_rl())


@app.middleware("http")
async def auth(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path.startswith("/2/"):
        header = request.headers.get("authorization", "")
        ok = header == f"Bearer {DEMO_BEARER}" or (header.startswith("OAuth ") and f'oauth_consumer_key="{DEMO_KEYS["api_key"]}"' in header)
        if not ok:
            return JSONResponse({"title": "Unauthorized", "type": "about:blank", "status": 401, "detail": "Unauthorized"}, status_code=401)
        request.state.user_context = header.startswith("OAuth ")
    return await call_next(request)


@app.get("/2/users/me")
def me(request: Request) -> JSONResponse:
    if not request.state.user_context:
        return JSONResponse({"title": "Unsupported Authentication", "type": "https://api.x.com/2/problems/client-forbidden", "status": 403}, status_code=403)
    return JSONResponse({"data": USERS[OUR_ID]}, headers=_rl())


@app.get("/2/users/by/username/{username}")
def lookup(username: str) -> JSONResponse:
    user = next((u for u in USERS.values() if u["username"].lower() == username.lower()), None)
    if user is None:
        return JSONResponse({"errors": [{"title": "Not Found Error", "type": "https://api.x.com/2/problems/resource-not-found", "detail": f"Could not find user with username: [{username}]."}]})
    return JSONResponse({"data": user}, headers=_rl())


@app.get("/2/tweets/search/recent")
def search(request: Request) -> JSONResponse:
    params = dict(request.query_params)
    q = params.get("query", "").lower()
    for token in ("-is:retweet", "-is:reply", "lang:en"):
        q = q.replace(token, "")
    q = q.replace('"', "").replace("(", "").replace(")", "").strip()
    if q.startswith("from:"):
        name = q[5:].split()[0]
        rows = [t for t in TWEETS if USERS[t["author_id"]]["username"].lower() == name]
    else:
        alternatives = [a.split() for a in q.split(" or ")]
        rows = [t for t in TWEETS if t["_kind"] == "public" and any(all(w in t["text"].lower() for w in words) for words in alternatives)]
    return _page(rows, params)


@app.get("/2/users/{uid}/tweets")
def user_tweets(uid: str, request: Request) -> JSONResponse:
    return _page([t for t in TWEETS if t["author_id"] == uid], dict(request.query_params))


@app.get("/2/users/{uid}/mentions")
def mentions(uid: str, request: Request) -> JSONResponse:
    return _page([t for t in TWEETS if t["_kind"] == "mention"], dict(request.query_params))


@app.get("/2/tweets")
def tweets(request: Request) -> JSONResponse:
    ids = request.query_params.get("ids", "").split(",")
    if ids == ["20"]:
        return JSONResponse({"data": [{"id": "20", "text": "just setting up my twttr", "author_id": "12"}]}, headers=_rl())
    rows = []
    for t in TWEETS:
        if t["id"] in ids:
            row = {k: v for k, v in t.items() if not k.startswith("_")}
            if request.state.user_context and t["author_id"] == OUR_ID:
                likes = t["public_metrics"]["like_count"]
                row["non_public_metrics"] = {"impression_count": likes * 55, "url_link_clicks": likes // 5, "user_profile_clicks": likes // 8}
            rows.append(row)
    return JSONResponse({"data": rows}, headers=_rl())


# --- writes (demo only): accepted with user-context auth, nothing is published anywhere --------------


@app.post("/2/tweets")
async def create_tweet(request: Request) -> JSONResponse:
    if not request.state.user_context:
        return JSONResponse({"title": "Unsupported Authentication", "status": 403}, status_code=403)
    body = await request.json()
    tid = str(1_900_000_000_000_001_000 + len(TWEETS))
    TWEETS.append({"id": tid, "text": body.get("text", ""), "author_id": OUR_ID, "created_at": _iso(0), "conversation_id": tid,
                   "lang": "en", "_kind": "own", "public_metrics": {"like_count": 0, "reply_count": 0, "retweet_count": 0,
                                                                    "quote_count": 0, "bookmark_count": 0, "impression_count": 0}})
    return JSONResponse({"data": {"id": tid, "text": body.get("text", "")}}, status_code=201)


@app.post("/2/users/{uid}/likes")
def like(uid: str, request: Request) -> JSONResponse:
    return JSONResponse({"data": {"liked": True}}) if request.state.user_context else JSONResponse({"status": 403}, status_code=403)


@app.post("/2/users/{uid}/retweets")
def repost(uid: str, request: Request) -> JSONResponse:
    return JSONResponse({"data": {"retweeted": True}}) if request.state.user_context else JSONResponse({"status": 403}, status_code=403)


@app.post("/2/users/{uid}/following")
def follow(uid: str, request: Request) -> JSONResponse:
    return JSONResponse({"data": {"following": True, "pending_follow": False}}) if request.state.user_context else JSONResponse({"status": 403}, status_code=403)


@app.middleware("http")
async def access_level(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    if request.headers.get("authorization", "").startswith("OAuth "):
        response.headers["x-access-level"] = "read-write"
    return response


if __name__ == "__main__":
    print("Mock X API on http://127.0.0.1:8082 (demo only, read endpoints only)")
    print(f"  Bearer Token: {DEMO_BEARER}")
    for k, v in DEMO_KEYS.items():
        print(f"  {k}: {v}")
    uvicorn.run(app, host="127.0.0.1", port=8082, log_level="warning")
