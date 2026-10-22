"""Brand Manager trend scan. TEST FIXTURES only: made-up headlines served by httpx.MockTransport, made-up counts.
No real outlet is contacted."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import brands, defaults, hashtags, queue, trends
from app.brand_manager.discovery import Topic
from app.models.brand_manager import BmTrendDay, BmVideo

P = "/api/brand-manager"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def item(title: str, source: str, hours_ago: float = 1.0) -> dict[str, Any]:
    return {"title": title, "source": source, "link": f"https://example.test/{abs(hash((title, source)))}", "at": NOW - timedelta(hours=hours_ago)}


def day_row(db: Session, days_ago: int, term: str, docs: int, label: str | None = None, kind: str = "headline", **kw: Any) -> None:
    db.add(BmTrendDay(day=(NOW - timedelta(days=days_ago)).date().isoformat(), term=term, label=label or term.capitalize(), kind=kind, docs=docs,
                      sources=kw.get("sources", ["CNBC", "Financial Times"]),
                      examples=kw.get("examples", [{"title": f"{label or term} headline", "source": "CNBC", "url": "https://example.test/a", "at": None}])))


# --- words from headlines --------------------------------------------------------------------------------------------------


def test_headline_terms_keep_names_phrases_acronyms_and_cashtags_but_not_filler() -> None:
    t = trends.terms_of("SEC approves Bitcoin ETF as rate cut hopes lift $SOL and Nvidia's stock")
    assert {"sec", "bitcoin etf", "rate cut", "$sol", "nvidia"} <= set(t)
    assert t["sec"] == "SEC" and t["$sol"] == "$SOL"
    assert not {"approves", "as", "and", "stock", "lift", "bitcoin"} & set(t)  # verbs, filler, evergreen words


def test_a_trend_needs_two_headlines_from_two_outlets_and_syndicated_copies_count_once() -> None:
    rows = trends.count([
        item("Stablecoin rules land in Washington", "CoinDesk"), item("Banks warm to stablecoin settlement", "Financial Times"),
        item("Zcash surges again", "Decrypt"), item("Zcash privacy push", "Decrypt"),  # one outlet only
        item("Tariff shock hits shipping", "CNBC"), item("Tariff shock hits shipping", "BBC Business"),  # the same headline twice
    ])
    terms = {r["term"]: r for r in rows}
    assert terms["stablecoin"]["docs"] == 2 and terms["stablecoin"]["sources"] == ["CoinDesk", "Financial Times"]
    assert "zcash" not in terms and "tariff shock" not in terms


def test_a_word_inside_one_phrase_becomes_the_phrase() -> None:
    rows = trends.count([item("Jobless claims fall again", "CNBC"), item("Jobless claims surprise economists", "MarketWatch"),
                         item("Jobless claims and the Fed", "Financial Times")])
    terms = {r["term"] for r in rows}
    assert "jobless claims" in terms and "jobless" not in terms
    assert next(r for r in rows if r["term"] == "jobless claims")["label"] == "Jobless claims"


# --- today vs the past week --------------------------------------------------------------------------------------------------


def test_status_compares_today_with_the_previous_seven_days(db: Session) -> None:
    for d in range(1, 8):
        day_row(db, d, "tariffs", 4)
        day_row(db, d, "budget", 6)
        day_row(db, d, "filler", 1)  # makes every past day a scanned day
    day_row(db, 1, "agents", 1)
    for term, docs in (("tariffs", 4), ("budget", 2), ("genius act", 5), ("agents", 6)):
        day_row(db, 0, term, docs)
    db.commit()
    got = {t["term"]: t for t in trends.current(db, NOW)}
    assert got["genius act"]["status"] == "new" and got["genius act"]["baseline"] == 0
    assert got["agents"]["status"] == "rising" and got["agents"]["series"][-2:] == [1, 6]
    assert got["tariffs"]["status"] == "steady"
    assert got["budget"]["status"] == "cooling"
    order = [t["term"] for t in trends.current(db, NOW)]
    assert order[-1] == "budget"  # cooling last, never boosted


def test_without_history_terms_are_measuring(db: Session) -> None:
    day_row(db, 0, "tokenized", 4)
    db.commit()
    assert trends.current(db, NOW)[0]["status"] == "measuring"


def test_yesterdays_scan_is_used_until_todays_runs(db: Session) -> None:
    day_row(db, 1, "tokenized", 4)
    db.commit()
    t = trends.current(db, NOW)
    assert t[0]["term"] == "tokenized" and t[0]["day"] == (NOW - timedelta(days=1)).date().isoformat()


# --- matching, boosting, stories -----------------------------------------------------------------------------------------------


def live(term: str, label: str, status: str = "rising", docs: int = 5, kind: str = "headline", **kw: Any) -> dict[str, Any]:
    return {"term": term, "label": label, "kind": kind, "status": status, "docs": docs, "outlets": kw.get("outlets", ["CNBC", "Financial Times", "CoinDesk"]),
            "baseline": kw.get("baseline", 1.0), "momentum": 3.0, "strength": kw.get("strength", 8.0), "series": [1] * 7 + [docs],
            "examples": kw.get("examples", [{"title": f"{label} headline {i}", "source": s, "url": f"https://example.test/{i}", "at": None}
                                            for i, s in enumerate(["CNBC", "Financial Times", "CoinDesk"])]), "day": NOW.date().isoformat()}


def test_matching_respects_acronym_case_and_coin_tickers() -> None:
    sec = live("sec", "SEC")
    assert trends.match("The SEC sued", [sec]) and not trends.match("a second look", [sec]) and not trends.match("the sec said", [sec])
    pengu = live("pudgy penguins", "Pudgy Penguins (PENGU)", status="searched", kind="search",
                 examples=[{"title": "#2 most-searched", "source": "CoinGecko", "url": "", "rank": 2, "symbol": "PENGU"}])
    assert trends.match("PENGU jumps 20%", [pengu]) and trends.match("pudgy penguins floor", [pengu]) and trends.match("$PENGU", [pengu])
    assert not trends.match("rate cut odds", [live("rate cut", "Rate cut", status="cooling")])
    assert not trends.match("tariffs again", [live("tariffs", "Tariffs", status="steady")])  # steady words don't boost


def test_annotate_boosts_trend_riding_topics_and_records_the_words() -> None:
    riding = Topic("crypto_news", "Stablecoin bill passes the Senate", "k1", 1.0, {"headline": "Stablecoin bill passes", "facts": []})
    other = Topic("crypto_news", "Exchange outage", "k2", 1.0, {"headline": "Exchange outage", "facts": []})
    trends.annotate([riding, other], [live("stablecoin", "Stablecoin", strength=10)], learned=1.2)
    assert riding.score == round(1 + 0.4 * 1.2, 4) and other.score == 1.0
    assert riding.brief["trend"]["terms"][0] == {"term": "stablecoin", "label": "Stablecoin", "status": "rising", "kind": "headline",
                                                 "headlines_24h": 5, "outlets": 3}
    assert "trend" not in other.brief


def test_a_trend_story_only_uses_the_attributed_headlines_and_the_scan_counts() -> None:
    s = trends.story(live("genius act", "GENIUS Act", status="new", baseline=0), NOW)
    assert s.category == "finance_trends" and s.template == "breaking_news"
    texts = [f["text"] for f in s.brief["facts"]]
    assert texts[0] == "“GENIUS Act” appeared in 5 headlines from 3 outlets in the last 24 hours (CNBC, Financial Times, CoinDesk)."
    assert texts[1] == "It didn't appear in these outlets' headlines in the previous 7 days."
    assert texts[2] == "CNBC headline: “GENIUS Act headline 0”"
    assert s.brief["numbers"] == {"headlines_24h": 5, "outlets": 3} and len(s.brief["sources"]) == 3
    assert trends.story(live("thin", "Thin", docs=2), NOW) is None  # too few headlines for a story of its own
    assert trends.story(live("pengu", "PENGU", kind="search", status="searched"), NOW) is None


def test_learning_compares_trend_videos_with_the_rest() -> None:
    def v(views: int, label: str | None) -> Any:
        brief = {"trend": {"terms": [{"term": label.lower(), "label": label}]}} if label else {}
        return SimpleNamespace(brief=brief, metrics={"views": views})

    vids = [v(3000, "Stablecoin"), v(2500, "Stablecoin"), v(2000, "Tariffs"), v(1000, None), v(1000, None), v(1000, None)]
    out = trends.learn(vids, overall=1750)
    assert out["multiplier"] == 1.4  # 2500 vs 1000, capped
    assert out["words"][0] == {"label": "Stablecoin", "videos": 2, "avg_views": 2750}
    assert trends.learn(vids[:4], overall=1000)["multiplier"] is None  # too few videos on one side


# --- the scan ----------------------------------------------------------------------------------------------------------------


def rss(*titles: str) -> str:
    when = format_datetime(NOW - timedelta(hours=2))
    return "<rss><channel>" + "".join(f"<item><title>{t}</title><link>https://example.test/{i}</link><pubDate>{when}</pubDate></item>"
                                      for i, t in enumerate(titles)) + "</channel></rss>"


def test_refresh_saves_todays_counts_and_keeps_them_when_every_source_is_down(db: Session) -> None:
    feeds = {u: n for n, u in defaults.TREND_FEEDS}
    down = {"all": False}

    def handle(r: httpx.Request) -> httpx.Response:
        if down["all"]:
            return httpx.Response(503)
        if "search/trending" in str(r.url):
            return httpx.Response(200, json={"coins": [{"item": {"id": "pudgy-penguins", "name": "Pudgy Penguins", "symbol": "pengu"}},
                                                        {"item": {"id": "bitcoin", "name": "Bitcoin", "symbol": "btc"}}]})
        name = feeds.get(str(r.url))
        if name in ("CNBC", "Financial Times", "CoinDesk"):
            return httpx.Response(200, text=rss(f"Tokenized stock plan draws fire at {name}", f"{name} desk: tokenized bonds grow"))
        return httpx.Response(200, text=rss())

    out = asyncio.run(trends.refresh(db, NOW, httpx.MockTransport(handle)))
    assert out["headlines"] == 6 and out["failed"] == []
    rows = {r.term: r for r in db.scalars(select(BmTrendDay))}
    assert rows["tokenized"].docs == 6 and rows["tokenized"].sources == ["CNBC", "CoinDesk", "Financial Times"]
    assert rows["pudgy penguins"].kind == "search" and "bitcoin" not in rows  # Bitcoin is always searched
    down["all"] = True
    out = asyncio.run(trends.refresh(db, NOW, httpx.MockTransport(handle)))
    assert out["kept_previous"] and db.scalar(select(BmTrendDay).where(BmTrendDay.term == "tokenized")) is not None


# --- Content Studio ----------------------------------------------------------------------------------------------------------


def test_trends_api_lists_and_makes_a_video_once(client: TestClient, db: Session) -> None:
    day_row(db, 0, "genius act", 5, label="GENIUS Act", sources=["CNBC", "CoinDesk", "Financial Times"],
            examples=[{"title": "GENIUS Act rule lands", "source": "CNBC", "url": "https://example.test/1", "at": None}])
    day_row(db, 0, "thin", 2)
    db.commit()
    body = client.get(f"{P}/trends").json()
    assert body["trends"][0]["label"] == "GENIUS Act" and body["category_on"] is True and "CNBC" in body["outlets"]
    r = client.post(f"{P}/trends/video", json={"term": "genius act"})
    assert r.status_code == 200, r.text
    v = db.get(BmVideo, r.json()["id"])
    assert v.category == "finance_trends" and v.brief["trend"]["terms"][0]["label"] == "GENIUS Act"
    assert client.post(f"{P}/trends/video", json={"term": "genius act"}).status_code == 409  # one per trend per day
    assert client.post(f"{P}/trends/video", json={"term": "thin"}).status_code == 409
    assert client.post(f"{P}/trends/video", json={"term": "nope"}).status_code == 404
    listed = client.get(f"{P}/trends").json()["trends"][0]
    assert listed["videos"][0]["id"] == v.id


def test_the_writer_and_hashtags_get_the_storys_trend_words(db: Session) -> None:
    b = brands.ensure_default(db)
    brief = {"headline": "Stablecoin bill passes", "facts": [], "trend": {"terms": [{"term": "stablecoin", "label": "Stablecoin"},
                                                                                     {"term": "genius act", "label": "GENIUS Act"}]}}
    v = BmVideo(brand_id=b.id, category="crypto_news", template="breaking_news", topic="Stablecoin bill passes", topic_key="x", score=1.0,
                brief=brief, status="queued", stages={}, publish_mode="drafts", generation=1)
    db.add(v)
    db.flush()
    job = queue.enqueue_render(db, v)
    db.commit()
    payload = queue._payload(db, job, v, b, None)
    assert payload["trends"] == ["Stablecoin", "GENIUS Act"]
    assert "#stablecoin" in hashtags.build([], brands.strategy(b), "crypto_news", brief)  # the one-word trend becomes a tag


def test_brands_that_chose_every_category_get_finance_trends() -> None:
    saved = list(defaults._CATEGORIES_BEFORE["v2"])
    assert "finance_trends" in defaults.merged({"categories": saved})["categories"]
    assert defaults.merged({"categories": ["crypto_news"]})["categories"] == ["crypto_news"]  # a deliberate choice stays
