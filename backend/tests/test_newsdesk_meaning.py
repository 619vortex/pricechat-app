"""Headline meaning checks (owner, 2026-11-13): qualifiers survive, and a headline must say what one source sentence says."""

import pytest

from app.newsdesk import claims, hooks, meaning

ANTHROPIC = ("Anthropic Sets IPO Price Range at $70 to $78, Valuing Company at Up to $1.4 Trillion. The Claude developer is also lining up "
             "$26 billion from Nvidia, Google and other tech giants in a private placement alongside the listing.")


@pytest.mark.parametrize("text", [
    "Anthropic IPO values company at $1.4 trillion",
    "Anthropic eyes $1.4 trillion valuation with $70-$78 IPO price",
    "Anthropic IPO values company at at least $1.4 trillion",
])
def test_up_to_never_becomes_a_flat_or_a_lower_bound(text: str) -> None:
    assert any("1.4" in p and "upper bound" in p for p in meaning.figure_problems(text, ANTHROPIC))


def test_the_qualifier_kept_passes_and_about_may_be_added() -> None:
    assert not meaning.figure_problems("Anthropic IPO could value it at up to $1.4 trillion", ANTHROPIC)
    assert not meaning.figure_problems("Anthropic lines up $26 billion alongside its IPO", ANTHROPIC)
    assert not meaning.figure_problems("Anthropic lines up about $26 billion alongside its IPO", ANTHROPIC)


@pytest.mark.parametrize("src, bad, good", [
    ("At least six people were killed in the strike, officials said.", "Six killed in strike", "At least six killed in strike"),
    ("OKXICE will open its venue Nov. 23 with more than 70 assets.", "OKXICE opens with 70 assets", "OKXICE to open with 70+ assets"),
    ("Brent fell to less than $92 per barrel.", "Brent falls to $92", "Brent falls below $92"),
    ("The exchange lost nearly $6.3 billion in the hack.", "Exchange lost $6.3 billion in hack", "Exchange lost nearly $6.3 billion in hack"),
    ("The exchange lost nearly $6.3 billion in the hack.", "Exchange lost at least $6.3 billion", "Exchange lost almost $6.3 billion"),
])
def test_figure_qualifiers(src: str, bad: str, good: str) -> None:
    assert meaning.figure_problems(bad, src)
    assert not meaning.figure_problems(good, src)


@pytest.mark.parametrize("src, bad, good", [
    ("The founder allegedly diverted customer funds to a hedge fund.", "Founder diverted customer funds to a hedge fund",
     "Founder allegedly diverted customer funds to a hedge fund"),
    ("The exchange is reportedly preparing a US listing, according to people familiar.", "Exchange is preparing a US listing",
     "Exchange reportedly preparing a US listing"),
    ("The new rules could force stablecoin issuers to hold more reserves.", "New rules force stablecoin issuers to hold more reserves",
     "New rules could force stablecoin issuers to hold more reserves"),
    ("Regulators may block the merger of the two exchanges.", "Regulators block the merger of the two exchanges",
     "Regulators may block exchange merger"),
    ("The company plans to buy back $2 billion of its shares.", "Company buys back $2 billion of its shares",
     "Company plans $2 billion share buyback"),
    ("Trump is not planning to attack Iran at this point, officials said.", "Trump planning to attack Iran",
     "Trump not planning Iran attack at this point, officials say"),
])
def test_hedges_and_negations_survive(src: str, bad: str, good: str) -> None:
    assert meaning.problems(bad, src), bad
    assert not meaning.problems(good, src), (good, meaning.problems(good, src))


def test_direction_and_superlatives() -> None:
    src = "Bitcoin rose 4% to $98,000 on Tuesday as ETF inflows returned."
    assert any("a fall" in p for p in meaning.problems("Bitcoin falls 4% to $98,000 as ETF inflows return", src))
    assert any("record" in p for p in meaning.problems("Bitcoin rises 4% to record $98,000", src))
    assert not meaning.problems("Bitcoin rises 4% to $98,000 as ETF inflows return", src)
    assert any("peak" in p for p in meaning.problems("Saudi output tops pre-war peak", "Saudi output jumped 26% to near pre-war levels."))


def test_a_headline_must_be_carried_by_one_source_sentence_not_just_share_words() -> None:
    assert any("no single source sentence" in p for p in meaning.problems("Tech giants rush to fund Claude maker", ANTHROPIC))
    assert not meaning.problems("Claude developer lines up $26 billion from tech giants alongside listing", ANTHROPIC)


def test_attribution_binds_headlines_unless_an_outlet_says_it_in_its_own_voice_or_the_claimant_is_named() -> None:
    elliptic = "North Korean hackers used six memecoins to launder $430,000 from the Binance hack, Elliptic says."
    assert meaning.problems("North Korean hackers launder $430,000 from Binance hack with memecoins", elliptic)
    assert not meaning.problems("Elliptic: North Korean hackers launder $430,000 from Binance hack with memecoins", elliptic)
    both = [elliptic, "North Korean hackers used six memecoins to launder $430,000 from the Binance hack."]
    assert not meaning.problems("North Korean hackers launder $430,000 from Binance hack with memecoins", " ".join(both), both)
    saudi = "Saudi Arabia told OPEC that its crude oil production climbed by 26% last month."
    assert not meaning.problems("Saudi crude oil production climbs 26%", saudi)


def test_captions_check_qualifiers_but_leave_attribution_and_joined_context_to_the_writer() -> None:
    src = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura, officials say. Brent rose up to 3% in early trade."
    assert not meaning.problems("Missile hits Aramco's Ras Tanura oil terminal", src, strict=False)
    assert meaning.problems("Missile hits Aramco's Ras Tanura oil terminal. Brent rose 3%.", src, strict=False)
    assert not meaning.problems("Bullish or bearish for oil?", src)


def test_the_hook_ranker_and_the_caption_checker_both_apply_meaning() -> None:
    problems, _ = hooks.fact_check("Anthropic eyes $1.4 trillion valuation with $70-$78 IPO price", ANTHROPIC)
    assert any("upper bound" in p for p in problems)
    assert any("upper bound" in p for p in claims.check("Anthropic valued at $1.4 trillion in its IPO.", ANTHROPIC, numbers=False))


def test_a_qualifier_any_source_puts_on_a_term_binds_it_even_in_another_sentence() -> None:
    """Audit 2026-11-12: 'Russian diesel exports double after Trump lifts sanctions' while another outlet wrote 'Trump's temporary waiver
    of some sanctions'."""
    sents = ["Russian diesel exports double after Trump lifts sanctions, but US gets none.",
             "Analysts pointed to President Donald Trump's temporary waiver of some sanctions on Moscow's fuel exports."]
    src = " ".join(sents)
    held = meaning.problems("Russian diesel exports double after Trump lifts sanctions", src, sents, strict=False)
    assert any("temporary" in p and "sanction" in p for p in held)
    assert not meaning.problems("Russian diesel exports double after Trump's temporary sanctions waiver", src, sents, strict=False)


def test_a_qualifier_binds_what_follows_it_not_what_comes_before() -> None:
    sents = ["Nvidia's board is investigating Huang's affair with an alleged Chinese spy."]
    assert not meaning.binding_problems("Nvidia board investigates Huang's affair", sents)
    assert meaning.binding_problems("Huang's affair with Chinese spy under investigation", sents)
    assert not meaning.binding_problems("Nvidia shares fell after a report in May", ["Nvidia warned investors in May about export rules."])
