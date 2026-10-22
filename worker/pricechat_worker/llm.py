"""Script writing. Engines, in order (strategy.llm = auto): the local Ollama on this machine (DGX Spark), Anthropic
(cloud fallback, only if ANTHROPIC_API_KEY is set on the worker), then a deterministic template script built only from
the brief's facts (no AI, always valid). The backend checks every script (no invented numbers, no financial claims)
and the worker rewrites with that feedback."""

import json
import os
import re
from typing import Any

import httpx

WORDS_PER_SECOND = 2.6

SCENES_BY_TEMPLATE = {
    "breaking_news": ["hook", "facts", "facts", "source", "outro"],
    "price_alert": ["hook", "chart", "facts", "outro"],
    "pokemon_spotlight": ["hook", "card", "facts", "outro"],
    "top_facts": ["hook", "list", "list", "list", "list", "list", "outro"],
    "youtube_highlight": ["hook", "thumbnail", "facts", "outro"],
}


# How each template tells its story (the angle, not the facts: those come only from the brief).
ANGLES = {
    "breaking_news": "News, fast. Lead with what happened in plain words, then who reported it, then the one detail that makes it matter. "
                     "Attribute it ('according to CoinDesk'). No guessing what happens next.",
    "price_alert": "A market move. Lead with the move itself (the coin and the percent or the price, exactly as the brief gives it), "
                   "then put it in context with the brief's other numbers (7-day change, volume, rank). Describe, never predict.",
    "pokemon_spotlight": "A collector story. Lead with the price or the move, then make the viewer picture the card (name, set, year, grade), "
                         "then the detail collectors will repeat (population, last sale, grade meaning).",
    "top_facts": "A countdown of surprising facts. Each list segment is one fact, told like a reveal. Save the most surprising for last.",
    "youtube_highlight": "A teaser for our own YouTube video: what it's about and why it's worth watching. Point to the full video at the end.",
}
BANNED_PHRASES = ("in this video", "let's dive in", "dive into", "buckle up", "game-changer", "game changer", "hey guys", "folks",
                  "without further ado", "stay tuned", "smash that", "in today's video", "make sure to",
                  "the brief", "the notes", "no info", "no information", "the research")


def _prompt(job: dict[str, Any], feedback: list[str]) -> tuple[str, str]:
    b, st, brief = job["brand"], job["strategy"], job["brief"]
    lo, hi = st["min_seconds"], st["max_seconds"]
    words = int((lo + hi) / 2 * WORDS_PER_SECOND)
    scenes = SCENES_BY_TEMPLATE[job["template"]]
    cta = b.get("cta") if b.get("cta") and st.get("outro_cta") else ""
    system = (
        f"You are the head writer of a fast-growing TikTok account, {b['name']}. Brand voice: {b['personality']}. Audience: {b['audience']}. "
        f"Language: {b['language']}.\n"
        "Your scripts keep people watching: they hook in the first second, move fast, and end with something people want to comment on.\n\n"
        "FACT RULES (mandatory; the script is rejected otherwise):\n"
        "- Use ONLY the facts in the brief. Never add a number, price, date, name, quote or claim that is not in the brief, and don't "
        "explain background the brief doesn't give.\n"
        "- Say each number the way the brief gives it (3.65 percent stays 3.65 percent; $150,000 stays $150,000).\n"
        "- No predictions, no advice, no hype (guaranteed, to the moon, buy now, will hit...). Attribute news to its source.\n"
        "- No claims the brief doesn't make: never call something rare, a record, the most expensive, the biggest, one of only a few, "
        "historic or the first unless the brief says exactly that. Don't say 'just' or 'today' about something the brief dates in the "
        "past. Never mention the research, the brief or what information is missing: just tell what is known.\n"
        "- Reactions are welcome ('That's a lot of money for one card.'): they're opinions, not facts.\n\n"
        "CRAFT:\n"
        "- The HOOK is the first segment's first sentence: at most 12 words, and it stops the scroll. Open with the most surprising "
        "fact or number in the brief, a sharp contrast, or a question the video answers. Never open with the brand name, a greeting "
        "or 'Did you know'.\n"
        "- One idea per segment. Each segment's text is 1-2 short spoken sentences (8-22 words). Talk like a sharp friend: "
        "contractions, plain words, rhythm. Vary sentence length. No emojis, no hashtags in the voiceover.\n"
        "- Build curiosity: each segment should make the viewer want the next one ('But here's the part nobody mentions.').\n"
        "- \"screen\" is 2-5 punchy words shown huge on screen for that segment: the payoff of what's said, not a repeat of the "
        "sentence. A number on screen is copied exactly from that segment's text.\n"
        f"- The last segment lands the story in one line{(' and then says: ' + cta) if cta else ''}. Before that, ask viewers one "
        "short question they'll want to answer in the comments: about their own taste or experience ('Would you pay that?', "
        "'Which one surprised you?'), never about future prices, outlook or what to buy.\n"
        f"- About {words} words in total ({lo}-{hi} seconds spoken).\n"
        f"- Never use these phrases: {', '.join(BANNED_PHRASES)}. Never copy other creators' wording.\n"
        f"\nANGLE for this video: {ANGLES.get(job['template'], '')}"
    )
    if b.get("examples"):
        system += "\nApproved examples of the brand's tone:\n" + "\n".join(f"- {e[:300]}" for e in b["examples"])
    if (job.get("learned") or {}).get("top_hooks"):
        system += "\nHooks that performed well before (match the energy, don't reuse the words):\n" + "\n".join(f"- {h}" for h in job["learned"]["top_hooks"])
    tags = job.get("suggested_hashtags") or []
    user = (
        f"Topic: {job['topic']}\nTemplate: {job['template_style']['label']}\n"
        f"Brief (the only facts you may use):\n{json.dumps({k: brief.get(k) for k in ('headline', 'facts', 'numbers', 'sources', 'note')}, ensure_ascii=False)}\n\n"
        "Return JSON only, exactly this shape:\n"
        '{"hook": "the hook sentence", "segments": [' + ", ".join(f'{{"scene": "{sc}", "text": "spoken words", "screen": "2-5 words"}}' for sc in scenes)
        + '], "title": "max 70 chars, curiosity + the key fact, no clickbait lies", '
        '"description": "1 sentence that adds context and cites the source, then the comment question", '
        '"hashtags": ["#two", "#specific"]}\n'
        f"Exactly {len(scenes)} segments, in that order. The first segment's text starts with the hook. "
        "hashtags: 2 lowercase tags about this exact topic (the coin, the card, the event), no generic ones"
        + (f" (these are added anyway: {' '.join(tags)})" if tags else "") + "."
    )
    if feedback:
        user += "\n\nYour previous script was rejected. Fix ALL of these:\n" + "\n".join(f"- {f}" for f in feedback)
    return system, user


def _parse(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    data = json.loads(m.group(0))
    segs = [s for s in data.get("segments") or [] if isinstance(s, dict) and str(s.get("text", "")).strip()]
    if not segs:
        raise ValueError("no segments")
    return {"hook": str(data.get("hook") or segs[0]["text"]).strip(), "segments": segs, "title": str(data.get("title") or "").strip(),
            "description": str(data.get("description") or "").strip(), "hashtags": [str(h) for h in data.get("hashtags") or []][:8]}


def ollama(job: dict[str, Any], feedback: list[str], base: str, model: str) -> dict[str, Any]:
    system, user = _prompt(job, feedback)
    r = httpx.post(f"{base.rstrip('/')}/api/chat", timeout=300.0, json={
        "model": model, "stream": False, "format": "json", "options": {"temperature": 0.75, "num_ctx": 8192},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return _parse(r.json()["message"]["content"])


def anthropic(job: dict[str, Any], feedback: list[str], key: str, model: str) -> dict[str, Any]:
    system, user = _prompt(job, feedback)
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120.0, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": model, "max_tokens": 1500, "system": system, "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return _parse("".join(b.get("text", "") for b in r.json().get("content") or []))


def short(text: str, n: int) -> str:
    """At most n characters, cut at a word boundary (never in the middle of a number)."""
    text = text.strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def _think() -> dict[str, Any]:
    v = os.environ.get("PCW_OLLAMA_THINK", "").strip()
    return {"think": v == "1"} if v in ("0", "1") else {}


def _ask(engine: str, system: str, user: str) -> str:
    """One JSON answer from the engine that wrote the script ("ollama:<model>" or "anthropic:<model>")."""
    kind, _, model = engine.partition(":")
    if kind == "ollama":
        r = httpx.post(f"{os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/api/chat", timeout=180.0, json={
            "model": model, "stream": False, "format": "json", **_think(), "options": {"temperature": 0.0, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return str(r.json()["message"]["content"])
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120.0, headers={
        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": model, "max_tokens": 800, "system": system, "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content") or [])


def fact_check(job: dict[str, Any], script: dict[str, Any]) -> list[str]:
    """A second, strict read by the AI: every sentence of the voiceover, title and description against the brief. Returns
    the rewrite feedback for sentences that claim something the brief doesn't say (an empty list = clean, or the check
    couldn't run: the backend's own number and safety checks still apply)."""
    engine = str(script.get("engine") or "")
    if not engine.startswith(("ollama:", "anthropic:")):
        return []
    brief = job["brief"]
    # each fact WITH its source: "sold on eBay" is supported when eBay is where the sale record comes from
    research = "\n".join(f"- {f.get('text')} (source: {f.get('source')})" if isinstance(f, dict) and f.get("source") else f"- {f.get('text') if isinstance(f, dict) else f}"
                         for f in brief.get("facts") or [])
    srcs = ", ".join(str(s.get("name")) for s in brief.get("sources") or [] if s.get("name"))
    if srcs:
        research += f"\n- The story's source(s): {srcs}. Saying where the sale or the data comes from ('on eBay', 'according to CoinGecko') is supported."
    system = ("You are a strict fact-checker for a news video. You compare a script with its research notes. A sentence is UNSUPPORTED "
              "when it states a FACT the notes don't say: a number, date, rarity or scarcity, ranking, record, cause, effect, or what "
              "happened next. These are FINE and must not be flagged: reactions and opinions ('that's a lot of money for one card', "
              "'wild', 'seriously'), questions to the viewer, the call to action, describing what the card or chart looks like, and "
              "rewording of the notes. Answer JSON only.")
    text = [*(str(s.get("text", "")) for s in script.get("segments") or []), str(script.get("title") or ""), str(script.get("description") or "")]
    user = (f"Research notes (headline: {brief.get('headline') or ''}):\n{research}\n\nScript sentences:\n"
            + "\n".join(f"- {t}" for t in text if t.strip())
            + '\n\nReturn {"unsupported": [{"quote": "the exact sentence", "why": "what the notes don\'t say"}]} (an empty list if all are supported).')
    try:
        data = json.loads(re.search(r"\{.*\}", _ask(engine, system, user), re.S).group(0))  # type: ignore[union-attr]
    except (httpx.HTTPError, ValueError, AttributeError, KeyError):
        return []
    out = []
    for item in (data.get("unsupported") or [])[:4]:
        if isinstance(item, dict) and str(item.get("quote") or "").strip():
            out.append(f"Unsupported claim, remove or rephrase it to say only what the brief says: “{str(item['quote'])[:160]}” "
                       f"({str(item.get('why') or 'not in the brief')[:120]}).")
    return out


QUESTIONS = {"breaking_news": "What do you make of it? Tell me in the comments.", "price_alert": "Were you watching this move? Tell me below.",
             "pokemon_spotlight": "Would you ever pay that for one card?", "top_facts": "Which one surprised you most?",
             "youtube_highlight": "Have you seen the full video yet?"}


def _money(v: Any) -> str:
    v = float(v)
    return f"${v:,.2f}" if v < 100 else f"${v:,.0f}"


def _template_hook(t: str, brief: dict[str, Any], headline: str) -> str:
    nums, card, coin = brief.get("numbers") or {}, brief.get("card") or {}, brief.get("coin") or {}
    if t == "pokemon_spotlight" and nums.get("sale_usd") and card.get("name"):
        return f"{_money(nums['sale_usd'])}. For one {card['name']} card."
    if t == "pokemon_spotlight" and nums.get("change_30d_pct") and card.get("name"):
        return f"{card['name']} is up {nums['change_30d_pct']} percent in 30 days."
    if t == "price_alert" and nums.get("change_24h_pct") is not None and coin.get("name"):
        ch = float(nums["change_24h_pct"])
        return f"{coin['name']} just moved {abs(ch):.2f} percent {'up' if ch >= 0 else 'down'} in a day."
    return {"breaking_news": f"Big crypto news: {headline}.", "price_alert": f"{headline}. Here's what the numbers say.",
            "pokemon_spotlight": f"{headline}. Collectors, look at this.", "top_facts": "Five facts most people never hear about.",
            "youtube_highlight": f"From our channel: {headline}."}.get(t, headline)


def template_script(job: dict[str, Any]) -> dict[str, Any]:
    """No AI: a clean script assembled from the brief's own sentences (every number is the brief's)."""
    brief, b, st = job["brief"], job["brand"], job["strategy"]
    facts = [str(f["text"] if isinstance(f, dict) else f).strip() for f in brief.get("facts") or []]
    src = (brief.get("sources") or [{}])[0].get("name", "")
    t = job["template"]
    headline = brief.get("headline") or job["topic"]
    hook = _template_hook(t, brief, headline)
    scenes = SCENES_BY_TEMPLATE[t]
    segs: list[dict[str, str]] = [{"scene": "hook", "text": hook, "screen": short(headline, 60)}]
    body = [s for s in scenes[1:] if s != "outro"]
    for i, scene in enumerate(body):
        text = facts[i] if i < len(facts) else (f"That's according to {src}." if src else "")
        if scene == "source" and src:
            text = f"That's according to {src}."
        if text:
            segs.append({"scene": scene, "text": text, "screen": short(text, 70)})
    ending = QUESTIONS.get(t, "")
    if st.get("outro_cta") and b.get("cta"):
        ending = f"{ending} {b['cta']}".strip()
    segs.append({"scene": "outro", "text": ending or "That's the update.", "screen": b["name"]})
    return {"hook": hook, "segments": segs, "title": short(headline, 80),
            "description": (f"{headline}. Source: {src}. " if src else f"{headline}. ") + QUESTIONS.get(t, ""), "hashtags": [], "engine": "template"}


def style_problems(script: dict[str, Any]) -> list[str]:
    """What makes a script weak on TikTok (the backend checks facts and safety; this checks craft)."""
    out = []
    segs = script.get("segments") or []
    first = re.split(r"(?<=[.!?])\s+", str(segs[0].get("text") if segs else "").strip())[0]
    if len(first.split()) > 14:
        out.append(f"The hook (first sentence) has {len(first.split())} words: cut it to 12 or fewer and lead with the surprising part.")
    low = " ".join(str(s.get("text", "")) for s in segs).lower()
    used = [ph for ph in BANNED_PHRASES if ph in low]
    if used:
        out.append(f"Remove these filler phrases: {', '.join(used)}.")
    if low.lstrip().startswith(("did you know", "hey", "hi ", "welcome")):
        out.append("Don't open with 'Did you know', a greeting or a welcome: open with the fact itself.")
    long_screens = [str(s.get("screen")) for s in segs if len(str(s.get("screen") or "").split()) > 6]
    if long_screens:
        out.append(f"On-screen text must be 2-5 words; too long: “{long_screens[0]}”.")
    return out


def write(job: dict[str, Any], feedback: list[str], attempt: int) -> dict[str, Any]:
    """One script from the best available engine. The 5th attempt (or no AI available) uses the template script."""
    st = job["strategy"]
    pref = st.get("llm") or "auto"
    if attempt >= 5 or pref == "template":
        return template_script(job)
    base = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.environ.get("OLLAMA_MODEL") or st.get("ollama_model") or "gemma4:31b"
    if pref in ("auto", "ollama"):
        try:
            return {**ollama(job, feedback, base, model), "engine": f"ollama:{model}"}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if pref == "ollama":
                raise RuntimeError(f"Ollama couldn't write the script: {exc}") from exc
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and pref in ("auto", "anthropic"):
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
        return {**anthropic(job, feedback, key, model), "engine": f"anthropic:{model}"}
    return template_script(job)


def finish(script: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """The fields the backend checks: voiceover, storyboard, sources."""
    segs = script["segments"]
    return {**script, "voiceover": " ".join(s["text"].strip() for s in segs),
            "storyboard": [{"scene": s.get("scene", "facts"), "text": short(str(s.get("screen") or ""), 80)} for s in segs],
            "sources": job["brief"].get("sources") or []}
