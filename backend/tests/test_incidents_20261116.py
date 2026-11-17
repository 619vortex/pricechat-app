"""Major incidents production missed (owner, 2026-11-17: "Price Chat is missing major viral breaking news"). Real headlines and timings:

- 2026-11-16 JPMorgan Chase's outage (software bug; reports at Bank of America, Wells Fargo; U.S. Bank: Zelle delays only). Never discovered:
  the News Desk dropped every telling but one before storing it (no market keyword), so Trending never saw it.
- 2026-11-16 United Airlines' system outage: Trending found it (22:39 UTC), held it for want of a "reliable" outlet, scored 78.8 at 23:00,
  then queued it behind the 3-a-day limit until it went stale at 03:04.
- 2026-11-13 the FAA's nationwide ground stop and 2026-11-12 the Microsoft 365 outage: dropped before storage; Trending not yet live.

Separate outages are never assumed to be connected: Chase is not "major US banks", Microsoft is not the FAA, United is not the FAA."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdStory
from app.models.trending import TtPost, TtTopic
from app.newsdesk import chaos, editorial, engine as nd_engine, incidents
from app.trending import coordinate, engine, postmortem, rank, sources
from tests.test_newsdesk import Web, rss

# --- the real headlines --------------------------------------------------------------------------------------------------------------
CHASE_FOX = "JPMorgan Chase systems recovering following hourslong nationwide technical outage"  # Fox Business 2026-11-16 18:51 UTC
BANKS_FOX = "Financial stocks slide as online banking outages reported at several large US banks"  # Fox Business 20:06 UTC
CHASE_CNBC = "JPMorgan Chase says temporary software bug is affecting consumer banking"  # CNBC 22:14 UTC
BANKS_BBC = "Customers report service outages at major US banks"  # BBC 2026-11-17 00:18 UTC
BANKS_CBS = "Major U.S. banks report widespread service outages"  # CBS 01:09 UTC
BANKS_LIST = "Chase, Wells Fargo, Bank of America, US Bank among banks experiencing outages"  # Gray TV stations
FAA_FOX = "Nationwide FAA ground stop lifted but cancellations, significant delays to continue: transportation secretary"  # Fox Business 2026-11-13
FAA_AXIOS = "FAA ends hours-long nationwide ground stop"  # Axios 2026-11-14 02:10 UTC
MSFT_NPR = "A negative Outlook: Microsoft scrambles to fix email outage"  # NPR 2026-11-13 01:26 UTC
MSFT_BBC = "Microsoft says services fully restored after day-long global outage"  # BBC 2026-11-14
UNITED = "United Airlines experiencing a widespread system outage, airline says"  # production topic, 2026-11-16 22:39 UTC
UNITED_AXIOS = 'United Airlines resolves "widespread system outage" that delayed flights'

# not incidents, or not the event itself
NOT_THE_EVENT = [
    "The FAA ground stop left them stranded, so they bought a car and drove it home",  # NBC feature
    "Woman buys Ford Bronco, drives 27 hours home after flights grounded",  # CBS feature
    "Officials received years of warnings before FAA ground stop",  # CBS retrospective
    "What we know about what caused the FAA's nationwide ground stop",  # Axios explainer
    "Sprawling Malware Attack Reportedly Affects Zoom and Other Major Brands",  # Gizmodo, hedged
]
NOT_INCIDENTS = [
    "Armed carjacking leads to police chase in Royal Oak",  # production top 5 on 2026-11-16 (Google "chase")
    "Bank of America says these stocks have major upside potential in an uncertain market",
    "Supreme Court Ends Custodia Bank's Fight for a Fed Master Account",
    "Microsoft detects high-volume phishing campaign using fake 365 alerts",
    "Stocks Fall Before Nvidia Earnings, Anthropic IPO",
    "U.S. Bank systems are not directly impacted by the outage",
]


# --- 1. discovery: the headline is an incident --------------------------------------------------------------------------------------


@pytest.mark.parametrize("title,kind", [(CHASE_FOX, "banking"), (BANKS_FOX, "banking"), (CHASE_CNBC, "banking"), (BANKS_BBC, "banking"), (BANKS_CBS, "banking"),
                                        (BANKS_LIST, "banking"), (FAA_FOX, "aviation"), (FAA_AXIOS, "aviation"), (MSFT_NPR, "cloud"), (MSFT_BBC, "cloud"),
                                        (UNITED, "aviation"), ("Tech issue grounds all US flights day after global Microsoft outage", "aviation")])
def test_every_missed_headline_is_a_major_incident(title: str, kind: str) -> None:
    inc = incidents.detect(title)
    assert inc is not None and inc.major, title
    assert inc.kind == kind


@pytest.mark.parametrize("title", NOT_THE_EVENT)
def test_features_explainers_and_hedged_reports_are_not_major(title: str) -> None:
    inc = incidents.detect(title)
    assert inc is None or not inc.major, title


@pytest.mark.parametrize("title", NOT_INCIDENTS)
def test_lookalikes_are_not_incidents(title: str) -> None:
    assert incidents.detect(title) is None, title


def test_the_bank_list_names_each_bank_without_claiming_a_cause() -> None:
    inc = incidents.detect(BANKS_LIST)
    assert set(inc.entities) == {"JPMorgan Chase", "Wells Fargo", "Bank of America", "U.S. Bank"}
    assert "cyber" not in inc.why()


# --- 2. separate outages are never connected ------------------------------------------------------------------------------------------


def test_same_incident_only_for_the_same_kind_at_the_same_company() -> None:
    assert incidents.same_incident(CHASE_FOX, CHASE_CNBC)  # both Chase
    assert incidents.same_incident(BANKS_BBC, BANKS_CBS)  # both sector-wide bank reports
    assert incidents.same_incident(FAA_FOX, FAA_AXIOS)
    assert not incidents.same_incident(CHASE_FOX, BANKS_CBS)  # Chase is not "major US banks"
    assert not incidents.same_incident(MSFT_NPR, FAA_FOX)  # Microsoft is not the FAA (Microsoft said so)
    assert not incidents.same_incident(UNITED, FAA_FOX)  # United's outage is not the FAA's ground stop
    assert not incidents.same_incident(MSFT_BBC, CHASE_CNBC)


NOW = datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc)
_id = [1000]


def sig(title, outlet, pub_min, seen_min=None, source="news", reliable=True, eng=2.0, history=None, **extra):
    _id[0] += 1
    seen = NOW - timedelta(minutes=pub_min if seen_min is None else seen_min)
    return rank.Sig(id=_id[0], source=source, title=title, outlet=outlet, reliable=reliable, engagement=eng, first_seen=seen, last_seen=NOW,
                    published_at=NOW - timedelta(minutes=pub_min), extra=extra, history=history or [])


def test_trending_keeps_the_four_incidents_apart() -> None:
    sigs = [sig(CHASE_FOX, "Fox Business", 250), sig(CHASE_CNBC, "CNBC", 40), sig(BANKS_CBS, "CBS News", 30), sig(BANKS_BBC, "BBC", 20),
            sig(MSFT_NPR, "NPR", 60), sig(FAA_FOX, "Fox Business", 50), sig(UNITED, "NBC News", 45)]
    groups = [{s.title for s in c.signals} for c in rank.cluster(sigs)]
    for g in groups:
        kinds = {incidents.detect(t).kind for t in g}
        assert len(kinds) == 1, g
    assert not any(CHASE_FOX in g and BANKS_CBS in g for g in groups)
    assert not any(MSFT_NPR in g and FAA_FOX in g for g in groups)
    assert not any(UNITED in g and FAA_FOX in g for g in groups)


# --- 3. the News Desk stores them (production dropped them here) ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_news_desk_stores_incident_headlines_it_used_to_drop(db: Session) -> None:
    now = utcnow()
    s = nd_engine.settings_row(db)
    s.feeds = [{"name": "Fox Business", "url": "https://fox.test/rss", "tier": 1, "category": None}]
    s.state = {"extra_feeds": False}
    web = Web()
    web.feeds["https://fox.test/rss"] = rss([(CHASE_FOX, "https://fox.test/chase", now - timedelta(minutes=8)),
                                             (FAA_FOX, "https://fox.test/faa", now - timedelta(minutes=6)),
                                             (MSFT_NPR, "https://fox.test/msft", now - timedelta(minutes=4)),
                                             ("Woman buys Ford Bronco, drives 27 hours home after flights grounded", "https://fox.test/bronco", now - timedelta(minutes=3)),
                                             ("Armed carjacking leads to police chase in Royal Oak", "https://fox.test/carjack", now - timedelta(minutes=2))])
    async with httpx.AsyncClient(transport=web.transport) as client:
        await nd_engine.collect(db, s, client, now)
    rows = {r.link: r for r in db.scalars(select(NdStory))}
    assert rows["https://fox.test/chase"].category == "stocks"  # a banking outage: financial
    assert rows["https://fox.test/faa"].category == "viral"
    assert rows["https://fox.test/msft"].category == "viral"
    assert "https://fox.test/carjack" not in rows  # still dropped: no market word, no incident


def test_the_chaos_filter_does_not_read_an_outage_as_reassurance() -> None:
    story = SimpleNamespace(title="Chase app offline for thousands of customers, bank says", summary="", link="https://x.test/a", category="stocks")
    assert chaos.rejected(story) != "reassurance, not news"


# --- 4. News Desk importance and breaking -----------------------------------------------------------------------------------------------


def test_a_verified_major_bank_outage_is_important_and_breaks() -> None:
    sig_ = editorial.significance(CHASE_FOX)
    assert sig_ is not None and sig_.kind == "infrastructure"
    ok, because = editorial.breaking_worthy(CHASE_FOX, "", {"kind": sig_.kind})
    assert ok and "banking outage" in because
    s = editorial.importance_score(sig_, evidence="two outlets", outlets=2, outlets_3h=2, age_minutes=20, title=CHASE_FOX)
    assert s.total >= 70  # the breaking bar, with two outlets (Fox Business + CNBC)
    one = editorial.importance_score(sig_, evidence="attributed", outlets=1, outlets_3h=1, age_minutes=20, title=CHASE_FOX)
    assert one.total < 70  # one outlet never breaks


def test_aftermath_stories_never_break() -> None:
    sig_ = editorial.significance("Officials received years of warnings before FAA ground stop")
    assert sig_ is None or sig_.kind != "infrastructure"
    ok, _ = editorial.breaking_worthy("Officials received years of warnings before FAA ground stop", "", {"kind": "infrastructure"})
    assert not ok


def test_the_two_chase_reports_corroborate_each_other() -> None:
    at = datetime(2026, 11, 16, 18, 51)
    fox = SimpleNamespace(id=1, title=CHASE_FOX, source="Fox Business", published_at=at, summary="")
    cnbc = SimpleNamespace(id=2, title=CHASE_CNBC, source="CNBC", published_at=at + timedelta(hours=3, minutes=23), summary="")
    bbc = SimpleNamespace(id=3, title=BANKS_BBC, source="BBC", published_at=at + timedelta(hours=5), summary="")
    faa = SimpleNamespace(id=4, title=FAA_FOX, source="Fox News", published_at=at, summary="")
    got = {o.id for o in editorial.corroborating(fox, [fox, cnbc, bbc, faa])}
    assert 2 in got and 3 not in got and 4 not in got


# --- 5. Trending: category, score, evidence ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title,cat", [(BANKS_CBS, "markets"), (BANKS_BBC, "markets"), (CHASE_FOX, "markets"), (FAA_FOX, "technology"),
                                       (MSFT_NPR, "technology"), (UNITED, "technology")])
def test_trending_files_incidents_on_the_beat(title: str, cat: str) -> None:
    assert rank.classify(title)[0] == cat  # production: BANKS_CBS / BANKS_BBC / FAA_FOX were None (dropped)


def _united_at_22_39() -> rank.Cluster:
    """The production topic at 22:39 UTC (age 0.5 h, Google 10K+ -> 20K+, two outlets in 2 h), with the outlets now counted as credible."""
    hist = [[(NOW - timedelta(minutes=35)).isoformat(), 10_000.0], [(NOW - timedelta(minutes=21)).isoformat(), 20_000.0]]
    sigs = [sig("united airlines", "Google Trends", 30, source="google", reliable=False, eng=20_000, history=hist, query="united airlines",
                group="google:united airlines", geo="US"),
            sig(UNITED, "USA Today", 28, source="google", kind="article", group="google:united airlines", query="united airlines"),
            sig("United Airlines flights delayed by widespread system outage", "The Independent", 24, source="google", kind="article",
                group="google:united airlines", query="united airlines")]
    now = NOW - timedelta(minutes=21)
    for s in sigs:
        s.last_seen = now
    cs = rank.cluster(sigs)
    assert len(cs) == 1
    return cs[0]


def test_united_outage_crosses_the_automatic_line_at_its_first_scan() -> None:
    c = _united_at_22_39()
    sc, parts = rank.score(c, NOW - timedelta(minutes=21))
    assert c.category == "technology"
    assert parts["incident"]["major"] and parts["incident"]["verified"]
    assert sc >= 75, parts["why"]  # production: 61.7 at 22:39, 78.5 only at 23:00


def test_credible_outlets_back_google_linked_outage_articles() -> None:
    assert sources.reliable_outlet("USA Today")
    assert sources.reliable_outlet("Fox Business")
    assert sources.reliable_outlet("Tom's Guide", title="Chase Bank Goes Down for Thousands, Downdetector Reports")
    assert not sources.reliable_outlet("Tom's Guide", title="The best laptops of 2026")  # outage coverage only
    assert not sources.reliable_outlet("Yahoo", title="Sprawling Malware Attack Reportedly Affects Zoom and Other Major Brands")  # hedged


def test_one_outlet_without_a_company_statement_is_not_verified() -> None:
    one = rank.cluster([sig(BANKS_FOX, "Fox Business", 10)])[0]
    _, parts = rank.score(one, NOW)
    assert parts["incident"]["major"] and not parts["incident"]["verified"]
    stmt = rank.cluster([sig(CHASE_CNBC, "CNBC", 10)])[0]
    _, parts2 = rank.score(stmt, NOW)
    assert parts2["incident"]["verified"]  # the company's own word ("JPMorgan Chase says ...")
    two = rank.cluster([sig(BANKS_BBC, "BBC", 20), sig(BANKS_CBS, "CBS News", 10)])[0]
    _, parts3 = rank.score(two, NOW)
    assert parts3["incident"]["verified"]


def test_an_unverified_incident_never_goes_out_automatically(db: Session) -> None:
    s = engine.settings_row(db)
    t = TtTopic(key="t:inc", title=BANKS_FOX, category="markets", status="active", score=80.0, peak_score=80.0, signal_ids=[], sources=[], metrics={},
                parts={"growth": {"measured": True}, "incident": {"major": True, "verified": False, "evidence": "one outlet and no statement"}},
                first_seen=NOW, last_seen=NOW)
    ok, why = engine.auto_ok(s, t, "text", None)
    assert not ok and "not yet verified" in why


# --- 6. the daily limit: a verified incident may go beyond it, a little --------------------------------------------------------------


def _topic(db: Session) -> int:
    t = TtTopic(key=f"t:{_id[0]}", title="earlier topic", category="crypto", status="posted", score=80.0, peak_score=80.0, parts={}, signal_ids=[],
                sources=[], metrics={}, first_seen=utcnow(), last_seen=utcnow())
    _id[0] += 1
    db.add(t)
    db.flush()
    return t.id


def _sent(db: Session, n: int, now: datetime) -> None:
    tid = _topic(db)
    for i in range(n):
        db.add(TtPost(topic_id=tid, category="crypto", status="published", text=f"post {i}", angle="", writer="rules", fingerprint=f"f{i}", score=80,
                      sources=[], source_text="", checks=[], metrics={}, published_at=now - timedelta(hours=20 - i * 4)))
    db.commit()


def test_a_verified_incident_uses_the_incident_allowance(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    _sent(db, 3, now)  # production 2026-11-16 23:00: posts 152, 167, 204
    assert "daily limit" in (coordinate.cap_problem(db, s, now) or "")
    assert coordinate.cap_problem(db, s, now, incident=True) is None
    _sent(db, 2, now)
    assert "incident allowance" in (coordinate.cap_problem(db, s, now, incident=True) or "")


def test_incident_spacing_is_shorter_but_never_zero(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    db.add(TtPost(topic_id=_topic(db), category="crypto", status="published", text="p", angle="", writer="rules", fingerprint="p", score=80, sources=[],
                  source_text="", checks=[], metrics={}, published_at=now - timedelta(minutes=90)))
    db.commit()
    assert "spacing" in (coordinate.spacing_problem(db, s, now) or "")  # 180 min for ordinary topics
    assert "trend post" not in (coordinate.spacing_problem(db, s, now, incident=True) or "")
    assert coordinate.INCIDENT_GAP_MINUTES >= 60


# --- 7. the FAA's own status --------------------------------------------------------------------------------------------------------


def _faa(programs: list[tuple[str, str]]) -> str:
    body = "".join(f"<Program><ARPT>{a}</ARPT><Reason>{r}</Reason><End_Time>9:00 pm EST</End_Time></Program>" for a, r in programs)
    return (f"<AIRPORT_STATUS_INFORMATION><Update_Time>Fri Nov 13 18:05:00 2026 GMT</Update_Time><Delay_type><Name>Ground Stop Programs</Name>"
            f"<Ground_Stop_List>{body}</Ground_Stop_List></Delay_type></AIRPORT_STATUS_INFORMATION>")


def test_a_nationwide_ground_stop_is_an_official_item() -> None:
    xml = _faa([(a, "equipment / outage") for a in ("ATL", "ORD", "DFW", "DEN", "LAX", "JFK", "SFO", "SEA", "LAS", "MCO", "CLT", "PHX")])
    items = sources.parse_faa(xml)
    assert len(items) == 1 and items[0]["reliable"] and items[0]["extra"]["official"]
    assert "12 US airports" in items[0]["title"]
    inc = incidents.detect(items[0]["title"])
    assert inc is not None and inc.major and inc.kind == "aviation"
    assert rank.classify(items[0]["title"])[0] == "technology"


def test_routine_weather_ground_stops_are_not_items() -> None:
    assert sources.parse_faa(_faa([("SFO", "low ceilings")])) == []
    assert sources.parse_faa(_faa([("SFO", "low ceilings"), ("EWR", "thunderstorms")])) == []
    assert len(sources.parse_faa(_faa([("SFO", "equipment outage"), ("OAK", "equipment outage")]))) == 1  # an equipment failure at two


# --- 8. Admin: every incident's detection time, score and reason ------------------------------------------------------------------


def test_the_incident_watch_records_detection_score_and_reason(db: Session) -> None:
    now = utcnow()
    s = engine.settings_row(db)
    t = TtTopic(key="t:united", title=UNITED, category="technology", status="active", score=78.5, peak_score=78.8, signal_ids=[], sources=[], metrics={},
                parts={"incident": {"kind": "aviation", "label": "aviation disruption", "entities": ["United Airlines"], "major": True, "verified": True,
                                    "evidence": "the affected company's own statement"},
                       "timeline": {"event_time": (now - timedelta(minutes=50)).isoformat(), "outlets": 4}, "why": "78.5: fresh"},
                first_seen=now - timedelta(minutes=21), last_seen=now)
    db.add(t)
    db.flush()
    db.add(TtPost(topic_id=t.id, category="technology", status="queued", text="x", angle="", writer="rules", fingerprint="x", score=78.5, sources=[],
                  source_text="", checks=[], metrics={}, note="Trending's daily limit reached (3/3 in 24 h)"))
    db.commit()
    lines = engine.watch_incidents(db, s, [t], now)
    e = (s.state or {})["incidents"][0]
    assert e["status"] == "queued" and "daily limit" in e["reason"]
    assert e["detect_lag_min"] == 29 and e["peak_score"] == 78.8 and e["verified"]
    assert lines and "trending incident" in lines[0] and "daily limit" in lines[0]
    assert engine.watch_incidents(db, s, [t], now + timedelta(minutes=5)) == []  # one line per change, not per scan


def test_the_audited_misses_are_in_admin() -> None:
    stories = " ".join(str(m["story"]) for m in postmortem.MISSED)
    for name in ("JPMorgan Chase", "United Airlines", "FAA", "Microsoft 365"):
        assert name in stories
    for m in postmortem.MISSED:
        assert m["stage"] and m["reason"] and m["now"]


# --- 9. replay of the bank-outage day --------------------------------------------------------------------------------------------------
T0 = datetime(2026, 11, 16, tzinfo=timezone.utc)


def _at(h: int, m: int) -> datetime:
    return T0 + timedelta(hours=h, minutes=m)


def _seen_sig(title, outlet, at, source="news", reliable=True, eng=2.0, history=None, **extra):
    _id[0] += 1
    return rank.Sig(id=_id[0], source=source, title=title, outlet=outlet, reliable=reliable, engagement=eng, first_seen=at + timedelta(minutes=4),
                    last_seen=at + timedelta(minutes=4), published_at=at, extra=extra, history=history or [])


def test_googles_chase_trend_tells_the_outage_not_the_police_chase() -> None:
    """Production 2026-11-16 19:09: "Armed carjacking leads to police chase in Royal Oak" carried Google's "chase" trend (10K+) while the bank
    outage had no topic. Reconstruction of that unit (Google's linked outage articles; the outlets are illustrative, the headline is real)."""
    now = _at(17, 55)
    hist = [[_at(17, 40).isoformat(), 10_000.0]]
    unit = [_seen_sig("chase", "Google Trends", _at(17, 35), source="google", reliable=False, eng=10_000, history=hist, query="chase", group="google:chase", geo="US"),
            _seen_sig("Armed carjacking leads to police chase in Royal Oak", "WXYZ", _at(17, 30), source="google", reliable=False, kind="article", group="google:chase", query="chase"),
            _seen_sig("Chase Bank Goes Down for Thousands, Downdetector Reports", "Yahoo", _at(17, 20), source="google",
                      reliable=sources.reliable_outlet("Yahoo", title="Chase Bank Goes Down for Thousands, Downdetector Reports"), kind="article", group="google:chase", query="chase"),
            _seen_sig("Chase outage: thousands of customers report login problems", "USA Today", _at(17, 25), source="google", reliable=True, kind="article",
                      group="google:chase", query="chase")]
    for s in unit:
        s.last_seen = now
    clusters = rank.cluster(unit)
    bank = next(c for c in clusters if "Downdetector" in c.lead.title or "Chase outage" in c.lead.title)
    assert not any("carjacking" in s.title for s in bank.signals)
    sc, parts = rank.score(bank, now)
    assert bank.category == "markets" and parts["incident"]["verified"]
    assert sc >= 75, parts["why"]


def test_from_the_feeds_alone_the_bank_outage_is_detected_at_once_and_crosses_when_coverage_bursts() -> None:
    feed = [(CHASE_FOX, "Fox Business", _at(18, 51)), (BANKS_FOX, "Fox Business", _at(20, 6)), (CHASE_CNBC, "CNBC", _at(22, 14)),
            (BANKS_BBC, "BBC", _at(24, 18)), (BANKS_CBS, "CBS News", _at(25, 9))]

    def topics(now):
        sigs = [_seen_sig(t, o, a) for t, o, a in feed if a + timedelta(minutes=4) <= now]
        for s in sigs:
            s.last_seen = now
        return [(c, *rank.score(c, now)) for c in rank.cluster(sigs)]

    first = topics(_at(18, 56))
    assert len(first) == 1 and first[0][2]["incident"]["major"]  # a topic five minutes after Fox Business (production: never)
    assert first[0][1] < 75 and not first[0][2]["incident"]["verified"]  # one outlet: in Admin, not on X
    late = topics(_at(25, 14))
    sector = next(x for x in late if x[0].lead.title == BANKS_FOX or "major" in x[0].lead.title.lower())
    assert sector[1] >= 75 and sector[2]["incident"]["verified"]  # BBC + CBS within the hour: a burst
    chase = next(x for x in late if "JPMorgan" in x[0].lead.title)
    assert not any(s.title in (BANKS_BBC, BANKS_CBS) for s in chase[0].signals)  # never merged with the sector reports


def test_cold_storage_reassurance_is_not_an_incident() -> None:
    """tests/test_newsdesk_telegram: "offline" and "hackers" in a reassurance headline are not a cyberattack or an outage."""
    assert incidents.detect("Coinbase says 99% of customer crypto is offline and out of hackers' reach") is None
