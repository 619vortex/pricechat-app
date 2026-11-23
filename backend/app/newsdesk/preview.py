"""The source article's own link preview (owner, 2026-11-23).

When no licensed photograph of the story passes the Visual Editor, the post can still carry the real picture of the event: Telegram's
link preview of the source article. Telegram fetches the article page itself and shows the picture the publisher chose for sharing
(its og:image), large and above the words, with the outlet's name. The desk never downloads that picture to re-upload it: publishers'
photos are licensed to them, not to Price Chat (owner: "Don't download and republish copyrighted publisher images without permission").

Before a post relies on a preview, `check` reads the article the way Telegram's preview robot does and confirms the page offers a real,
large picture: a paywalled page (FT) or one that blocks robots (CoinDesk) offers none, and a logo, a placeholder or a small thumbnail is
refused. Only the image's header is read, to learn its size. A post with neither a licensed picture nor a working preview is held for
the owner's review: the desk never publishes text-only on its own.
"""

from __future__ import annotations

import html as _html
import io
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

UA = {"User-Agent": "TelegramBot (like TwitterBot)", "Accept": "text/html,application/xhtml+xml"}
PAGE_MAX = 1_500_000  # bytes of the article read at most (the <head> is near the top)
IMAGE_PEEK = 262_144  # bytes of the picture read, only to learn its size
MIN_WIDTH, MIN_HEIGHT = 600, 314  # smaller previews show as a thumbnail, not the large picture the post needs
NOT_A_PHOTO = re.compile(r"(logo|favicon|placeholder|default[-_]?(?:image|og|share)|sprite|icon[-_]|apple-touch|brand[-_]?image|"
                         r"social[-_]?share[-_]?default|fallback)", re.I)
TIMEOUT = 12.0


@dataclass
class Preview:
    url: str
    ok: bool
    why: str = ""
    image_url: str = ""
    width: int = 0
    height: int = 0
    site: str = ""
    title: str = ""

    def out(self) -> dict[str, Any]:
        return asdict(self)


def _meta(page: str, *names: str) -> str:
    for name in names:
        for rx in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']+)',
                   rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']'):
            m = re.search(rx, page, re.I)
            if m:
                return _html.unescape(m.group(1)).strip()
    return ""


def site_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def parse(page: str, url: str) -> tuple[str, str, str]:
    """(image URL, site name, title) the page offers for link previews."""
    head = page[: page.lower().find("</head>") + 7] if "</head>" in page.lower() else page
    image = _meta(head, "og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    site = _meta(head, "og:site_name") or site_of(url)
    title = _meta(head, "og:title", "twitter:title")
    return (urljoin(url, image) if image else ""), site, title


async def _read(client: httpx.AsyncClient, url: str, limit: int, headers: dict[str, str]) -> tuple[int, bytes, str]:
    async with client.stream("GET", url, headers=headers, timeout=TIMEOUT, follow_redirects=True) as r:
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) >= limit:
                break
        return r.status_code, bytes(buf[:limit]), str(r.url)


def _size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:  # noqa: BLE001 - a header PIL can't read from the first bytes
        return None


async def check(url: str, transport: Any = None) -> Preview:
    """Whether Telegram's preview of this article will show a real, large picture."""
    if not url.startswith("https://"):
        return Preview(url, False, "not an https link")
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            status, body, final = await _read(client, url, PAGE_MAX, UA)
            if status >= 400:
                return Preview(url, False, f"the article page answered {status} to a preview robot")
            page = body.decode("utf-8", "ignore")
            image, site, title = parse(page, final)
            if not image:
                return Preview(url, False, "the article offers no preview picture (paywall, robot block or none set)", site=site, title=title)
            if NOT_A_PHOTO.search(urlparse(image).path):
                return Preview(url, False, "the preview picture is a logo or a placeholder, not a photo", image, site=site, title=title)
            status, head, _ = await _read(client, image, IMAGE_PEEK, {"User-Agent": UA["User-Agent"]})
            if status >= 400:
                return Preview(url, False, f"the preview picture answered {status}", image, site=site, title=title)
            size = _size(head)
            if size is None:
                return Preview(url, False, "the preview picture could not be read", image, site=site, title=title)
            w, h = size
            if w < MIN_WIDTH or h < MIN_HEIGHT:
                return Preview(url, False, f"the preview picture is small ({w}x{h}): Telegram would show a thumbnail", image, w, h, site, title)
            return Preview(url, True, "", image, w, h, site, title)
    except httpx.HTTPError as e:
        return Preview(url, False, f"the article could not be read ({type(e).__name__})")


async def first_working(urls: list[str], transport: Any = None, limit: int = 3) -> tuple[Preview | None, list[Preview]]:
    """The first source article (in the post's own order) whose preview shows a real picture, and every look taken."""
    looks: list[Preview] = []
    for u in list(dict.fromkeys(u for u in urls if u))[:limit]:
        p = await check(u, transport)
        looks.append(p)
        if p.ok:
            return p, looks
        logger.info("article preview: %s: %s", site_of(u), p.why)
    return None, looks


def meta_for(p: Preview, base: dict[str, Any]) -> dict[str, Any]:
    """What the post's picture record says when the picture is the article's own preview (shown by Telegram, never re-uploaded)."""
    return {**base, "kind": "link_preview", "layout": "link_preview", "preview_url": p.url, "image_url": p.image_url, "width": p.width,
            "height": p.height, "site": p.site, "page": p.url, "title": p.title, "credit_line": f"Picture: {p.site} (article preview)",
            "licence": "Publisher's own link preview, shown by Telegram from the article page; not downloaded or re-uploaded by Price Chat",
            "source": "article preview"}
