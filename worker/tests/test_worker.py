"""Worker unit tests (no network, no server). TEST FIXTURES only."""

from pathlib import Path

from pricechat_worker import llm, render

JOB = {
    "template": "price_alert", "topic": "Bitcoin is up 3.2% in 24 hours",
    "brief": {"headline": "Bitcoin is up 3.2% in 24 hours", "facts": [{"text": "Bitcoin (BTC) price: $67,412 (CoinGecko).", "source": "CoinGecko"},
                                                                   {"text": "24-hour change: +3.20%.", "source": "CoinGecko"}],
              "sources": [{"name": "CoinGecko", "url": "https://www.coingecko.com"}]},
    "brand": {"name": "Price Chat", "cta": "Follow Price Chat for daily market news.", "personality": "", "audience": "", "language": "English"},
    "strategy": {"min_seconds": 20, "max_seconds": 40, "outro_cta": True, "llm": "template"}, "template_style": {"label": "B"},
}


def test_template_script_uses_only_the_brief() -> None:
    s = llm.finish(llm.write(JOB, [], 1), JOB)
    assert s["engine"] == "template" and [x["scene"] for x in s["segments"]] == ["hook", "chart", "facts", "outro"]
    assert "$67,412" in s["voiceover"] and s["voiceover"].endswith("Follow Price Chat for daily market news.")
    assert s["sources"][0]["name"] == "CoinGecko"


def test_short_never_cuts_a_number() -> None:
    assert llm.short("Bitcoin (BTC) price: $67,412 (CoinGecko).", 26) == "Bitcoin (BTC) price…"


def test_captions_light_up_word_by_word(tmp_path: Path) -> None:
    import glob

    font = next(iter(glob.glob("C:/Windows/Fonts/arialbd.ttf") + glob.glob("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")))
    segs = [{"text": "Bitcoin just jumped."}, {"text": "It's up 3.2 percent today, according to CoinGecko."}]
    n = render.captions(segs, [(0.0, 1.5), (1.7, 5.0)], (0, 229, 143), font, tmp_path / "c.ass")
    text = (tmp_path / "c.ass").read_text(encoding="utf-8")
    assert n >= 3 and "PlayResX: 1080" in text and "{\k" in text and "&H008FE500" in text  # accent in ASS BGR order


def test_the_voice_reads_numbers_the_way_people_say_them() -> None:
    from pricechat_worker import tts

    assert tts.spoken("A Gengar sold for $150,000.") == "A Gengar sold for 150,000 dollars."
    assert tts.spoken("BTC is +3.2% in 24h, $15B traded, #1 by market cap.") == "B T C is plus 3.2 percent in 24 hours, 15 billion dollars traded, number 1 by market cap."


def test_weak_scripts_are_sent_back_for_craft() -> None:
    weak = {"segments": [{"text": "Hey guys, in this video we are going to take a look at what happened to the price of Bitcoin over the last day.",
                          "screen": "What happened to the Bitcoin price over the last day"}]}
    problems = llm.style_problems(weak)
    assert any("hook" in p for p in problems) and any("in this video" in p for p in problems) and any("2-5 words" in p for p in problems)
    assert llm.style_problems({"segments": [{"text": "Bitcoin just jumped 3.2 percent.", "screen": "BTC +3.2%"}]}) == []


def test_template_hook_leads_with_the_number() -> None:
    job = {**JOB, "template": "pokemon_spotlight", "brief": {"headline": "Gengar PSA 10 sold for $150,000", "numbers": {"sale_usd": 150000.0},
                                                            "card": {"name": "Gengar"}, "facts": [], "sources": [{"name": "eBay"}]}}
    s = llm.template_script(job)
    assert s["hook"] == "$150,000. For one Gengar card." and s["segments"][-1]["text"].startswith("Would you ever pay that for one card?")
