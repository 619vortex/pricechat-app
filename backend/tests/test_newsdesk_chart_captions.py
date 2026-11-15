"""Chart-post captions (owner, 2026-11-15, after post 181 listed all 13 assets of its own chart under it): one strong headline and at most
one takeaway, never the chart's data again, at most three assets, one cashtag at most on X and none on Telegram, the sources and
@thepricechat on one discreet last line, a licence's photo credit kept, Telegram-mobile formatting. The checks still pass on every one."""

import re
from html.parser import HTMLParser
from typing import Any

import pytest

from app.models.newsdesk import NdEvent
from app.newsdesk import checks, clean, datapost, network
from tests import newsdesk_chart_cases as cases

TAG = re.compile(r"(?<![\w$])\$[A-Z]{2,6}\b")
ASSET_NAMES = ("Tesla", "Solana", "Bitcoin", "Ether", "Nvidia", "Apple", "gold", "silver", "Coinbase", "Strategy", "S&P 500", "Nasdaq", "WTI")


class _Tg(HTMLParser):
    """Telegram's caption HTML: only the tags we use, balanced, links with an https href."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.bad: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ("b", "a"):
            self.bad.append(tag)
        if tag == "a" and not any(k == "href" and (v or "").startswith("https://") for k, v in attrs):
            self.bad.append("a without https href")
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.bad.append(f"unbalanced </{tag}>")


def telegram_ok(html: str) -> list[str]:
    p = _Tg()
    p.feed(html)
    p.close()
    return p.bad + ([f"unclosed {p.stack}"] if p.stack else []) + (["over 1024"] if len(html) > 1024 else [])


async def caption(name: str) -> tuple[Any, Any]:
    """(package, chart-captioned telegram (html, plain)) as the lane sends it."""
    if name in cases.STORIES:  # a story whose picture is a collage with candles: compose_visual re-captions it as a chart post
        ev, rows = cases.story_event(name, NdEvent)
        pkg = await network.package(ev, settings=None, rows=rows, x_links=True, style="clean")
        clean.chart_recaption(pkg, ev, rows, x_links=True)  # what telegram.compose_visual does once the picture has the chart
        return pkg, (pkg.telegram_html, pkg.telegram_plain)
    ev = cases.data_event(name, NdEvent)
    pkg = await network.package(ev, settings=None, rows=[], x_links=True, style="clean")
    return pkg, (pkg.telegram_html, pkg.telegram_plain)


@pytest.mark.parametrize("name", list(cases.POSTS))
async def test_a_chart_post_has_a_short_clean_caption(name: str) -> None:
    pkg, (html, plain) = await caption(name)
    assert pkg.ok, [c for c in pkg.checks if not c["passed"]]  # the factual checks still pass
    body, _, footer = plain.rpartition("\n\n")
    parts = body.split("\n\n")  # a chart caption: the headline, then the takeaway on its own line
    assert len(parts) <= 2, body  # one headline, at most one takeaway
    assert not TAG.search(plain), plain  # no cashtags on a Telegram chart post
    assert len(TAG.findall(pkg.x_text)) <= 1, pkg.x_text  # X: one at most
    named = [n for n in ASSET_NAMES if re.search(rf"\b{re.escape(n)}\b", body, re.I)]
    assert len(named) <= 3, named
    for n in named:
        assert len(re.findall(rf"\b{re.escape(n)}\b", body, re.I)) <= 2, (n, body)  # never the whole chart again
    assert len(re.findall(r"[+−-]?\d[\d,.]*%", body)) <= 3, body
    assert "\n" not in footer and footer.endswith("· @thepricechat") and footer.startswith("Source: "), footer  # one discreet last line
    assert html.count("\n\n") <= 3 and html.rstrip().endswith('· <a href="https://t.me/thepricechat">@thepricechat</a>'), html
    assert telegram_ok(html) == [], telegram_ok(html)
    assert "Read the full story" not in plain and "Understand the crypto stories" not in plain
    assert len(plain) < 300, len(plain)


async def test_the_weekly_ranking_reads_like_the_owners_example() -> None:
    pkg, (html, plain) = await caption("ranking")
    assert plain == ("The week's winners and losers: Tesla +8.9%, Solana −17.4%.\n\nUS stocks rose while crypto fell the most.\n\n"
                     "Source: Yahoo Finance · @thepricechat"), plain
    assert html.endswith('🔗 Yahoo Finance · <a href="https://t.me/thepricechat">@thepricechat</a>'), html  # our own measured data: credited by name


async def test_the_checks_still_see_every_figure_the_card_shows() -> None:
    ev = cases.data_event("ranking", NdEvent)
    told = ev.facts["told"]
    for s, r in cases.RANKING_7D.items():
        assert datapost.pct(r) in told, (s, r)  # the full list stays in what the checks verify against
    assert all(isinstance(b[1], float) for b in ev.facts["data"]["bars"]) and len(ev.facts["data"]["bars"]) == 10  # the card is unchanged


def test_a_takeaway_only_says_what_every_member_of_the_group_did() -> None:
    rows = [("^GSPC", 2.5), ("^IXIC", -0.3), ("BTC-USD", -13.2), ("ETH-USD", -14.3), ("SOL-USD", -17.4), ("GC=F", -4.6), ("SI=F", -5.4), ("CL=F", -2.5)]
    assert datapost.group_line(rows) == "Crypto fell the most; US stocks held up best."  # the Nasdaq fell: stocks didn't "rise"
    assert datapost.group_line([("^GSPC", 1.0), ("BTC-USD", 2.0)]) == ""  # one member each: no group to speak of


def test_cashtags_once_each_and_limited() -> None:
    assert checks.limit_cashtags("Bitcoin $BTC rose; bitcoin $BTC then fell.", 1) == "Bitcoin $BTC rose; bitcoin then fell."
    assert checks.limit_cashtags("Bitcoin $BTC and Ether $ETH rose; $SOL too.", 1) == "Bitcoin $BTC and Ether rose; SOL too."
    assert checks.limit_cashtags("Bitcoin $BTC and Ether $ETH rose.", 0) == "Bitcoin and Ether rose."


def test_a_licensed_photos_credit_stays_on_its_own_line_before_the_last_one() -> None:
    html = 'Story.\n\n🔗 <a href="https://x.test/a">CNA</a> · <a href="https://t.me/thepricechat">@thepricechat</a>'
    plain = "Story.\n\nSource: CNA · @thepricechat"
    meta = {"credit": "Shlomo Spitzer (שלמה שפיצר) / Wikimedia Commons", "licence": "CC BY 2.5", "page": "https://commons.wikimedia.org/wiki/File:x.jpg"}
    h, p = clean.with_credit(html, plain, meta)  # the font can't draw Hebrew: the licence's credit goes in the caption
    assert p == "Story.\n\nPhoto: Shlomo Spitzer (שלמה שפיצר) / Wikimedia Commons, CC BY 2.5\n\nSource: CNA · @thepricechat"
    assert telegram_ok(h) == []
    assert clean.with_credit(html, plain, {"credit": "Jane Doe / Wikimedia Commons", "licence": "CC BY 4.0"}) == (html, plain)  # drawn on the picture


async def test_x_follows_the_chart_rules_when_the_picture_has_the_chart() -> None:
    pkg, _ = await caption("btc70k")
    assert "in 24H" not in pkg.x_text and len(TAG.findall(pkg.x_text)) == 1, pkg.x_text  # the chart shows the move; one cashtag
