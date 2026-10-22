"""Content Studio v4: the editorial gate, the edit builder, graphics fact checks and the authenticity score (no network, no GPU)."""

from pricechat_worker import authenticity, editorial

BRIEF = {"headline": "Rocket's Mewtwo PSA 10 sold for $17,225", "subject": "Rocket's Mewtwo", "category": "pokemon_rare",
         "facts": [{"text": "Rocket's Mewtwo (Gym Challenge, 2000) PSA 10 sold for $17,225 on October 12, 2026.", "source": "eBay"}],
         "card": {"name": "Rocket's Mewtwo", "set_name": "Gym Challenge", "year": 2000}, "numbers": {"sale_usd": 17225.0},
         "sources": [{"name": "eBay"}], "images": [{"url": "https://images.example/card.png"}]}
JOB = {"brief": BRIEF, "category": "pokemon_rare", "template": "pokemon_spotlight", "creative": {}, "strategy": {"llm": "none"}}


def asset(i: str, kind: str = "image", match: str = "exact", **kw):
    return {"id": i, "kind": kind, "provider": kw.pop("provider", "commons"), "url": f"https://x/{i}", "file": f"{i}.jpg", "status": "ok", "match": match,
            "width": 1920, "height": 1080, "duration": 10.0 if kind == "video" else None, "license": "CC BY 4.0", "page": f"https://x/p/{i}",
            "attribution": "Photo: A · CC BY 4.0", "permitted": {"attribution": True}, "authentic": True, "shows": f"{i} shows it", **kw}


def test_gate_needs_the_exact_subject_and_enough_real_shots() -> None:
    news = {**JOB, "brief": {**BRIEF, "card": None}}
    assert not editorial.assess([asset("a", match="related"), asset("b", match="related")], news)["ok"]
    gate = editorial.assess([asset("a", match="related"), asset("b", "video", "exact")], news)
    assert not gate["ok"] and "only 1 exact" in gate["problems"][0]
    assert editorial.assess([asset("a", "video", "exact"), asset("b", "video", "related")], news)["ok"]
    assert editorial.assess([asset("c", provider="brief"), asset("d", match="related")], JOB)["ok"]  # a card story: its own scan + context


def test_card_scan_gives_real_details_and_the_style_comes_from_the_story() -> None:
    st = editorial.style_of(JOB)
    assert st["id"] == "collectibles" and st["transition"] == "dissolve"
    shots = editorial.shots([asset("card", provider="brief", width=600, height=825), asset("hall", match="related")], JOB, st)
    assert [s["asset"] for s in shots[:4]] == ["card"] * 4 and len({(tuple(s["focus"]), s["zoom"]) for s in shots[:4]}) == 4
    assert shots[4]["asset"] == "hall" and all(s["fit"] == "frame" for s in shots[:4])


def test_build_puts_the_price_on_the_subject_and_never_steals_a_planned_shot() -> None:
    st = editorial.style_of(JOB)
    assets = {a["id"]: a for a in [asset("card", provider="brief", width=600, height=825), asset("h1", match="related"), asset("h2", match="related"),
                                    asset("h3", match="related")]}
    shots = editorial.shots(list(assets.values()), JOB, st)
    segs = [{"text": "A PSA 10 Rocket's Mewtwo sold for $17,225 on eBay.", "shots": ["s1"], "overlay": "price:sale_usd"},
            {"text": "Context.", "shots": ["s2"], "overlay": "none"}, {"text": "Ending line here.", "shots": ["s6"], "overlay": "none"}]
    words = [{"t": t, "s": 0.3 + i * 0.4, "e": 0.6 + i * 0.4, "p": 0} for i, t in enumerate(segs[0]["text"].split())]
    tl = editorial.build(JOB, st, segs, [(0.2, 4.6), (5.0, 7.0), (7.4, 14.0)], words, shots, assets)
    price = next(o for o in tl["overlays"] if o["type"] == "price")
    assert price["data"]["value"] == "$17,225" and price["data"]["note"].startswith("PSA 10")
    under = [c for c in tl["video"] if c["start"] <= price["start"] + 0.1 < c["start"] + c["dur"] or c["start"] < price["start"] + price["dur"] - 0.2 < c["start"] + c["dur"]]
    assert all(assets[c["asset"]]["match"] == "exact" for c in under)
    planned = [c for c in tl["video"] if c["shot"] == "s6"]
    assert len(planned) == 1 and planned[0]["start"] >= 7.2  # s6 belongs to the last line only
    assert all(abs(a["start"] + a["dur"] - b["start"]) < 1e-6 for a, b in zip(tl["video"], tl["video"][1:]))
    assert not editorial.unsupported(tl, BRIEF)
    tl["overlays"].append({"id": "x", "type": "text", "start": 1, "dur": 2, "data": {"text": "Now worth $25,000"}})
    assert editorial.unsupported(tl, BRIEF) == ["text: 25,000"]


def test_authenticity_flags_filler_repeats_and_soft_pictures() -> None:
    st = editorial.style_of({**JOB, "brief": {**BRIEF, "card": None, "category": "stocks"}})
    good = {"a": asset("a", "video", "exact"), "b": asset("b", "video", "related"), "c": asset("c", match="exact", width=4000, height=3000)}
    tl = {"video": [{"id": f"c{i}", "asset": k, "start": i * 3.0, "dur": 3.0, "fit": "frame", "focus": [0.5, 0.5], "zoom": 1} for i, k in enumerate("abc")],
          "overlays": [], "captions": {"words": [{"t": "Short", "s": 0, "e": 1, "p": 0}]}}
    segs = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    spans = [(0, 3), (3, 6), (6, 9)]
    s1 = authenticity.score(JOB, tl, good, segs, spans, st, use_llm=False)
    assert s1["score"] >= 80 and not s1["flags"]
    bad = {"g": asset("g", match="generic", width=400, height=300)}
    tl2 = {**tl, "video": [{**c, "asset": "g", "fit": "fill", "zoom": 1} for c in tl["video"]]}
    s2 = authenticity.score(JOB, tl2, bad, segs, spans, st, use_llm=False)
    assert s2["score"] < 50 and any("generic" in f for f in s2["flags"]) and any("soft" in f for f in s2["flags"]) and any("identical" in f for f in s2["flags"])


def test_market_structure_chart_passes_through_with_its_basis() -> None:
    """A trading story's market-structure chart (backend marketviz.py) reaches the renderer with its variant, basis, venues
    and window; an ordinary price chart is unchanged."""
    brief = {"chart": {"kind": "market", "variant": "liq", "title": "BTC liquidations reported by OKX", "source": "OKX · Observed · USD · UTC",
                       "points": [[1, 100.0], [2, 99.0]], "bars": [[1, 0, 5000], [2, 2000, 0]], "basis": "observed", "basis_label": "Observed",
                       "venues": ["OKX"], "start": 1, "end": 2, "junk": "dropped"}}
    ch = editorial.chart_of(brief)
    assert ch and ch["kind"] == "market" and ch["variant"] == "liq" and ch["basis"] == "observed" and ch["venues"] == ["OKX"]
    assert ch["bars"] == [[1, 0, 5000], [2, 2000, 0]] and "junk" not in ch
    plain = editorial.chart_of({"chart": {"points": [[1, 1.0], [2, 2.0]], "label": "BTC · 48h"}})
    assert plain and plain["kind"] == "line" and "basis" not in plain


def test_someone_elses_chart_is_never_b_roll() -> None:
    from pricechat_worker import assets

    assert assets.is_chart({"title": "Bitcoin Price.webp", "shows": "a line graph showing the price of Bitcoin over ten years"})
    assert assets.is_chart({"title": "BTC price history chart 2014-2024.png"})
    assert not assets.is_chart({"title": "Bitcoin on Laptop Keyboard.jpg", "shows": "a gold bitcoin coin on a laptop keyboard"})


def test_scripts_dont_ask_the_viewer_about_themselves() -> None:
    from pricechat_worker import script4

    bad = {"segments": [{"text": "Sellers led for two days.", "shots": ["s1"]}, {"text": "Do you use Bitcoin ATMs in your city?", "shots": ["s2"]}]}
    ok = {"segments": [{"text": "Sellers led for two days.", "shots": ["s1"]}, {"text": "Who was on the other side?", "shots": ["s2"]}]}
    assert any("viewer" in p for p in script4.style_problems(bad))
    assert not any("viewer" in p for p in script4.style_problems(ok))
