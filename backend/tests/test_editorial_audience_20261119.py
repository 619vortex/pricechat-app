"""Would OUR audience care? (owner, 2026-11-19: "Price Chat's Telegram channel still looks like a boring automated RSS feed ... Stop
publishing ordinary news simply because it's verified or geopolitically significant ... Give each candidate an audience-interest score and
a specific reason to publish. Verification alone must never qualify a story.")

Fixtures are the real headlines of the channel's last 30 posts (Telegram 167-198) and of the labelled 72-hour candidate set."""
import pytest

from app.newsdesk import newsworthy as nw

# --- the three the owner named --------------------------------------------------------------------------------------------------------


def test_telegram_196_binance_freeze_is_kept_with_a_specific_reason() -> None:
    a = nw.qualify("Binance receives Abu Dhabi court order to 'Freeze' assets after $6.3B heist", "", outlets=3)
    assert a.ok and a.interest >= nw.INTEREST_BAR
    assert "$6.3B" in a.pitch and "Binance" in a.pitch  # the reason names the money and the readers' market, not "verified"


def test_telegram_197_a_remark_about_ending_the_war_is_not_an_act() -> None:
    a = nw.qualify("U.S., China can help end Ukraine war, Trump tells Xi before trilateral with Putin", "", outlets=3)
    assert not a.ok and a.strength == "none"  # "can help end" is a remark: the exceptional path no longer reads it as a decisive act
    assert not nw.exceptional("U.S., China can help end Ukraine war, Trump tells Xi before trilateral with Putin", outlets=3).ok


def test_telegram_198_missiles_intercepted_with_nothing_hit_is_a_routine_military_update() -> None:
    a = nw.qualify("Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south", "", outlets=3)
    assert not a.ok and a.strength == "none" and "routine military update" in a.reason


# --- the rest of the last 30 ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("headline,pitch_has", [
    ("Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs ($BTC -5.4% in 24H)", "5.4% move in Bitcoin"),  # 173
    ("Iran launches drone attacks against US military base in Bahrain", "a major attack"),  # 183
    ("US strikes Iranian missile site after Bahrain attacks", "a major attack"),  # 189
    ("Russian diesel exports double after Trump lifts sanctions, but US gets none", "double"),  # 167
])
def test_the_strong_ones_still_go_and_say_why(headline: str, pitch_has: str) -> None:
    a = nw.assess(headline, "More than $1 billion in crypto positions were forced shut." if "Bitcoin" in headline else "")
    assert a.ok, a.reason
    assert pitch_has in a.pitch and a.interest >= nw.INTEREST_BAR


def test_telegram_169_a_routine_partnership_on_our_beat_is_still_routine() -> None:
    a = nw.qualify("Western Union’s USDPT Stablecoin Becomes a Settlement Option on Visa, Anchorage Digital Says", "", outlets=2)
    assert not a.ok and "routine" in a.reason  # being about stablecoins is not a reason on its own


def test_the_strength_of_a_refusal_is_none_so_no_fast_lane_or_bypass_uses_it() -> None:
    a = nw.assess("Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south")
    assert a.strength == "none"  # events.breaks_through and terminal intel.fast_lane both read assess().strength


def test_the_terminal_fast_lane_refuses_the_routine_interception() -> None:
    from app.terminal import intel

    srcs = [{"name": "Al Jazeera", "url": "https://www.aljazeera.com/a"}, {"name": "Reuters", "url": "https://www.reuters.com/b"}]
    ev = {"rumor": False, "score": 99, "level": "official"}
    ok, why = intel.fast_lane("Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south", "", srcs, {"age_hours": 0.1}, ev)
    assert not ok


# --- the labelled 72-hour set: important stories are not lost ----------------------------------------------------------------------


@pytest.mark.parametrize("headline", [
    "Iran targets US military radar in Bahrain in ‘retaliatory’ drone strike",  # the interception headline goes; the strike itself stays
    "China, Canada agree to further reduce tariffs at APEC summit in Shenzhen",  # an agreement is an outcome, not summit talk
    "Supreme Court Ends Custodia Bank’s Fight for a Fed Master Account",  # the real candidate titles (docs/editorial)
    "Judge Acquits MIT Brothers in $25M Ethereum MEV Case, Ending Prosecution",
    "Norway's Equinor detects ongoing cyberattack, oil and gas output unaffected",
    "Outages reported at major US banks including Chase and Bank of America",
    "Estonia to close Russia border after 'sabotage' bid",
])
def test_exceptional_stories_keep_their_place(headline: str) -> None:
    assert nw.qualify(headline, "", outlets=3).ok, nw.qualify(headline, "", outlets=3).reason


@pytest.mark.parametrize("headline,why", [
    ("Bahrain intercepts Iranian aerial attacks", "routine military update"),
    ("China, US discuss tariffs, farm trade ahead of Xi-Trump meeting", ""),
    ("FAA pauses router installations nationwide after tech issue caused Friday's ground stop", ""),
])
def test_ordinary_news_is_refused(headline: str, why: str) -> None:
    a = nw.qualify(headline, "", outlets=3)
    assert not a.ok and why in a.reason


def test_diplomacy_talk_without_an_outcome_is_refused_but_a_signed_deal_is_not() -> None:
    talk = nw.audience("Trump tells Xi the US and China can end the war", "", nw.Assessment(True, "strong", "x", "", 70))
    assert talk[2].startswith("diplomacy talk")
    deal = nw.qualify("Russia and Ukraine sign ceasefire agreement", "", outlets=3)
    assert deal.ok and deal.interest >= nw.INTEREST_BAR
