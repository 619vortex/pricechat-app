"""Content Studio v2 worker pieces, offline: no model, no renderer, no network (the card image fetch is not exercised)."""

from pathlib import Path

from pricechat_worker import creative, script2, storyboard, timing, voice2

BRIEF = {
    "headline": "Rocket's Mewtwo PSA 10 sold for $17,225",
    "facts": [{"text": "Rocket's Mewtwo (Gym Challenge, 2000) PSA 10 sold for $17,225 on October 12, 2026.", "source": "eBay"},
              {"text": "PSA 10 is PSA's highest grade, called Gem Mint.", "source": "PSA grading standards"}],
    "card": {"name": "Rocket's Mewtwo", "set_name": "Gym Challenge", "year": 2000},
    "numbers": {"sale_usd": 17225.0, "year": 2000}, "images": [], "sources": [{"name": "eBay", "url": "https://example.test/card"}],
}
JOB = {"video_id": 3, "template": "pokemon_spotlight", "category": "pokemon_rare", "brief": BRIEF,
       "brand": {"name": "Price Chat", "website": "https://pricechat.live", "cta": "Follow Price Chat."}}


def test_presets_resolve_and_owner_choices_win() -> None:
    cr = creative.resolve({**JOB, "creative": {"preset": "viral", "narrator": "documentary", "tone": "auto"}})
    assert cr["style"] == "pokemon" and cr["intensity"] == "high" and cr["captions"] == "boxed" and cr["pace"] == "fast"
    assert cr["narrator"] == "documentary" and cr["tone"] == "dramatic"  # explicit choice beats the preset; auto keeps it
    assert creative.resolve({**JOB, "creative": {"narrator": "nobody"}})["narrator"] == "documentary"  # unknown -> the story's default


def test_pronunciation_fixes_names_but_captions_keep_the_written_word() -> None:
    assert voice2.spoken("A Pokémon card: Rocket's Mewtwo, PSA 10, $17,225.") == "A Pokey-mon card: Rocket's Mew two, PSA 10, 17,225 dollars."
    assert voice2.spoken("Hyperliquid +3.2%", {"Hyperliquid": "Hyper liquid exchange"}) == "Hyper liquid exchange plus 3.2 percent"


def test_voice_plan_turns_beats_into_pauses() -> None:
    cr = creative.resolve({**JOB, "creative": {"preset": "documentary"}})
    takes = voice2.plan([{"beat": "hook", "text": "Someone paid $17,225. [beat] For cardboard."}, {"beat": "ending", "text": "Would you?"}], cr)
    assert [t["text"] for t in takes] == ["Someone paid 17,225 dollars.", "For cardboard.", "Would you?"]
    assert takes[0]["gap"] == creative.NARRATORS["documentary"]["beat_gap"] and takes[-1]["gap"] == 0.0
    assert takes[0]["speed"] > takes[2]["speed"]  # the hook is said a touch faster


def test_caption_words_follow_the_recognised_voice_and_never_overlap() -> None:
    segs = [{"text": "Someone paid $17,225 for cardboard.", "emphasis": ["cardboard"]}, {"text": "It's Rocket's Mewtwo — PSA 10.", "emphasis": []}]
    spans = [(0.0, 2.4), (2.7, 4.6)]
    asr = {"words": [{"w": "Someone", "s": 0.0, "e": 0.4}, {"w": "paid", "s": 0.4, "e": 0.7}, {"w": "$17", "s": 0.7, "e": 1.1},
                     {"w": ",225", "s": 1.1, "e": 1.6}, {"w": "for", "s": 1.6, "e": 1.8}, {"w": "cardboard.", "s": 1.8, "e": 2.4},
                     {"w": "It's", "s": 2.7, "e": 2.9}, {"w": "Rocket's", "s": 2.9, "e": 3.3}, {"w": "Mu-2", "s": 3.3, "e": 3.8},
                     {"w": "PSA", "s": 3.9, "e": 4.2}, {"w": "10.", "s": 4.2, "e": 4.6}], "text": "..."}
    words, stats = timing.words(segs, spans, asr, "pop")
    assert [w["t"] for w in words] == ["Someone", "paid", "$17,225", "for", "cardboard.", "It's", "Rocket's", "Mewtwo,", "PSA", "10."]
    assert words[2]["s"] == 0.7 and words[2]["hl"] and words[4]["hl"]  # the number, timed from the voice, and the emphasis word
    assert 3.2 <= words[7]["s"] <= 3.9  # "Mewtwo" heard as "Mu-2": placed between its matched neighbours
    assert all(a["e"] <= b["s"] for a, b in zip(words, words[1:]))
    assert stats["matched"] >= 0.8
    phrases: dict[int, list[str]] = {}
    for w in words:
        phrases.setdefault(w["p"], []).append(w["t"])
    assert all(1 < len(p) <= 3 for p in phrases.values())  # punchy captions, and no lone orphan word


def test_without_recognition_words_share_their_segment() -> None:
    words, stats = timing.words([{"text": "One two three four."}], [(1.0, 3.0)], None, "clean")
    assert words[0]["s"] == 1.0 and abs(words[-1]["e"] - 3.0) < 1e-6 and not stats["asr"]
    assert len({w["p"] for w in words}) == 1  # calm captions: up to 5 words a phrase


def test_storyboard_shows_only_verified_data(tmp_path: Path) -> None:
    cr = creative.resolve({**JOB, "creative": {"preset": "documentary"}})
    segs = [{"beat": "hook", "visual": "price_reveal", "text": "a", "screen": "$17,225"},
            {"beat": "context", "visual": "card_entrance", "text": "b", "screen": "Rocket's Mewtwo"},
            {"beat": "development", "visual": "evidence", "text": "c", "screen": "Sold on eBay"},
            {"beat": "ending", "visual": "chart", "text": "d", "screen": "Would you?"}]
    spans = [(0.0, 3.0), (3.3, 6.0), (6.3, 9.0), (9.3, 12.0)]
    props = storyboard.build(JOB, cr, segs, spans, [{"t": "a", "s": 0.1, "e": 0.5, "p": 0, "hl": False}], tmp_path)
    kinds = [s["kind"] for s in props["scenes"]]
    assert kinds == ["price_reveal", "punchline", "evidence", "punchline", "outro"]  # no image: no card scene; no history: no chart
    reveal, record = props["scenes"][0]["data"], props["scenes"][2]["data"]
    assert reveal["value"] == 17225.0 and reveal["label"] == "SOLD FOR" and reveal["sub"] == "Rocket's Mewtwo · PSA 10"
    assert {"k": "Sold", "v": "October 12, 2026"} in record["rows"] and {"k": "Marketplace", "v": "eBay"} in record["rows"]
    assert props["gold"] is True and props["scenes"][0]["transition"] == "cut"
    trans = [s["transition"] for s in props["scenes"]]
    assert all(a != b for a, b in zip(trans, trans[1:]))
    assert storyboard.unsupported(props, BRIEF) == []
    props["scenes"][1]["screen"] = "Up 40% this year"
    assert storyboard.unsupported(props, BRIEF) == ["punchline: 40"]


def test_script_checks_send_back_cliches_and_unsupported_claims() -> None:
    cliche = {"segments": [{"text": "Seventeen thousand dollars for a Pokémon card? It happened."}, {"text": "A good investment?"}]}
    probs = script2.style_problems(cliche)
    assert any("bare rhetorical question" in p for p in probs) and any("money or investing" in p for p in probs)
    claims = {"segments": [{"text": "Gym Challenge cards are popular, and this one is perfect."}], "title": "", "description": ""}
    assert "popular" in script2.claim_problems(claims, BRIEF)[0] and "perfect" in script2.claim_problems(claims, BRIEF)[0]
    ok = {"segments": [{"text": "PSA 10 is the highest grade, called Gem Mint."}], "title": "", "description": ""}
    assert script2.claim_problems(ok, BRIEF) == []
    assert script2.available_visuals({**JOB, "brief": {**BRIEF, "images": [{"url": "https://x.test/c.png"}]}}) == \
        ["price_reveal", "card_entrance", "card_detail", "evidence", "punchline"]


def test_spoken_amounts_and_condition_claims_are_checked() -> None:
    assert script2.spoken_amounts_off("Cardboard, and nearly twenty grand.", BRIEF) == ["twenty grand"]
    assert script2.spoken_amounts_off("Seventeen grand. Seventeen thousand two hundred twenty-five dollars.", BRIEF) == []
    probs = script2.claim_problems({"segments": [{"text": "Basically untouched. No flaws. A tough grade to get, and nearly twenty grand."}]}, BRIEF)
    assert "untouched" in probs[0] and "no flaws" in probs[0].lower() and "tough grade to get" in probs[0] and "twenty grand" in probs[1]


NEWS = {"headline": "Bitcoin is rejected at $83K as US bond yields reverse higher", "facts": [{"text": "Bitcoin was rejected near $83,000.", "source": "Cointelegraph"}],
        "numbers": {}, "sources": [{"name": "Cointelegraph", "url": "https://example.test/news"}]}
MEDIA = {"coin": {"symbol": "BTC", "price_usd": 81966.0, "change_24h_pct": 1.16, "logo": "coin.png",
                  "chart": {"points": [[i * 3600, 81000.0 + i * 20] for i in range(49)], "label": "BTC · last 48h · Hyperliquid, live"}},
         "photos": [{"src": f"photo_{i}.jpg", "ratio": 0.75, "credit": f"Photo: Someone {i} · CC BY 2.0"} for i in range(3)]}


def test_news_story_finds_its_coin_and_offers_the_live_and_photo_scenes() -> None:
    from pricechat_worker import media

    assert media.coin_of(NEWS) == "BTC" and media.coin_of({"headline": "Fed holds rates"}) is None
    job = {"template": "breaking_news", "brief": NEWS, "media": MEDIA}
    vis = script2.available_visuals(job)
    assert {"coin", "chart", "photo", "headline"} <= set(vis) and "stat" not in vis  # no live number under the script's words
    assert "photo" not in script2.available_visuals({"template": "breaking_news", "brief": NEWS})


def test_storyboard_puts_photos_behind_words_and_never_repeats_the_same_shot(tmp_path: Path) -> None:
    job = {"video_id": 5, "template": "breaking_news", "brief": NEWS, "media": MEDIA, "brand": {"name": "Price Chat"}}
    cr = creative.resolve({**job, "creative": {"preset": "viral"}})
    segs = [{"visual": "punchline", "screen": "REJECTED", "text": "a"}, {"visual": "coin", "screen": "NEAR $83,000", "text": "b"},
            {"visual": "chart", "screen": "SLIDE", "text": "c"}, {"visual": "punchline", "screen": "TOUGH DAY", "text": "d"},
            {"visual": "punchline", "screen": "WATCH", "text": "e"}, {"visual": "punchline", "screen": "YIELDS", "text": "f"},
            {"visual": "punchline", "screen": "BYE", "text": "g"}]
    spans = [(i * 3.0, i * 3.0 + 2.6) for i in range(len(segs))]
    props = storyboard.build(job, cr, segs, spans, [], tmp_path)
    kinds = [s["kind"] for s in props["scenes"]]
    assert kinds[:3] == ["photo", "coin", "chart"] and "photo" in kinds[3:] and kinds[-1] == "outro"
    coin = props["scenes"][1]["data"]
    assert coin["value"] == 81966.0 and coin["source"] == "Hyperliquid · live" and props["assets"]["coin"] == "coin.png"
    assert len(props["scenes"][2]["data"]["points"]) == 49 and "Hyperliquid" in props["scenes"][2]["data"]["label"]
    shots = [(s["data"].get("photo") or {}).get("src") for s in props["scenes"]]
    assert all(a is None or a != b for a, b in zip(shots, shots[1:]))  # never the same photo twice in a row
    assert all(s["data"]["photo"]["credit"].startswith("Photo:") for s in props["scenes"] if s["kind"] == "photo")
    screens = [s["screen"] for s in props["scenes"] if s["screen"]]
    assert len(screens) == len(set(screens))  # the same words never come back as a second shot
    assert storyboard.unsupported(props, NEWS, MEDIA) == [] and storyboard.unsupported(props, NEWS) != []  # live numbers need the live data
