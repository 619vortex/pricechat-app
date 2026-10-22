"""Local mock of the Telegram Bot API for development and demos. NOT used in production.

Serves a synthetic community conversation through the same Bot API methods Price
Chat calls (getMe, getUpdates, getChat), so the real ingestion pipeline can be
exercised without a Telegram account.

Run:  backend\\.venv\\Scripts\\python.exe scripts\\mock_telegram.py   (listens on 127.0.0.1:8081)
Then start a backend with TELEGRAM_BOT_TOKEN=<MOCK_TOKEN> and TELEGRAM_API_BASE=http://127.0.0.1:8081
(see scripts\\run-demo.ps1). POST /mock/say {"chat_id": ..., "text": ..., "user": ...} injects a live message.
"""

import random
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request

MOCK_TOKEN = "100000001:MockTokenForLocalDevelopmentOnly_xyz"
COMMUNITY = {"id": -1001000000001, "title": "Price Chat Community (demo)", "type": "supergroup"}
OTHER = {"id": -1001000000099, "title": "Some Other Group", "type": "supergroup"}

MEMBERS = [f"{n}" for n in (
    "Ava", "Ben", "Chloe", "Dev", "Eli", "Fatima", "Gus", "Hana", "Ivan", "Jade", "Kai", "Lena", "Milo", "Nora",
    "Omar", "Pia", "Quinn", "Rosa", "Sam", "Tara",
)]

# (hours_ago_start, hours_ago_end, messages)
SCRIPT: list[tuple[float, float, list[str]]] = [
    (40, 26, [  # yesterday: calmer
        "gm everyone", "gm gm", "Has anyone looked at ETH ETF flows this week?", "ETH ETF inflows were pretty strong on Monday",
        "ETH ETF flows slowed down after Tuesday though", "what does Price Chat actually do?",
        "Price Chat sends alerts and shares market discussion, that's how I use it",
        "price alerts on Android are not working for me since the update", "same, my Android alerts stopped after updating the app",
        "is there a fix coming for the Android alert bug?", "lol", "When is the next AMA?", "I think the AMA is on Thursday",
        "BTC funding rates look normal today", "Watch out, someone is DMing people pretending to be an admin, it's a scam",
        "yes I got that fake admin DM too, offering an airdrop", "never click airdrop links from DMs",
        "Would love a weekly summary of the best discussions here", "a weekly recap post would be great",
        "how do I set a price alert for SOL?", "can someone explain what Price Chat is for?",
    ]),
    (10, 0, [  # today: funding rates heating up
        "gm", "Bitcoin funding rates are spiking hard this morning", "funding rates on BTC just flipped very positive",
        "why are bitcoin funding rates so high right now?", "high funding usually means longs are crowded",
        "BTC funding rates spiking again, careful with leverage", "funding rate on Binance perp is the highest in weeks",
        "what does a high funding rate actually mean for price?", "can someone explain funding rates simply?",
        "funding rates explained: longs pay shorts when funding is positive", "I closed my long because funding got too expensive",
        "ETH funding rates are rising too", "Are funding rates a reliable signal or just noise?",
        "what does Price Chat do exactly? just joined", "welcome! it's a community for market chat and alerts",
        "Android price alerts still broken for me", "the Android notifications bug is still there on version 2.3",
        "how do I set price alerts in the app?", "Another fake admin is DMing people about an airdrop, report them",
        "so what is Price Chat actually about?", "funding rates cooling a bit now?", "nah funding still elevated on BTC",
        "Content idea: a short video explaining funding rates would help newcomers", "a simple funding rates explainer would be useful",
        "🔥🔥🔥", "When is the next AMA again?", "is the AMA still on Thursday?",
    ]),
]


def build_updates() -> list[dict[str, Any]]:
    rng = random.Random(42)
    now = int(time.time())
    updates: list[dict[str, Any]] = []
    update_id = 500_000
    message_id = 1
    for start_h, end_h, lines in SCRIPT:
        span = (start_h - end_h) * 3600
        for i, text in enumerate(lines):
            date = now - int(start_h * 3600) + int(span * (i + 1) / (len(lines) + 1))
            name = MEMBERS[rng.randrange(len(MEMBERS))]
            user_id = 70_000 + MEMBERS.index(name)
            msg = {"message_id": message_id, "date": date, "chat": COMMUNITY,
                   "from": {"id": user_id, "is_bot": False, "first_name": name}, "text": text}
            if i > 0 and rng.random() < 0.25:
                msg["reply_to_message"] = {"message_id": message_id - 1, "date": date - 60, "chat": COMMUNITY}
            updates.append({"update_id": update_id, "message": msg})
            update_id += 1
            message_id += 1
    # Messages from a chat that is NOT authorised: Price Chat must ignore them.
    updates.append({"update_id": update_id, "message": {"message_id": 1, "date": now - 600, "chat": OTHER,
                    "from": {"id": 1, "first_name": "Stranger"}, "text": "private discussion in another group"}})
    return updates


app = FastAPI(title="Mock Telegram Bot API")
STATE: dict[str, Any] = {"updates": build_updates(), "next_update": 900_000, "next_message": 10_000}


def _check(token: str) -> None:
    if token != MOCK_TOKEN:
        raise HTTPException(401, detail={"ok": False, "error_code": 401, "description": "Unauthorized"})


@app.post("/bot{token}/getMe")
async def get_me(token: str) -> dict[str, Any]:
    _check(token)
    return {"ok": True, "result": {"id": 100000001, "is_bot": True, "first_name": "Price Chat Demo Bot", "username": "pricechat_demo_bot"}}


@app.post("/bot{token}/getChat")
async def get_chat(token: str, request: Request) -> dict[str, Any]:
    _check(token)
    chat_id = (await request.json()).get("chat_id")
    for chat in (COMMUNITY, OTHER):
        if chat["id"] == chat_id:
            return {"ok": True, "result": chat}
    return {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}


@app.post("/bot{token}/getUpdates")
async def get_updates(token: str, request: Request) -> dict[str, Any]:
    _check(token)
    body = await request.json()
    offset = body.get("offset")
    if offset is not None:
        STATE["updates"] = [u for u in STATE["updates"] if u["update_id"] >= offset]
    return {"ok": True, "result": STATE["updates"][: body.get("limit", 100)]}


@app.post("/mock/replay")
async def replay() -> dict[str, Any]:
    """Re-send the scripted history (new update ids, same message ids). Use after authorising the chat."""
    fresh = build_updates()
    for u in fresh:
        u["update_id"] = STATE["next_update"]
        STATE["next_update"] += 1
    STATE["updates"].extend(fresh)
    return {"ok": True, "queued": len(fresh)}


@app.post("/mock/say")
async def say(request: Request) -> dict[str, Any]:
    """Inject a live message: {"text": "...", "user": "Ava", "chat_id": optional}."""
    body = await request.json()
    name = body.get("user", "Ava")
    chat = COMMUNITY if body.get("chat_id", COMMUNITY["id"]) == COMMUNITY["id"] else OTHER
    msg = {"message_id": STATE["next_message"], "date": int(time.time()), "chat": chat,
           "from": {"id": 70_000 + abs(hash(name)) % 1000, "is_bot": False, "first_name": name}, "text": body["text"]}
    STATE["updates"].append({"update_id": STATE["next_update"], "message": msg})
    STATE["next_update"] += 1
    STATE["next_message"] += 1
    return {"ok": True, "queued": len(STATE["updates"])}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8081, log_level="warning")
