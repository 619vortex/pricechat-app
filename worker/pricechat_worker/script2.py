"""Script engine v2 (Content Studio v2): story structure, selectable tone, and a storyboard the motion renderer can draw.

Every script has five beats: HOOK (stop the scroll) → CONTEXT (what this is) → DEVELOPMENT (the interesting turn) →
PAYOFF (the line people repeat) → ENDING (a natural close + the comment question). Each segment also names the VISUAL
that carries it, chosen only from visuals the verified brief can support (no card image = no card scene, no verified
price history = no chart). The fact rules are the same as v1, and the backend checks every script the same way."""

import json
import os
import re
import time
from typing import Any

import httpx

from pricechat_worker import creative, llm

WORDS_PER_SECOND = {"slow": 2.35, "normal": 2.6, "fast": 2.9}
SEGMENTS = {"slow": 6, "normal": 6, "fast": 7}

# the visuals each story kind can use, and what each one needs from the brief
VISUALS = {
    "price_reveal": "the verified price counts up huge on screen (a sale or a price)",
    "card_entrance": "the card itself flies in and turns in 3D",
    "card_detail": "close-up of the card: its name, set, year and grade",
    "evidence": "the sale record: price, grade, date, marketplace (from the brief only)",
    "chart": "the verified price history drawn as a line",
    "headline": "the news headline with its source",
    "stat": "one big verified number with a label",
    "coin": "the coin's logo spinning in 3D with its live price",
    "photo": "a full-screen real photo (licensed) with the screen words over it",
    "punchline": "the screen words as big kinetic type (any segment can use this)",
    "list": "a numbered countdown item",
}
EXTRA_BANNED = ("let that sink in", "here's the thing", "but wait", "you won't believe", "and that's not all", "believe it or not",
                "mind-blowing", "jaw-dropping", "absolutely insane", "it's official", "in a world where", "ladies and gentlemen",
                "drumroll", "spoiler alert", "plot twist")


def available_visuals(job: dict[str, Any]) -> list[str]:
    brief, t = job.get("brief") or {}, job.get("template")
    nums = brief.get("numbers") or {}
    has_img = bool((brief.get("images") or [{}])[0].get("url")) if brief.get("images") else False
    has_card = bool(brief.get("card"))
    has_chart = len(((brief.get("chart") or {}).get("points") or [])) >= 2
    price = any(k in nums for k in ("sale_usd", "price_usd", "market_price_usd", "last_sale_usd"))
    out = ["punchline"]
    if t == "pokemon_spotlight" or has_card:
        if price:
            out.append("price_reveal")
        if has_img:
            out += ["card_entrance", "card_detail"]
        if brief.get("sources") and price:
            out.append("evidence")
    if t in ("price_alert", "breaking_news") or brief.get("coin"):
        if price or "change_24h_pct" in nums:
            out.append("stat")
        out.append("headline")
    if isinstance((brief.get("stat") or {}).get("value"), (int, float)):
        out.append("stat")
    if t == "top_facts":
        out.append("list")
    if has_chart:
        out.append("chart")
    if t == "youtube_highlight":
        out.append("headline")
    md = job.get("media") or {}  # the worker's live data and licensed photos (media.py)
    if md.get("coin"):  # labelled live scenes only: a live number under the script's own words could read as its claim
        out += ["coin", "chart"]
    if md.get("photos"):
        out.append("photo")
    return sorted(set(out), key=list(VISUALS).index)


def _prompt(job: dict[str, Any], feedback: list[str], cr: dict[str, Any]) -> tuple[str, str]:
    b, st, brief = job["brand"], job["strategy"], job["brief"]
    pace = cr.get("pace") or "normal"
    secs = cr.get("seconds")
    lo, hi = (int(secs) - 4, int(secs) + 4) if secs else (int(st["min_seconds"]), int(st["max_seconds"]))
    words = int((lo + hi) / 2 * WORDS_PER_SECOND[pace])
    n = SEGMENTS[pace]
    visuals = available_visuals(job)
    cta = b.get("cta") if b.get("cta") and st.get("outro_cta") else ""
    system = (
        f"You write short vertical videos for {b['name']}, a {b['personality']} brand. Audience: {b['audience']}. Language: {b['language']}.\n"
        "You are a senior short-form story editor: your scripts sound like a sharp person talking, never like an AI summary.\n\n"
        "FACT RULES (mandatory; the script is rejected otherwise):\n"
        "- Use ONLY the facts in the brief. Never add a number, price, date, name, quote, comparison or claim the brief doesn't give.\n"
        "- Say each number the way the brief gives it ($17,225 stays $17,225; 3.65 percent stays 3.65 percent). You may round a "
        "price in a spoken reaction only if the exact number is also said.\n"
        "- Never call something rare, a record, the most/biggest/first, historic or one of a few unless the brief says exactly that.\n"
        "- No predictions, no advice, no hype words (guaranteed, to the moon, buy now). Attribute news to its source.\n"
        "- Opinions and reactions are welcome and are how the voice gets personality: they must be clearly opinions.\n"
        "- Never mention the brief, the research, or missing information.\n\n"
        "STRUCTURE (one segment per beat, in this order; use extra segments for a second development beat):\n"
        "1. HOOK: the first sentence stops the scroll in under 2 seconds (max 12 words). Lead with the most surprising true thing, "
        "said with an angle. Good angles: a contrast between the object and the money ('Someone just paid [PRICE] for a piece of "
        "cardboard.'), an understatement, the viewer's reaction said out loud. No invented comparisons (a car, rent, a salary). FORBIDDEN "
        "openers: a bare question about the number ('$X for a card?', 'Seventeen thousand dollars for a Pokémon card?'), the brand "
        "name, a greeting, 'Did you know', 'Imagine'.\n"
        "2. CONTEXT: what exactly this is, in plain words (the item, the coin, the event).\n"
        "3. DEVELOPMENT: the detail that makes it interesting (the grade, the source, the move, what the term means).\n"
        "4. PAYOFF: the memorable line: the fact that lands, or the observation people will repeat.\n"
        f"5. ENDING: a natural close in one short line{(' then: ' + cta) if cta else ''}, and one comment question about the "
        "viewer's taste or experience (never about future prices or what to buy).\n\n"
        f"TONE: {creative.TONES[cr['tone']]}\n"
        "CRAFT:\n"
        "- Each segment's text is 1-2 spoken sentences (6-24 words). Vary sentence length; contractions; plain words.\n"
        "- You may put [beat] in the text where a short dramatic pause helps (at most twice in the whole script).\n"
        "- \"screen\" is 2-5 punchy words shown huge for that segment: the payoff of what's said, not a copy of the sentence. "
        "A number on screen is copied exactly from that segment's text.\n"
        "- \"emphasis\": 1-2 words from that segment's text that the captions should highlight (numbers, names, the twist word).\n"
        f"- \"visual\": pick the visual that best carries each segment, ONLY from this list: {', '.join(visuals)}. Use each "
        "visual at most twice, and don't use the same visual on two segments in a row.\n"
        "  " + "; ".join(f"{v} = {VISUALS[v]}" for v in visuals) + ".\n"
        f"- About {words} words in total ({lo}-{hi} seconds spoken).\n"
        f"- Never use these phrases: {', '.join(llm.BANNED_PHRASES + EXTRA_BANNED)}. Don't reuse a joke or opener formula from the "
        "examples: adapt to this story.\n"
    )
    system += (
        "\nHOW A GOOD SCRIPT FLOWS (a made-up placeholder story, only to show rhythm; never use its words or facts):\n"
        "HOOK: \"Someone just paid [PRICE] for one piece of cardboard.\" CONTEXT: \"It's the [SET] [CARD], graded [GRADE].\" "
        "DEVELOPMENT: \"[GRADE] means [WHAT THE BRIEF SAYS IT MEANS]. [beat] That's the part collectors chase.\" PAYOFF: \"Final price: "
        "[PRICE], on [MARKETPLACE].\" ENDING: \"Cardboard, but make it [PRICE]. Would you have paid it?\"\n"
        "Notice: every number in it comes from the story; the personality is in how it's said.\n"
    )
    if b.get("examples"):
        system += "\nApproved examples of the brand's tone (match the voice, not the words):\n" + "\n".join(f"- {e[:300]}" for e in b["examples"])
    if (job.get("learned") or {}).get("top_hooks"):
        system += "\nHooks that performed well before (match the energy, never reuse the words):\n" + "\n".join(f"- {h}" for h in job["learned"]["top_hooks"])
    tags = job.get("suggested_hashtags") or []
    beats = ["hook", "context", "development", "payoff"] + ["development"] * max(0, n - 5) + ["ending"]
    user = (
        f"Topic: {job['topic']}\n"
        f"Brief (the only facts you may use):\n{json.dumps({k: brief.get(k) for k in ('headline', 'facts', 'numbers', 'card', 'coin', 'sources', 'note')}, ensure_ascii=False)}\n\n"
        "Return JSON only, exactly this shape:\n"
        '{"hook": "the hook sentence", "segments": ['
        + ", ".join(f'{{"beat": "{bt}", "visual": "...", "text": "spoken words", "screen": "2-5 words", "emphasis": ["word"]}}' for bt in beats)
        + '], "title": "max 70 chars, curiosity + the key fact, no clickbait lies", '
        '"description": "1 sentence that adds context and cites the source, then the comment question", '
        '"hashtags": ["#two", "#specific"]}\n'
        f"Exactly {len(beats)} segments. The first segment's text starts with the hook. "
        "hashtags: 2 lowercase tags about this exact topic, no generic ones" + (f" (these are added anyway: {' '.join(tags)})" if tags else "") + "."
    )
    words = [str(w) for w in job.get("trends") or [] if str(w).strip()][:3]
    if words:  # the backend's trend scan: words many finance headlines use today that are already in this story
        user += ("\n\nTRENDING NOW (in this story, and in many finance headlines today): " + ", ".join(words)
                 + ". Use one in the hook, title or description if it fits naturally. It adds no facts.")
    if feedback:
        user += "\n\nYour previous script was rejected. Fix ALL of these:\n" + "\n".join(f"- {f}" for f in feedback)
    return system, user


def _parse(text: str, visuals: list[str]) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    data = json.loads(m.group(0))
    segs = []
    for s in data.get("segments") or []:
        if not isinstance(s, dict) or not str(s.get("text", "")).strip():
            continue
        v = str(s.get("visual") or "punchline")
        segs.append({"beat": str(s.get("beat") or ""), "visual": v if v in visuals else "punchline", "text": str(s["text"]).replace("*", "").strip(),
                     "screen": str(s.get("screen") or "").replace("*", "").strip(), "emphasis": [str(w) for w in (s.get("emphasis") or [])][:3]})
    if len(segs) < 3:
        raise ValueError("too few segments")
    return {"hook": str(data.get("hook") or segs[0]["text"]).strip(), "segments": segs, "title": str(data.get("title") or "").strip(),
            "description": str(data.get("description") or "").strip(), "hashtags": [str(h) for h in data.get("hashtags") or []][:8]}


def think_option() -> dict[str, Any]:
    """PCW_OLLAMA_THINK=0/1 turns a thinking model's reasoning off/on (unset: the model's own default)."""
    v = os.environ.get("PCW_OLLAMA_THINK", "").strip()
    return {"think": v == "1"} if v in ("0", "1") else {}


def _call(engine: str, system: str, user: str, temperature: float, json_mode: bool = True) -> str:
    """json_mode=False: no Ollama JSON grammar (it sometimes aborts a long answer with a 500); the JSON is parsed from the text."""
    kind, _, model = engine.partition(":")
    if kind == "ollama":
        r = httpx.post(f"{os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/api/chat", timeout=300.0, json={
            "model": model, "stream": False, **({"format": "json"} if json_mode else {}), **think_option(),
            "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": 1600},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return str(r.json()["message"]["content"])
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120.0, headers={
        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": model, "max_tokens": 1800, "temperature": temperature, "system": system, "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content") or [])


def _engines(job: dict[str, Any]) -> list[str]:
    st = job["strategy"]
    pref = st.get("llm") or "auto"
    out = []
    if pref in ("auto", "ollama"):
        out.append(f"ollama:{os.environ.get('OLLAMA_MODEL') or st.get('ollama_model') or 'gemma4:31b'}")
    if os.environ.get("ANTHROPIC_API_KEY") and pref in ("auto", "anthropic"):
        out.append(f"anthropic:{os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-5-5')}")
    return out


def write(job: dict[str, Any], feedback: list[str], attempt: int, cr: dict[str, Any]) -> dict[str, Any]:
    """One script from the best available engine; the 6th attempt (or no AI) is the facts-only template script."""
    if attempt >= 6 or (job["strategy"].get("llm") or "auto") == "template":
        return template_script(job, cr)
    system, user = _prompt(job, feedback, cr)
    visuals = available_visuals(job)
    temp = {"funny": 0.85, "dramatic": 0.8}.get(cr["tone"], 0.72) - (0.15 if feedback else 0.0)  # fixes: less invention
    errors = []
    for engine in _engines(job):
        hook = workshop_hook(job, cr, engine)
        if hook:
            user_h = user + f"\n\nThe HOOK is already written. The first segment's text must START with exactly this sentence: \"{hook}\""
        for k in range(2):  # one retry without the JSON grammar: a local model occasionally fails or breaks the JSON
            try:
                if hook:
                    return {**_parse(_call(engine, system, user_h, temp, k == 0), visuals), "engine": engine, "hook_workshop": hook}
                return {**_parse(_call(engine, system, user, temp, k == 0), visuals), "engine": engine}
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                errors.append(f"{engine}: {type(exc).__name__}: {str(exc)[:120]}")
                time.sleep(3)
    return {**template_script(job, cr), "engine_errors": errors}


ANGLES = ("a contrast between the object and the money, said flatly", "an understatement", "the viewer's own reaction said out loud",
          "a vivid reframe of what the item physically is", "a deadpan observation about the situation")


def _numbers_ok(text: str, brief: dict[str, Any]) -> bool:
    from pricechat_worker.storyboard import brief_numbers

    allowed = brief_numbers(brief)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    words = re.findall(r"\b(thousand|million|billion|grand|hundred)\b", text, re.I)
    return all(any(abs(n - a) < 0.01 for a in allowed) for n in nums) and not (words and not nums and any(w.lower() == "grand" for w in words))


def workshop_hook(job: dict[str, Any], cr: dict[str, Any], engine: str) -> str | None:
    """Five hooks from five different angles; code drops the formulaic ones and any with a number not in the brief; the
    model then picks the strongest for this tone. None when nothing survives (the main prompt writes its own)."""
    brief = job.get("brief") or {}
    system = ("You write the first sentence of short vertical videos: the line that stops the scroll. Max 12 words, a statement "
              "(not a question), true to the facts given, no hype words, no brand names, no 'Did you know' / 'Imagine'. "
              "Use only numbers from the facts, written exactly as given ($17,225 stays $17,225). Answer JSON only.")
    facts_txt = json.dumps({k: brief.get(k) for k in ("headline", "facts", "numbers", "card", "coin")}, ensure_ascii=False)
    user = (f"Facts: {facts_txt}\nTone: {creative.TONES[cr['tone']]}\n"
            f"Write one hook for each angle: {'; '.join(ANGLES)}.\nReturn {{\"hooks\": [\"...\", \"...\", \"...\", \"...\", \"...\"]}}")
    try:
        data = json.loads(re.search(r"\{.*\}", _call(engine, system, user, 0.9), re.S).group(0))  # type: ignore[union-attr]
    except (httpx.HTTPError, ValueError, AttributeError, KeyError):
        return None
    good = []
    for h in data.get("hooks") or []:
        h = str(h).replace("*", "").strip().strip('"')
        probe = {"segments": [{"text": h}]}
        if not h or len(h.split()) > 13 or h.endswith("?") or style_problems(probe) or not _numbers_ok(h, brief) or claim_problems(probe, brief):
            continue
        item = str((brief.get("card") or brief.get("coin") or {}).get("name") or "").lower().split()
        if not ("$" in h or re.search(r"\b(card|cardboard|coin|token|bitcoin|crypto|market)\b", h, re.I) or any(w in h.lower() for w in item if len(w) > 3)):
            continue  # a hook names the money or the thing; "I didn't expect that." alone is vague clickbait
        if re.match(r"^\s*(seventeen|\$?\d)", h, re.I) and re.search(r"\bfor an?\b", h, re.I) and len(h.split()) <= 8:
            continue  # "$X for a card." is the cliché in statement form
        good.append(h)
    if not good:
        return None
    if len(good) == 1:
        return good[0]
    pick = ("Pick the strongest hook for this tone (" + cr["tone"] + "): the one a viewer would least expect and most want explained. "
            "Return {\"best\": <index from 0>}.\n" + "\n".join(f"{i}: {h}" for i, h in enumerate(good)))
    try:
        best = int(json.loads(re.search(r"\{.*\}", _call(engine, "Answer JSON only.", pick, 0.0), re.S).group(0))["best"])  # type: ignore[union-attr]
        return good[best] if 0 <= best < len(good) else good[0]
    except (httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError):
        return good[0]


def template_script(job: dict[str, Any], cr: dict[str, Any]) -> dict[str, Any]:
    """No AI: the v1 facts-only script, given v2 beats and visuals so it still renders as a full storyboard."""
    base = llm.template_script(job)
    visuals = available_visuals(job)
    order = [v for v in ("price_reveal", "card_entrance", "card_detail", "evidence", "headline", "stat", "chart", "list") if v in visuals]
    segs = []
    for k, s in enumerate(base["segments"]):
        last = k == len(base["segments"]) - 1
        v = "punchline" if last or not order else order[min(k, len(order) - 1)]
        # data scenes show the verified data themselves; kinetic text gets a few words, never a whole sentence
        raw = str(s.get("screen") or "")
        screen = raw if v == "punchline" and len(raw.split()) <= 5 else ""
        segs.append({"beat": "hook" if k == 0 else "ending" if last else "development", "visual": v, "text": s["text"],
                     "screen": screen or ("Would you pay that?" if last and "?" in s["text"] else ""), "emphasis": []})
    return {**base, "segments": segs, "engine": "template"}


def style_problems(script: dict[str, Any]) -> list[str]:
    out = llm.style_problems(script)
    segs = script.get("segments") or []
    low = " ".join(str(s.get("text", "")) for s in segs).lower()
    used = [ph for ph in EXTRA_BANNED if ph in low]
    if used:
        out.append(f"Remove these clichés: {', '.join(used)}.")
    first = re.split(r"(?<=[.!?])\s+", str(segs[0].get("text", "")).strip())[0] if segs else ""
    numberish = r"(\$?[\d,.]+|(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
    numberish += r"sixty|seventy|eighty|ninety|hundred|seventeen|thousand|million|billion)\b)"
    if first.endswith("?") and re.search(numberish, first, re.I) and len(first.split()) <= 10:
        out.append("Don't open with a bare rhetorical question about the number (\"$X for a card?\"): give the hook an angle, e.g. a "
                   "contrast between the object and the money, said as a statement.")
    last = str(segs[-1].get("text", "")) if segs else ""
    for q in re.findall(r"[^.!?]*\?", last):
        if re.search(r"\b(invest\w*|buy|sell|worth it|go up|go down|future|price will|good deal|profit)\b", q, re.I):
            out.append(f"The comment question asks about money or investing (“{q.strip()}”): ask about the viewer's taste or "
                       "experience instead, e.g. 'Would you have paid it?' or 'Which card would you frame?'.")
            break
    if re.match(r"^\s*(imagine|picture this|did you know)\b", first, re.I):
        out.append("Don't open with 'Imagine' / 'Picture this' / 'Did you know': start with the fact itself, with an angle.")
    vis = [s.get("visual") for s in segs]
    if any(a == b and a != "punchline" for a, b in zip(vis, vis[1:])):
        out.append("Don't use the same visual on two segments in a row.")
    if low.count("[beat]") > 2:
        out.append("Use [beat] at most twice.")
    return out


# claims a local model likes to add that research briefs almost never support (each is allowed when the brief says it)
CLAIMS = (r"\bpopular\b", r"\binvest(ment|ing|or)?s?\b", r"\btop price\b", r"\bprice went up\b", r"\bdrives? the (value|price)\b",
          r"\bin (high )?demand\b", r"\bhighly sought\b", r"\bsought[- ]after\b", r"\bskyrocket\w*\b", r"\bvaluable\b", r"\brising\b",
          r"\bpremium\b", r"\bholy grail\b", r"\bevery collector\b", r"\ball collectors\b", r"\bmost wanted\b", r"\bbest condition possible\b",
          r"\bflawless\b", r"\bpristine\b", r"\bnear[- ]perfect\b", r"\bperfect(ion)?\b",
          r"\buntouched\b", r"\bno (flaws|damage|scratches)\b", r"\breflected in the price\b", r"\b(tough|hard|difficult) (grade )?to get\b",
          r"\brare\b", r"\brarity\b", r"\bscarce\b", r"\bonly a (few|handful)\b", r"\brecord\b", r"\bhighest[- ]ever\b",
          r"\bthat.s why the price\b", r"\bbecause of (the|its) grade\b")


def claim_problems(script: dict[str, Any], brief: dict[str, Any]) -> list[str]:
    research = json.dumps({k: brief.get(k) for k in ("headline", "facts", "note")}, ensure_ascii=False).lower()
    text = " ".join([*(str(s.get("text", "")) for s in script.get("segments") or []), str(script.get("title") or ""),
                     str(script.get("description") or "")])
    found = sorted({m.group(0) for pat in CLAIMS for m in re.finditer(pat, text, re.I) if not re.search(pat, research, re.I)})
    out = []
    if found:
        out.append(f"Remove claims the research doesn't make: {', '.join(found)}. Keep reactions clearly personal ('that's a lot for one "
                   "card') and don't describe value, demand, popularity, condition or investing.")
    wrong = spoken_amounts_off(text, brief)
    if wrong:
        out.append(f"These spoken amounts don't match the research: {', '.join(wrong)}. Say the exact number from the brief "
                   "(a rounding like 'seventeen grand' is fine only within 3%).")
    return out


_NUMW = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                     "sixteen seventeen eighteen nineteen".split())}
_NUMW.update({w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())})
_AMOUNT = re.compile(r"\b((?:(?:" + "|".join(_NUMW) + r"|hundred)[\s-]*)+)(grand|thousand|million)\b", re.I)


def spoken_amounts_off(text: str, brief: dict[str, Any]) -> list[str]:
    """'nearly twenty grand' for a $17,225 sale: a spelled-out amount more than 3% away from every number in the brief."""
    from pricechat_worker.storyboard import brief_numbers

    allowed = [a for a in brief_numbers(brief) if a >= 100]
    bad = []
    for m in _AMOUNT.finditer(text):
        cur = 0
        for w in re.split(r"[\s-]+", m.group(1).lower().strip()):
            if w == "hundred":
                cur = max(cur, 1) * 100
            elif w in _NUMW:
                cur += _NUMW[w]
        val = cur * (1_000_000 if m.group(2).lower() == "million" else 1000)
        if val and not any(abs(val - a) <= a * 0.03 for a in allowed):
            bad.append(m.group(0).strip())
    return bad


def finish(script: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """The fields the backend checks (voiceover without pause marks, storyboard, sources). `scene` = the visual."""
    segs = [{**s, "scene": s.get("visual") or "punchline"} for s in script["segments"]]
    clean = lambda t: re.sub(r"\s*\[beat\]\s*", " ", t).strip()  # noqa: E731
    return {**script, "segments": segs, "voiceover": " ".join(clean(s["text"]) for s in segs),
            "storyboard": [{"scene": s["scene"], "text": llm.short(str(s.get("screen") or ""), 80)} for s in segs],
            "sources": job["brief"].get("sources") or []}
