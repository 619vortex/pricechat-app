"""Private test of the News Desk's article previews (owner, 2026-11-23: "Enable source-article link previews, testing first in a private
Telegram chat"). Sends ONE message per article to a private chat the owner names, exactly as the desk would: Telegram itself shows the
article's preview picture above the words. Nothing goes to the public channel; nothing is stored.

Run with the production bot's key injected by Railway (never printed, never written to disk), from backend/:

    railway run python ../ops/preview-test/preview_test.py --chat <private chat id or @private_channel> [article URLs...]

With no URLs it uses the source articles of Telegram posts 197, 198 and 207 (posts that went out without a picture). Before sending, each
article is checked the way the desk checks it (app/newsdesk/preview.check) and the result is printed beside Telegram's answer.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.newsdesk import preview  # noqa: E402
from app.telegram.client import TelegramBotClient  # noqa: E402

DEFAULT = [
    ("The Guardian", "Global stock markets slump as Anthropic IPO fails to stem AI sell-off",
     "https://www.theguardian.com/business/2026/nov/20/global-stock-markets-slump-anthropic-ipo-ai-sell-off"),
    ("Al Jazeera", "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south",
     "https://www.aljazeera.com/news/2026/11/19/saudi-led-coalition-intercepts-houthi-missiles-targeting-riyadh-and-south"),
    ("The Moscow Times", "U.S., China can help end Ukraine war, Trump tells Xi before trilateral with Putin",
     "https://www.themoscowtimes.com/2026/11/19/us-china-can-help-end-ukraine-war-trump-tells-xi-before-trilateral-with-putin-a94307"),
    ("Financial Times", "Russia and Ukraine's drone war sends optical fibre prices soaring",
     "https://www.ft.com/content/339c3a72-bce5-44ed-bc6c-c8a8199cfb40"),
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", required=True, help="a PRIVATE chat: your user id (after pressing Start with the bot) or a private test channel")
    ap.add_argument("urls", nargs="*")
    a = ap.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set: run this through `railway run` from backend/.")
        return 2
    items = [("", "", u) for u in a.urls] or DEFAULT
    client = TelegramBotClient(token)
    try:
        chat = await client.get_chat(a.chat)
        if chat.get("type") != "private" and chat.get("username"):  # a public channel or group has a public @username
            print(f"Refused: @{chat.get('username')} is public. Use a private chat (your DM with the bot, or a private channel).")
            return 2
        for outlet, head, url in items:
            p = await preview.check(url)
            print(f"\n{outlet or preview.site_of(url)}: desk check {'OK' if p.ok else 'REFUSED'} {p.why} {p.image_url} {p.width}x{p.height}")
            text = f"<b>PREVIEW TEST (not posted)</b>\n\n{html.escape(head or p.title or url)}\n\n<a href=\"{html.escape(url, quote=True)}\">{html.escape(outlet or p.site)}</a>"
            sent = await client.send_message(chat["id"], text, html=True, silent=True, preview_url=url)
            print(f"  Telegram: message {sent.get('message_id')} sent; open the chat to see whether the preview shows a large picture")
    finally:
        await client.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
