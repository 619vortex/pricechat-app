"""PRICE CHAT VIRAL ENGINE 2.0 (owner, 2026-11-11): five hooks per story, only factual ones survive, the strongest goes out; the money
angle from our own prices; no empty posts; controlled experiments; the engagement learner; data posts and MARKETS GONE MAD."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from typing import Any

import pytest
from PIL import Image
import io

from app.models.newsdesk import NdPost
from app.newsdesk import datacards, datapost, engagement, money, network, sources, viral

NOW = datetime(2026, 11, 11, 14, 0, tzinfo=timezone.utc)
FIRST = NOW - timedelta(hours=2)

TANKER = [NS(source="Reuters", link="https://www.reuters.com/world/tanker-1", title="Tanker struck near Strait of Hormuz, Iran denies involvement",
             summary="A crude tanker carrying 2 million barrels was struck near the Strait of Hormuz. Iran denied any involvement, even as US officials "
                     "blamed Tehran. It was the biggest attack on shipping in the strait since 2019."),
          NS(source="Bloomberg", link="https://www.bloomberg.com/news/articles/tanker-2", title="Tanker hit near Hormuz as US blames Iran",
             summary="US officials blamed Iran for the strike on the tanker. Iran rejected the accusation.")]


def ev(**kw: Any) -> NS:
    base = dict(id=1, key="story:https://www.reuters.com/world/tanker-1", kind="news", title=TANKER[0].title, headline=TANKER[0].title, summary=TANKER[0].summary,
                topic="geopolitics", label="", evidence="two outlets", category="politics", sources=[{"name": r.source, "url": r.link} for r in TANKER],
                facts={"outlets": ["Reuters", "Bloomberg"], "told": " ".join(f"{r.title} {r.summary}" for r in TANKER)}, released_at=None, parent_id=None)
    return NS(**{**base, **kw})


def quote(symbol: str, then: float, now_price: float, *, start: datetime = FIRST - timedelta(hours=1), end: datetime = NOW) -> sources.Quote:
    """5-minute bars: flat at `then` until FIRST, then a straight walk to `now_price`."""
    bars, t = [], start
    while t <= end:
        if t <= FIRST:
            p = then
        else:
            p = then + (now_price - then) * (t - FIRST).total_seconds() / (end - FIRST).total_seconds()
        bars.append((int(t.timestamp()), p, 100.0))
        t += timedelta(minutes=5)
    return sources.Quote(symbol, now_price, end, None, None, bars)


QUOTES = {"BZ=F": quote("BZ=F", 80.0, 83.2), "CL=F": quote("CL=F", 76.0, 78.9), "GC=F": quote("GC=F", 4000.0, 4080.0), "^GSPC": quote("^GSPC", 7000.0, 6930.0)}


# --- the money angle ---------------------------------------------------------------------------------------------------------------


def test_the_money_angle_is_measured_since_the_first_report_and_ignores_noise() -> None:
    rs = money.reaction("Tanker struck near the Strait of Hormuz; oil and gold react", QUOTES, FIRST, NOW)
    got = {r.symbol: r.pct for r in rs}
    assert got["BZ=F"] == 4.0 and got["CL=F"] == 3.8 and got["GC=F"] == 2.0  # measured: 80 -> 83.2 is +4.0%
    quiet = {**QUOTES, "GC=F": quote("GC=F", 4000.0, 4060.0)}  # +1.5%: under gold's noise line (1.9%), any afternoon's move, not a reaction
    assert "GC=F" not in {r.symbol for r in money.reaction("oil and gold", quiet, FIRST, NOW)}
    stale = {"BZ=F": quote("BZ=F", 80.0, 83.2, end=NOW - timedelta(hours=1))}  # an hour-old price claims nothing
    assert money.reaction("oil", stale, FIRST, NOW) == []
    lines = money.lines(money.reaction("tanker hit, stocks and oil", QUOTES, FIRST, NOW))
    assert lines["winners_losers"].startswith("Winners: Brent crude +4.0%") and "S&P 500 −1.0%".replace("−", "-") in lines["winners_losers"]


# --- the five hooks -------------------------------------------------------------------------------------------------------------


def test_five_hooks_are_built_only_from_what_the_reporting_and_our_prices_support() -> None:
    p = viral.plan(ev(), TANKER, QUOTES, NOW, since=FIRST, experiments=False)
    hooks = {a.hook: a for a in p.angles}
    assert set(hooks) == set(viral.HOOKS) - {"winners_losers"}  # oil and gold both rose: no measured loser, so no winners-and-losers post
    assert "biggest attack" in hooks["shock"].evidence  # a record is the shock
    assert "denied" in hooks["controversy"].evidence or "denies" in hooks["controversy"].evidence  # attributed conflict, not invented outrage
    assert "even as" in hooks["contradiction"].evidence
    assert hooks["money"].copy.stakes.startswith("Since the first report: Brent crude +4.0%")
    assert all(a.ok for a in p.angles), [a.problems for a in p.angles]
    assert p.chosen is not None and p.chosen.score == max(a.score for a in p.angles)
    # without prices there is no money angle and no winners and losers: nothing is guessed
    bare = viral.plan(ev(), TANKER, {}, NOW, since=FIRST, experiments=False)
    assert {a.hook for a in bare.angles} == {"shock", "controversy", "contradiction"}
    stocks = viral.plan(ev(title=TANKER[0].title + " as Wall Street stocks fall"), TANKER, QUOTES, NOW, since=FIRST, experiments=False)
    wl = next(a for a in stocks.angles if a.hook == "winners_losers")  # the S&P 500 fell as oil rose: both sides measured
    assert wl.ok and wl.copy.stakes.startswith("Winners: Brent crude +4.0%") and "Losers: S&P 500" in wl.copy.stakes


def test_a_plain_story_gets_no_fake_controversy_or_contradiction() -> None:
    plain = [NS(source="CoinDesk", link="https://www.coindesk.com/a", title="Samsung Wallet adds USDC on Solana for US Galaxy users",
                summary="Eligible US Galaxy users can now buy, hold and send USDC on Solana in Samsung Wallet.")]
    e = ev(title=plain[0].title, headline=plain[0].title, summary=plain[0].summary, topic="corporate", facts={"told": plain[0].summary})
    p = viral.plan(e, plain, {}, NOW, since=FIRST)
    assert {a.hook for a in p.angles} <= {"shock"}  # nothing in the reporting supports conflict, contradiction or a money move


def test_a_hook_with_an_invented_figure_or_hype_never_wins() -> None:
    p = viral.plan(ev(), TANKER, QUOTES, NOW, since=FIRST, experiments=False)
    a = next(x for x in p.angles if x.hook == "money")
    a.copy.stakes = "Brent crude +9.0% since the first report."  # we measured +4.0%
    assert any("figures no source gave" in x for x in network.verify_copy(a.copy, network.source_text_of(ev(), TANKER) + " " + p.out()["measured"]["reaction"]))
    hyped = viral.Angle("shock", network.Copy(headline="Oil bloodbath as tanker obliterated near Hormuz", short="x"), "", 1.0)
    assert network.verify_copy(hyped.copy, network.source_text_of(ev(), TANKER))  # hype is refused before it can score


def test_the_hook_score_rewards_concrete_tense_short_and_supported() -> None:
    strong = viral.Angle("money", network.Copy(headline="Brent +4.0% since a tanker was hit near Hormuz", short="", stakes="Since the first report: Brent crude +4.0%."), "", 0.9)
    weak = viral.Angle("shock", network.Copy(headline="Here's what to know about what could happen next in the shipping lanes of the Middle East region", short=""), "", 0.2)
    s1, p1 = viral.hook_score(strong)
    s2, p2 = viral.hook_score(weak)
    assert s1 > s2 + 30
    assert p1["front"] == 6 and p1["money"] == 10 and p1["concrete"] >= 12
    assert p2["vague"] == -10 and p2["brevity"] == -8
    boosted, parts = viral.hook_score(strong, {"money": 5.0})
    assert boosted == s1 + 5 and parts["audience"] == 5.0  # what our audience engaged with lately


@pytest.mark.parametrize("headline,empty", [
    ("Live updates: Iran tanker strike", True), ("What we know about the Hormuz tanker attack", True), ("Here's what to know about Fed day", True),
    ("Markets wrap", True), ("Morning briefing: oil, gold and bitcoin", True),
    ("Tanker struck near Hormuz, Iran denies involvement", False), ("Brent jumps 4% after tanker strike", False),
    ("Iran opens training camp for women in readiness for war", False), ("Markets react to the news today", True)])
def test_no_empty_posts(headline: str, empty: bool) -> None:
    assert bool(viral.substance(network.Copy(headline=headline, short=""))) is empty


def test_experiments_try_the_runner_up_only_when_it_is_close_and_factual() -> None:
    keys = [f"story:{i}" for i in range(200)]
    share = sum(viral.explore(k) for k in keys) / len(keys)
    assert 0.12 < share < 0.28  # about one story in five, decided by its key (stable, auditable)
    k = next(k for k in keys if viral.explore(k))
    p = viral.plan(ev(key=k), TANKER, QUOTES, NOW, since=FIRST)
    ok = sorted((a for a in p.angles if a.ok), key=lambda a: -a.score)
    if ok[1].score >= max(viral.FLOOR, ok[0].score - viral.MARGIN):
        assert p.exploring and p.chosen is ok[1] and p.out()["experiment"]["control"] == ok[0].hook
    off = viral.plan(ev(key=k), TANKER, QUOTES, NOW, since=FIRST, experiments=False)  # Admin switch off: always the best
    assert not off.exploring and off.chosen.hook == ok[0].hook


def test_the_writer_gets_the_hook_and_the_voice_but_no_wit_on_sombre_stories() -> None:
    facts = {**ev().facts, "viral": {"hook": "money", "measured": {"reaction": "Since the first report: Brent crude +4.0%."}}}
    calm = ev(title="OPEC+ agrees to raise output by 400,000 barrels a day", summary="The group agreed the increase on Sunday.", facts=facts)
    assert "Angle: Market consequence" in viral.prompt(calm) and "Crypto Twitter's attitude" in viral.prompt(calm)
    assert "MARKET REACTION (measured on our prices): Since the first report: Brent crude +4.0%." in network.reporting(calm, TANKER)
    assert "no wit at all" in viral.prompt(ev(facts=facts))  # an attack on shipping: sober


def test_a_draft_must_say_who_and_never_turn_a_measured_move_into_a_cause() -> None:
    p = viral.plan(ev(), TANKER, QUOTES, NOW, since=FIRST, experiments=False)
    e = ev(facts={**ev().facts, "viral": p.out()})
    nameless = network.Copy(headline="Tanker struck as tensions rise in the Gulf", short="")
    assert any("doesn't say who" in x for x in viral.draft_problems(e, nameless, TANKER))
    binance = ev(title="Binance’s $6.3B hack leaves questions over hot wallet safeguards", summary="Experts cannot establish Binance’s signing controls.")
    assert any("doesn't say who (Binance)" in x for x in viral.draft_problems(binance, network.Copy(headline="$6.3B hack leaves questions over hot wallet safeguards", short=""), []))
    causal = network.Copy(headline="Brent jumps 4.0% as Iran denies tanker strike near Hormuz", short="")
    assert any("measured move" in x for x in viral.draft_problems(e, causal, TANKER))
    fine = network.Copy(headline="Iran denies striking a tanker near Hormuz as the US blames Tehran", short="", stakes="Since the first report: Brent crude +4.0%.")
    assert viral.draft_problems(e, fine, TANKER) == []
    assert "never in the headline or as a cause" in viral.prompt(e)
    spy = ev(title="Nvidia hires law firm to investigate alleged Chinese spy affair", summary="Claims its boss had an affair with an alleged Chinese spy.")
    rows = [NS(source="BBC", link="https://www.bbc.co.uk/news/a", title=spy.title, summary=spy.summary)]
    assert any("states it as fact" in x for x in viral.draft_problems(spy, network.Copy(headline="Nvidia hires Wachtell to probe CEO's affair with Chinese spy", short="", writer="local"), rows))
    assert not any("states it as fact" in x for x in viral.draft_problems(spy, network.Copy(headline="Nvidia hires Wachtell to probe CEO's alleged spy affair", short=""), rows))


def test_writer_headlines_come_out_in_sentence_case_keeping_names_and_tickers() -> None:
    src = "North Korean hackers used new tokens on NEAR to launder funds stolen from Binance, Elliptic said."
    assert network.sentence_case("North Korean Hackers Launder Binance Loot With Meme Coins on NEAR", src) == \
        "North Korean hackers launder Binance loot with meme coins on NEAR"
    assert network.sentence_case("Nvidia hires ex-prosecutor to probe CEO's affair", src) == "Nvidia hires ex-prosecutor to probe CEO's affair"
    assert network.sentence_case("Samsung Wallet Launches USDC For Galaxy Users", "Eligible U.S. Galaxy users can now hold USDC.") == \
        "Samsung wallet launches USDC for Galaxy users"  # Galaxy only ever appears capitalised: a name
    assert network.desk_copy(ev(kind="market", headline="S&P 500 Falls 3.8% in 24 Hours", title="S&P 500 Falls 3.8% in 24 Hours", summary="")).headline == "S&P 500 falls 3.8% in 24 hours"
    nyt = "Nvidia Hires Wachtell Lipton to Investigate CEO Jensen Huang’s Affair With Chinese Executive"
    assert network.desk_copy(ev(headline=nyt, title=nyt, summary="")).headline == nyt  # an outlet's names stay as the outlet wrote them
    assert datapost.price(7354.2, "^GSPC") == "7,354" and datapost.price(73550.0, "BTC-USD") == "$73,550"  # an index is points, not dollars
    long = network.Copy(headline="Binance’s $6.3B hack leaves questions over hot wallet safeguards", short="",
                        context="Experts say blockchain records show repeated wallet drains but cannot establish Binance’s internal signing controls or transaction limits, which leaves users guessing.")
    x = network.x_caption(long, [network.Link("Cointelegraph", "https://cointelegraph.com/news/binances-hack-questions-hot-wallet-risk-controls", "article")])
    assert "Experts say blockchain records" in x and network.x_length(x) <= 280  # shortened, not dropped
    c = network.Copy(headline="x", short="", context="Nvidia said Huang's affair is under review.")
    assert network.verify_copy(c, "Nvidia hires a law firm to look into Jensen Huang’s affair") == []  # either apostrophe


def test_a_routine_move_is_never_paired_with_an_unrelated_story() -> None:
    samsung = ev(title="Samsung Wallet launches USDC accounts on Solana for US Galaxy users", headline="Samsung Wallet launches USDC accounts on Solana",
                 summary="Stablecoin accounts for Galaxy users.", topic="adoption")
    btc = {"BTC-USD": quote("BTC-USD", 100000.0, 98400.0), "SOL-USD": quote("SOL-USD", 200.0, 197.0)}  # -1.6%, -1.5%: an ordinary afternoon
    assert viral.plan(samsung, [], btc, NOW, since=FIRST).reactions == []


async def test_the_chosen_hooks_plain_draft_goes_when_no_writer_passes() -> None:
    p = viral.plan(ev(), TANKER, QUOTES, NOW, since=FIRST, experiments=False)
    e = ev(facts={**ev().facts, "viral": p.out(), "local_copy": {"headline": "Oil soars 12% on Hormuz attack", "short": "", "context": "", "stakes": "", "question": ""}})
    c = await network.write(None, e, TANKER)
    assert c.writer == "angle" and c.rejected[0].startswith("local: figures")  # the PC's draft invented 12%
    assert c.headline == p.chosen.copy.headline


# --- the engagement learner ------------------------------------------------------------------------------------------------------


def _post(db: Any, hook: str, rate: float, *, exploring: bool = False, control: str = "shock", fmt: str = "photo") -> None:
    db.add(NdPost(kind="news", category="viral", status="published", text="x", key=f"k{hook}{rate}{exploring}{fmt}", fingerprint="", score=1.0, published_at=NOW - timedelta(days=1),
                  facts={"topic": "geopolitics", "viral": {"hook": hook, "exploring": exploring, "control": control, "format": fmt, "question": True, "links": True}},
                  metrics={"impression_count": 1000, "like_count": int(rate * 1000)}))


def test_the_learner_scores_hooks_from_our_own_metrics_and_reports_experiments(db: Any) -> None:
    for i in range(6):
        _post(db, "money", 0.04 + i / 1000, control="money")
        _post(db, "shock", 0.01 + i / 1000, exploring=True, control="money", fmt="card")
    db.commit()
    adj = engagement.adjustments(db, NOW)
    assert adj["hook:money"] > 0 > adj["hook:shock"]
    rep = engagement.report(db, NOW)
    assert rep["measured_posts"] == 12 and rep["features"]["format"]["photo"]["vs_average"] > 1
    exp = next(x for x in rep["experiments"] if x["arm"] == "shock")
    assert exp["control"] == "money" and exp["verdict"] == "control better" and exp["lift"] < 0


def test_nothing_is_learned_from_too_few_posts(db: Any) -> None:
    _post(db, "money", 0.05)
    db.commit()
    assert engagement.adjustments(db, NOW) == {} and engagement.report(db, NOW)["learning"] is False


# --- data posts and MARKETS GONE MAD -----------------------------------------------------------------------------------------------


def daily(start: float, moves_pct: list[float], end: datetime = NOW, *, base_noise: float = 1.0) -> list[tuple[datetime, float]]:
    """Daily closes ending at `end`: a gentle alternating history, then the given last moves."""
    out, p = [], start
    n = 400
    for i in range(n):
        p *= 1 + (base_noise if i % 2 else -base_noise * 0.98) / 100
        out.append((end - timedelta(days=n + len(moves_pct) - i), p))
    for i, m in enumerate(moves_pct):
        p *= 1 + m / 100
        out.append((end - timedelta(days=len(moves_pct) - 1 - i), p))
    return out


def test_an_anomaly_names_the_last_time_it_moved_this_much() -> None:
    series = daily(60000, [-9.0])
    p = datapost.anomaly("BTC-USD", series, NOW)
    assert p is not None and p.headline == "Bitcoin's biggest one-day drop in at least two years: −9.0%"
    assert "−9.0% on the day" in p.told and "−9.0% on the day" not in " ".join(p.lines) and p.interest >= 75  # the chart and the checks have it, not the caption
    assert p.lines == ["A typical day over the last 90 moved it 1.0%: today was 9.1 times that."]  # the takeaway
    with_earlier = daily(60000, [-9.5, *[0.5] * 200, -9.0])
    q = datapost.anomaly("BTC-USD", with_earlier, NOW)
    assert q is not None and "since " in q.headline  # the date we found, e.g. "since April 2026"
    assert datapost.anomaly("BTC-USD", daily(60000, [1.1]), NOW) is None  # an ordinary day is no post


def test_battles_rankings_and_the_daily_mad_pick_only_post_when_compelling() -> None:
    data = {"BTC-USD": daily(60000, [-1.0] * 30), "GC=F": daily(3000, [0.6] * 30)}
    b = datapost.battle("BTC-USD", "GC=F", data, NOW, 30)
    assert b is not None and b.headline.startswith("Bitcoin vs gold over the past 30 days: −")
    assert all(x in b.told for x in ("Bitcoin", "gold", "percentage points"))
    year = datapost.battle("BTC-USD", "GC=F", data, NOW, 365)
    assert year is None or year.why.get("slow") == -15  # a year-long gap is a chart, rarely news today
    many = {s: daily(100, [m] * 7) for (s, _, _), m in zip(datapost.ASSETS, [3, -4, 2, 1, -1, 0.5, 0.2, -0.3, -2.6, 1.5, 0.7, -0.8, 0.1])}
    r = datapost.ranking(many, NOW)
    assert r is not None and len(r.bars) == 10 and r.bars[0][0] == "Bitcoin" and r.bars[-1][0] == "Ether"  # the five best and the five worst
    assert datapost.markets_gone_mad([]) is None
    dull = datapost.DataPost("battle", "battle:a:b:30", "x", [], 60)
    assert datapost.markets_gone_mad([dull]) is None  # under the bar: most days nothing is MARKETS GONE MAD
    assert datapost.markets_gone_mad([datapost.DataPost("battle", "battle:a:b:30", "x", [], 95)]) is None  # a trend, not today's madness
    assert datapost.markets_gone_mad([datapost.DataPost("anomaly", "anomaly:BTC-USD", "x", [], 80)]) is not None


def test_data_cards_and_the_timeline_are_drawn_to_scale_at_16_by_9() -> None:
    data = {"BTC-USD": daily(60000, [-1.0] * 30), "GC=F": daily(3000, [0.6] * 30)}
    b = datapost.battle("BTC-USD", "GC=F", data, NOW, 30)
    img, meta = datacards.data_card(b.out(), when=NOW, mad=True)
    # 2026-11-19: drawn by the premium templates (app/newsdesk/templates), readable on a phone
    assert Image.open(io.BytesIO(img)).size == (1600, 900) and meta["mad"] is True and meta["template"] == "battle" and meta["design"] == "data"
    bars, meta2 = datacards.data_card({"kind": "ranking", "headline": "x", "bars": [["A", 3.0], ["B", -2.0], ["C", 1.0]]}, when=NOW)
    assert meta2["template"] == "ranking" and meta2["phone_min_px"] >= 8
    tl, meta3 = datacards.timeline_card(headline="Bybaz reopens withdrawals", entries=[(NOW - timedelta(days=2), "Bybaz hacked for $40M"),
                                                                                   (NOW - timedelta(days=1), "Bybaz halts withdrawals"), (NOW, "Bybaz reopens withdrawals")], when=NOW)
    assert Image.open(io.BytesIO(tl)).size == (1600, 900) and meta3["design"] == "timeline"


def test_the_mad_kicker_and_breaking_tag_are_on_both_platforms() -> None:
    c = network.Copy(headline="Bitcoin's biggest one-day drop since April 2025: −9.0%", short="")
    x = network.x_caption(c, [], outlets=["Yahoo Finance"], kicker="MARKETS GONE MAD")
    html, plain = network.telegram_caption(c, [], "Measured on our price data (Yahoo Finance)", outlets=["Yahoo Finance"], kicker="MARKETS GONE MAD")
    assert x.startswith("MARKETS GONE MAD\n\n") and html.startswith("<b>🤯 MARKETS GONE MAD</b>") and plain.startswith("MARKETS GONE MAD")
    from app.newsdesk import visual

    args = visual._design_args(ev(label="🚨 BREAKING"), {"type": "geopolitics"}, NOW)
    assert args["kicker"] == "PRICE CHAT BREAKING"
