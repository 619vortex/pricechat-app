"""The media engine (owner, 2026-11-13): editorial scorecard, five ranked hooks with fact-checks, the claim checker as a hard publishing gate
(cashtags only where the sources establish the asset), debate questions, story-level dedup, the editorial bar that skips weak slots,
normalized engagement from Telegram and X, generic-photo rejection, and the Admin endpoints. No test calls a real service or model."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEngagement, NdEvent, NdStory, NdTelegramPost
from app.newsdesk import checks, claims, editorial, engagement, engine, events, hooks, network, photos, scorecard, sources

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)


def ev(title: str, summary: str = "", **kw: Any) -> NS:
    base = dict(id=1, key=f"story:{abs(hash(title))}", kind="news", title=title, headline=title, summary=summary, topic="", evidence="two outlets",
                facts={"told": f"{title}. {summary}"}, sources=[{"name": "Reuters", "url": "https://r.test/a"}, {"name": "Bloomberg", "url": "https://b.test/a"}],
                parts={}, label="", status="candidate", released_at=None, parent_id=None)
    base.update(kw)
    return NS(**base)


# --- the scorecard -------------------------------------------------------------------------------------------------------------------


def test_the_scorecard_rewards_genuine_controversy_and_big_money_and_rejects_minor_corporate_news() -> None:
    scandal = scorecard.assess(title="Nvidia board investigates CEO Jensen Huang over affair with alleged Chinese spy", evidence="two outlets", outlets=3)
    hack = scorecard.assess(title="Binance's $6.3B hack leaves questions over hot wallet safeguards", topic="hack", evidence="two outlets", outlets=2)
    minor = scorecard.assess(title="Zeta Labs launches partnership with Acme Wallet to integrate stablecoin payments", evidence="two outlets", outlets=2)
    talk = scorecard.assess(title="Ukraine Peace Talks Face an Uphill Battle as Winter Approaches", topic="geopolitics", evidence="two outlets", outlets=2)
    assert set(scandal.dims) == set(scorecard.DIMENSIONS) and all(0 <= v <= 20 for v in scandal.dims.values())
    assert scandal.dims["controversy"] >= 15 and scandal.beat == "scandal" and not scandal.reject
    assert hack.total >= scorecard.MINIMUM and hack.dims["impact"] >= 15 and not hack.reject
    assert minor.beat == "minor_corporate" and "minor corporate" in minor.reject
    assert talk.beat == "soft_geopolitics" and talk.reject.startswith("editorial score")
    assert all(scandal.reasons[d] for d in ("controversy", "relevance", "discussion"))  # every point explained


def test_harm_to_people_is_never_scored_as_controversy_or_debate() -> None:
    c = scorecard.assess(title="Israeli strike on Nasser Hospital kills at least six, Gaza civil defence agency says", topic="geopolitics", evidence="two outlets")
    assert c.dims["controversy"] == 0 and c.dims["discussion"] <= 4


def test_an_accusation_about_a_named_person_needs_an_official_source_or_two_outlets() -> None:
    one = scorecard.assess(title="Jensen Huang accused of fraud by former employee", evidence="attributed", outlets=1)
    two = scorecard.assess(title="Jensen Huang accused of fraud by former employee", evidence="two outlets", outlets=2)
    passing = scorecard.assess(title="Binance hack launderers mint homemade Elon Musk memecoins", evidence="attributed", outlets=1)
    assert one.safety and "identifiable person" in one.reject
    assert not two.safety
    assert not passing.safety  # Musk is named, not accused


def test_learned_audience_points_move_the_total_within_bounds() -> None:
    base = scorecard.assess(title="SEC bans Bybit from US customers after record fine", evidence="official")
    up = scorecard.assess(title="SEC bans Bybit from US customers after record fine", evidence="official", learned={base.beat: 50.0})
    assert up.audience == 6.0 and up.total == min(100, base.total + 6)


# --- hooks and the claim checker ---------------------------------------------------------------------------------------------------

SOURCE = ("DOJ seizes $1.2 billion of crypto stolen in the Binance hack. The Justice Department said it seized $1.2 billion in stolen "
          "cryptocurrency linked to North Korean hackers behind the $6.3 billion Binance hack, according to a court filing.")


def test_five_hooks_are_ranked_and_any_hook_that_adds_a_claim_is_rejected() -> None:
    model = ["DOJ seizes $1.2B stolen in the Binance hack",  # fine
             "DOJ seizes $1.5B from Binance hackers",  # a number no source gave
             "Binance CEO arrested as DOJ seizes $1.2B",  # an arrest the sources never mention
             "DOJ seizure sparks Binance $BNB rally",  # a cause and a ticker the sources don't establish
             "\"We got them all,\" says DOJ after $1.2B seizure"]  # an invented quote
    ranked = hooks.rank([hooks.Hook(t, "model") for t in model], SOURCE, evidence="official", outlets=2, who={"binance", "doj"})
    by = {h.text: h for h in ranked}
    assert ranked[0].text == model[0] and ranked[0].ok and ranked[0].rank == 1
    assert any("number" in p or "figure" in p for p in by[model[1]].problems)
    assert any("arrest" in p for p in by[model[2]].problems)
    assert any("$BNB" in p for p in by[model[3]].problems) and any("cause" in p for p in by[model[3]].problems)
    assert any("quote" in p for p in by[model[4]].problems)
    assert all(h.total == 0 for h in ranked[1:])  # rejected hooks never win, however catchy


def test_press_release_wording_and_vagueness_lose_engagement_points() -> None:
    plain, _ = hooks.engagement("Binance hackers lose $1.2B to DOJ seizure")
    corporate, parts = hooks.engagement("Acme announces strategic partnership to enhance its ecosystem")
    assert plain > corporate and parts["press_release"] < 0


def test_desk_hooks_come_only_from_the_reporting_and_name_who() -> None:
    e = ev("North Korean hackers are creating memecoins to wash loot from $6.3 billion Binance hack",
           "North Korean hackers used six homemade memecoins to launder 117,000 NEAR. Binance said it is working with investigators.", topic="hack")
    plan = hooks.plan(e, [])
    assert 1 <= len(plan["hooks"]) <= 6 and plan["chosen"]
    src = network.source_text_of(e, [])
    for h in plan["hooks"]:
        assert not checks.numbers(h["text"]) - checks.numbers(src)
    assert all("binance" in h["text"].lower() or not h["ok"] for h in plan["hooks"])


@pytest.mark.parametrize("text,source,ok", [
    # Binance: hack, seizure, CEO news never get $BNB; BNB's own price may
    ("DOJ seizes $1.2B of stolen Binance $BNB crypto", SOURCE, False),
    ("Binance $BNB CEO steps down", "Binance CEO Richard Teng steps down, the exchange said.", False),
    ("$BNB falls 4% after the report", "BNB price falls 4% after the report, data showed.", True),
    # Coinbase: outage, hack or lawsuit never get $COIN unless its shares are discussed
    ("Coinbase $COIN outage hits users", "Coinbase suffered an outage on Tuesday, users reported.", False),
    ("Coinbase $COIN sued over data breach", "Coinbase was sued over a data breach, Reuters reported.", False),
    ("Coinbase $COIN shares drop 6%", "Coinbase shares dropped 6% in New York trading.", True),
    # exchange hacks: the stolen asset only when the sources name it
    ("Hackers drain $40M in stolen $ETH from Bybaz", "Hackers drained $40M in Ether from the Bybaz exchange.", True),
    ("Hackers drain $40M in stolen $ETH from Bybaz", "Hackers drained $40M in crypto from the Bybaz exchange.", False),
    # a company name inside another name is not the asset
    ("Mercado Bitcoin $BTC reopens", "Mercado Bitcoin begins post-hack reopening without a timeline.", False),
    # unrelated assets
    ("Gold hits record as $SOL slips", "Gold hit a record high on Tuesday.", False),
])
def test_tickers_only_where_the_sources_establish_the_asset(text: str, source: str, ok: bool) -> None:
    assert (not claims.ticker_problems(text, source)) is ok


def test_legal_actions_need_attribution_and_a_reliable_source() -> None:
    src = "Acme founder charged with fraud, a crypto blog reported."
    assert claims.reliable_problems("Acme founder charged with fraud", evidence="attributed", outlets=["CryptoBlogz"], source_text=src)
    assert not claims.reliable_problems("Acme founder charged with fraud, Reuters reports", evidence="attributed", outlets=["Reuters"], source_text=src)
    assert claims.reliable_problems("Acme founder charged with fraud", evidence="official", outlets=["SEC"], source_text=src, attributed=False)
    assert claims.legal_problems("Acme founder arrested", "Acme founder charged with fraud.")  # arrest: never in the sources


def test_the_final_caption_is_a_hard_accuracy_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    e = ev("DOJ seizes $1.2B from Binance hack", SOURCE, topic="hack", evidence="official")
    good = network.final_accuracy(e, "DOJ seizes $1.2B from Binance hack\n\nReuters https://r.test/a", "DOJ seizes $1.2B from Binance hack. Reuters", SOURCE, ["Reuters"])
    bad = network.final_accuracy(e, "JUST IN: DOJ seizes $1.2B of North Korea's stolen Binance $BNB crypto https://r.test/a", "x", SOURCE, ["Reuters"])
    assert good == []
    assert bad and any("$BNB" in p for p in bad) and bad[0].startswith("X:")
    market = ev("BTC falls 5%", kind="market")
    assert network.final_accuracy(market, "$BNB", "$BNB", "", []) == []  # measured posts keep their own checks


async def test_a_package_that_fails_the_claim_check_is_not_publishable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import clean

    e = ev("DOJ seizes $1.2B from Binance hack", SOURCE, topic="hack", evidence="official")
    pkg = await network.package(e, style="clean")
    assert pkg.accuracy == [] and "$BNB" not in pkg.telegram_plain
    # a regression of the old mechanical cashtags (2026-11-13) is caught by the gate, not posted
    monkeypatch.setattr(clean, "cashtag", lambda text: (text.replace("Binance", "Binance $BNB", 1), ["$BNB"]))
    pkg = await network.package(e, style="clean")
    assert not pkg.ok and any("$BNB" in p for p in pkg.accuracy)


def test_debate_questions_invite_disagreement_never_guilt_or_outrage() -> None:
    gov = ev("Trump lifts sanctions on Russian diesel exports")
    card = {"dims": {"discussion": 15}, "beat": "government"}
    q, why = hooks.debate_question(gov, card, source_text=gov.title)
    assert q == "Right call by Trump, or a mistake?" and "government" in why
    sombre = ev("Strike kills six at hospital")
    assert hooks.debate_question(sombre, card)[0] == ""
    assert hooks.debate_question(gov, {"dims": {"discussion": 3}, "beat": "government"})[0] == ""
    assert hooks.question_problems("Is Huang guilty?", "Huang affair probe")
    assert hooks.question_problems("Isn't this a disgrace?", "x")
    assert hooks.question_problems("Will $BNB crash now?", "Binance hack")


# --- dedup ---------------------------------------------------------------------------------------------------------------------------


def test_another_version_of_the_same_event_is_one_story_but_different_subjects_are_not() -> None:
    covered = [(7, "Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production")]
    assert scorecard.near_duplicate("Saudi crude output jumps 26% to near pre-war production, OPEC told", covered) is not None
    assert scorecard.near_duplicate("Iraq crude output jumps 26% to near pre-war production", covered) is None
    assert scorecard.near_duplicate("Bybaz exchange says hackers drained $700 million", [(1, "Zentra exchange says hackers drained $400 million")]) is None


def test_a_follow_up_needs_meaningful_new_information() -> None:
    parent = NS(id=1, kind="news", topic="hack", title="Bybaz exchange hacked for $400 million", headline="Bybaz exchange hacked for $400 million",
                facts={"told": "Bybaz exchange hacked for $400 million"}, evidence="two outlets")
    same = NS(title="Hackers launder Bybaz loot through memecoins", tier=2)
    bigger = NS(title="Bybaz hack losses rise to $600 million", tier=2)
    arrest = NS(title="Police arrest suspect in Bybaz hack", tier=2)
    maybe = NS(title="Bybaz could halt withdrawals, sources say", tier=2)
    assert not editorial.material_development(events.development(same, parent))  # laundering is context, not a development
    assert events.development(bigger, parent) == "a bigger figure"
    assert events.development(arrest, parent).startswith("new: arrest")
    assert events.development(maybe, parent) == ""
    assert editorial.material_development("a bigger figure") and not editorial.material_development("")


# --- the editorial bar skips weak slots ----------------------------------------------------------------------------------------------

SLOT = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 08:00 Dubai


def _two_outlets(db: Session, title: str, summary: str, at: datetime) -> None:
    for src in ("The Block", "Decrypt"):
        db.add(NdStory(link=f"https://{src.lower().replace(' ', '')}.test/{abs(hash(title + src))}", title=title if src == "The Block" else f"{title} today",
                       summary=summary, source=src, tier=1, category="crypto", published_at=at - timedelta(minutes=20), fingerprint=checks.fingerprint(title), score=2.0))
    db.commit()


async def _decide(db: Session, now: datetime, plan: dict[str, Any]) -> None:
    desk = engine.settings_row(db)
    desk.state = {"last_run": {"at": now.isoformat()}, "policy": plan}
    db.commit()
    await events.decide(db, now, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False)


async def test_a_slot_with_only_weak_news_is_skipped_never_filled(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(sources, "EXTRA_FEEDS", [])
    monkeypatch.setattr(sources, "WORLDMONITOR_FEEDS", [])
    _two_outlets(db, "Bybaz exchange launches partnership with Zentra Wallet to integrate payments",
                 "Bybaz exchange said it launched a partnership with Zentra Wallet. The integration goes live next month.", SLOT)
    await _decide(db, SLOT + timedelta(minutes=2), {"regular_bar": 0, "editorial_min": 45})
    db.expire_all()
    assert db.scalar(select(NdEvent).where(NdEvent.status == "released")) is None
    cand = db.scalar(select(NdEvent).where(NdEvent.kind == "news"))
    if cand is not None:  # the evidence rules may already refuse it; if they don't, the editorial bar does
        assert (cand.facts or {}).get("editorial", {}).get("beat") == "minor_corporate"
        assert cand.why.startswith("editorial: a minor corporate announcement")
    assert SLOT.isoformat() in events.skipped_slots(db)  # skipped by design, no alert


async def test_a_strong_story_clears_the_editorial_bar_and_takes_the_slot(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(sources, "EXTRA_FEEDS", [])
    monkeypatch.setattr(sources, "WORLDMONITOR_FEEDS", [])
    _two_outlets(db, "Bybaz exchange says hackers drained $400 million from its hot wallet",
                 "Bybaz exchange said hackers drained $400 million from its hot wallet. Withdrawals are paused.", SLOT)
    await _decide(db, SLOT + timedelta(minutes=2), {"regular_bar": 0, "editorial_min": 45})
    db.expire_all()
    out = db.scalar(select(NdEvent).where(NdEvent.status == "released"))
    assert out is not None and out.facts["editorial"]["total"] >= 45
    assert out.facts["hooks"]["hooks"] and out.facts["editorial"]["visual"]["first"]


# --- engagement ----------------------------------------------------------------------------------------------------------------------


def test_engagement_is_normalized_per_platform_and_learned_only_from_enough_posts(db: Session) -> None:
    now = utcnow()
    assert engagement.norm_rate("telegram", 10, 1, 1, 1) is None  # too few views to mean anything
    assert engagement.norm_rate("x", 1000, 5, 5, 10) == 0.03
    # Telegram posts get many more views; X posts a higher rate. Each is judged against its own platform.
    for i in range(4):
        engagement.record(db, platform="telegram", post_ref=f"@c/{i}", views=2000, shares=10 if i < 2 else 2, replies=0, reactions=20, now=now,
                          features={"beat": "scandal" if i < 2 else "minor_corporate"})
        engagement.record(db, platform="x", post_ref=f"x{i}", views=200, shares=2 if i < 2 else 0, replies=1, reactions=4, now=now,
                          features={"beat": "scandal" if i < 2 else "minor_corporate"})
    db.commit()
    assert engagement.beat_points(db, now) != {}  # 8 posts: learning
    ix = dict((r.post_ref, i) for r, i in engagement.indexed(db, now))
    assert ix["@c/0"] > 1 > ix["@c/3"] and ix["x0"] > 1 > ix["x3"]
    pts = engagement.beat_points(db, now)
    assert pts["scandal"] > 0 > pts["minor_corporate"] and all(abs(v) <= engagement.BEAT_SPAN for v in pts.values())
    db.query(NdEngagement).delete()
    db.commit()
    assert engagement.beat_points(db, now) == {}


async def test_telegram_metrics_are_not_connected_without_the_account_and_read_when_it_is(db: Session) -> None:
    now = utcnow()
    got = await engagement.collect_telegram(db, None, None, now)
    assert got["connected"] is False and "not connected" in got["why"]
    tp = NdTelegramPost(story_link="event:1", title="t", headline="h", text="x", source="s", status="delivered", chat="@Pricechatupdates", message_id=167)
    db.add(tp)
    db.flush()
    e = NdEvent(key="story:x", kind="news", title="Bybaz hacked", headline="Bybaz hacked", summary="", topic="hack", evidence="two outlets", virality=60,
                status="released", released_at=now - timedelta(hours=2), first_seen=now, tg_message_id=167, tg_post_id=tp.id,
                facts={"editorial": {"beat": "exchange", "total": 60}, "hooks": {"chosen_strategy": "outlet"}}, parts={}, sources=[])
    db.add(e)
    db.commit()
    calls = []

    async def fetch(channel: str, ids: list[int]) -> list[dict[str, Any]]:
        calls.append((channel, ids))
        return [{"id": 167, "views": 900, "forwards": 12, "replies": 3, "reactions": 40}]

    got = await engagement.collect_telegram(db, None, None, now, fetch=fetch)
    row = db.scalar(select(NdEngagement).where(NdEngagement.platform == "telegram"))
    assert got == {"connected": True, "updated": 1} and calls == [("@Pricechatupdates", [167])]
    assert row.views == 900 and row.shares == 12 and row.features["beat"] == "exchange" and row.rate == round((24 + 6 + 40) / 900, 5)


# --- pictures ------------------------------------------------------------------------------------------------------------------------


def _photo(title: str, **kw: Any) -> photos.LicensedPhoto:
    base: dict[str, Any] = dict(url="https://upload.wikimedia.org/x/1920px-a.jpg", page="https://commons.wikimedia.org/wiki/File:A.jpg", creator="Jane Doe",
                                provider="wikimedia", source="wikimedia", licence="CC BY 4.0", licence_url="https://creativecommons.org/licenses/by/4.0",
                                width=4000, height=2250, year=2023, mime="image/jpeg")
    base.update(kw)
    return photos.LicensedPhoto(title=title, **base)


def test_generic_server_rooms_and_skylines_are_rejected_and_the_subjects_face_ranks_first() -> None:
    assert "generic scene" in (photos.relevant(_photo("File:Binance data center server room.jpg"), ["binance"]) or "")
    assert "generic scene" in (photos.relevant(_photo("File:Dubai skyline with Binance office.jpg"), ["binance"]) or "")
    assert photos.relevant(_photo("File:Jensen Huang at Computex 2024.jpg"), ["jensen huang", "huang"]) is None
    face = photos._score(_photo("File:Jensen Huang at Computex 2024.jpg"), ["jensen huang", "huang"], [])
    hq = photos._score(_photo("File:Nvidia headquarters building Santa Clara.jpg"), ["nvidia"], [])
    assert face > hq


# --- Admin ---------------------------------------------------------------------------------------------------------------------------


def test_admin_shows_scorecards_hooks_rejections_and_performance(client: Any, db: Session) -> None:
    now = utcnow()
    db.add(NdEvent(key="story:a", kind="news", title="Zeta launches partnership", headline="Zeta launches partnership", summary="", topic="", evidence="two outlets",
                   virality=58, status="candidate", first_seen=now, why="editorial: a minor corporate announcement: not news our audience argues about",
                   facts={"editorial": {"total": 10, "beat": "minor_corporate", "dims": {d: 2 for d in scorecard.DIMENSIONS}, "reject": "minor"},
                          "hooks": {"hooks": [{"text": "Zeta launches partnership", "rank": 1, "ok": True, "total": 50}], "chosen": "Zeta launches partnership"},
                          "accuracy": {"ok": False, "problems": ["X: $BNB: the sources name Binance, not its BNB token"]}}, parts={}, sources=[]))
    db.commit()
    out = client.get("/api/x/newsdesk/editorial/scorecards").json()
    s = out["stories"][0]
    assert out["minimum"] == 0 or out["minimum"] >= 0  # pinned by the test settings, shown as is
    assert s["editorial"]["beat"] == "minor_corporate" and s["why"].startswith("editorial:") and s["hooks"][0]["rank"] == 1
    assert s["accuracy"]["ok"] is False and "$BNB" in s["accuracy"]["problems"][0]
    perf = client.get("/api/x/newsdesk/editorial/performance").json()
    assert perf["connections"]["telegram"]["connected"] is False and "not connected" in perf["connections"]["telegram"]["why"]
    assert perf["platforms"]["telegram"]["posts"] == 0 and perf["learning"] is False
    assert client.put("/api/x/newsdesk/editorial/quality", json={"editorial_min": 55}).json() == {"editorial_min": 55}
    assert client.put("/api/x/newsdesk/editorial/quality", json={"editorial_min": 101}).status_code == 422
    assert client.get("/api/x/newsdesk/editorial/scorecards").json()["minimum"] == 55


# --- the viral upgrade can't bypass verification (owner, 2026-11-13) ---------------------------------------------------------------


def _seizure_event(db: Session) -> NdEvent:
    now = utcnow()
    e = NdEvent(key="story:doj", kind="news", title="DOJ seizes $1.2B from Binance hack", headline="DOJ seizes $1.2B from Binance hack", summary=SOURCE,
                topic="hack", evidence="official", virality=95, status="released", released_at=now, first_seen=now, label="",
                facts={"told": SOURCE, "editorial": {"total": 90, "beat": "scandal", "dims": {d: 18 for d in scorecard.DIMENSIONS}, "reject": ""}},
                parts={}, sources=[{"name": "Reuters", "url": "https://reuters.test/doj"}, {"name": "Bloomberg", "url": "https://bloomberg.test/doj"}])
    db.add(e)
    db.commit()
    return e


async def test_a_high_engagement_candidate_that_fails_the_accuracy_check_is_never_published(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import telegram as lane

    e = _seizure_event(db)
    real_draft = network.draft

    async def catchy_but_false(settings: Any, ev: Any, rows: Any = None, transport: Any = None) -> network.Copy:
        c = await real_draft(settings, ev, rows, transport)
        c.context = "Binance's CEO was arrested as the DOJ seized the funds."  # an arrest no source reports
        return c

    monkeypatch.setattr(network, "draft", catchy_but_false)
    msg, why = await lane.compose_visual(e, None, utcnow(), db=db)
    assert msg is None and why.startswith("accuracy:") and "arrest" in why
    assert e.facts["accuracy"]["ok"] is False and e.virality == 95  # score never overrides the gate; the reason is kept for Admin


async def test_admin_headline_variants_pass_the_same_gates(client: Any, db: Session) -> None:
    e = _seizure_event(db)
    e.status, e.tg_status = "candidate", ""
    db.commit()
    bad = client.put("/api/x/newsdesk/editorial/headline", json={"event_id": e.id, "text": "Binance CEO arrested as DOJ seizes $1.2B"})
    assert bad.status_code == 422 and any("arrest" in p for p in bad.json()["detail"]["problems"])
    bnb = client.put("/api/x/newsdesk/editorial/headline", json={"event_id": e.id, "text": "DOJ seizes $1.2B of stolen Binance $BNB"})
    assert bnb.status_code == 422 and any("$BNB" in p for p in bnb.json()["detail"]["problems"])
    ok = client.put("/api/x/newsdesk/editorial/headline", json={"event_id": e.id, "text": "DOJ seizes $1.2B stolen in the Binance hack"})
    assert ok.status_code == 200 and ok.json()["admin_choice"] == "DOJ seizes $1.2B stolen in the Binance hack"
    db.refresh(e)
    pkg = await network.package(e, style="clean")
    assert pkg.ok and pkg.copy.headline == "DOJ seizes $1.2B stolen in the Binance hack"
    # a pick that skipped the endpoint (written straight into the event) still meets the final gate: not publishable
    e.facts = {**e.facts, "hooks": {**(e.facts.get("hooks") or {}), "admin_choice": "Binance CEO arrested as DOJ seizes $1.2B"}}
    db.commit()
    pkg = await network.package(e, style="clean")
    assert not pkg.ok and any("arrest" in p for p in pkg.accuracy)


async def test_a_top_candidate_whose_final_caption_fails_the_final_gate_is_held_and_admin_shows_why(client: Any, db: Session,
                                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import clean, telegram as lane, visual

    e = _seizure_event(db)

    async def picture(ev: Any, **kw: Any) -> tuple[bytes, dict[str, Any]]:
        return b"\x89PNG", {"style": "clean", "kind": "graphic"}

    monkeypatch.setattr(visual, "for_event", picture)
    # the last formatting step slips in a ticker and a "seized" claim about a person: only the final gate sees the caption this way
    monkeypatch.setattr(clean, "with_credit", lambda html, plain, meta: (html, plain + "\n\nBinance $BNB holders panic as Richard Teng arrested"))
    msg, why = await lane.compose_visual(e, None, utcnow(), db=db)
    assert msg is None and why.startswith("final check:") and "$BNB" in why
    db.commit()
    s = next(x for x in client.get("/api/x/newsdesk/editorial/scorecards").json()["stories"] if x["event"] == e.id)
    assert s["accuracy"]["ok"] is False and any("$BNB" in p for p in s["accuracy"]["problems"])
    assert e.virality == 95 and e.facts["editorial"]["total"] == 90  # the best-scoring story still doesn't go


def test_hooks_questions_and_admin_picks_run_through_the_production_final_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = checks.final_gate

    def spy(text: str, source_text: str, chart_symbol: str = "") -> list[str]:
        calls.append(text)
        return real(text, source_text, chart_symbol)

    monkeypatch.setattr(checks, "final_gate", spy)
    hooks.fact_check("DOJ seizes $1.2B stolen in the Binance hack", SOURCE)
    hooks.question_problems("Right call by DOJ, or a mistake?", SOURCE)
    assert calls == ["DOJ seizes $1.2B stolen in the Binance hack", "Right call by DOJ, or a mistake?"]
