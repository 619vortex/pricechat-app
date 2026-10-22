"""Short Market story writing: a short story told in scenes, each with the words spoken, a few words shown big, and what
the 3D shot shows. Same engines as Brand Manager scripts (the local Ollama first, Anthropic only if its key is on the
worker, then a script assembled from the brief's own sentences). The backend checks every story (no invented numbers,
no financial claims, every scene has a picture) and the worker rewrites with that feedback."""

import json
import os
import re
from typing import Any

import httpx

from pricechat_worker.llm import WORDS_PER_SECOND, short

SECONDS_PER_SCENE = 4.3

# Pictures for a story written without AI (the template script), by category: symbolic, no people, no text.
STOCK_VISUALS = {
    "markets": ["a huge glowing candlestick chart rises like a city skyline while a small round robot looks up at it, slow push-in",
                "golden coins pour slowly into a glass vault that glows from inside, camera orbits",
                "a giant needle on a glowing gauge swings from one side to the other above a tiny city, low angle",
                "a small round robot rides a rollercoaster shaped like a price chart, tracking shot"],
    "news": ["a tiny robot newsboy throws glowing newspapers across a miniature city at sunrise, tracking shot",
             "a great bank building with tall columns opens its heavy doors and warm light spills out, slow push-in",
             "stacks of coins grow like towers while clouds race overhead, time-lapse feeling, wide shot",
             "a small round robot reads a glowing tablet in a busy toy street, camera circles it"],
    "history": ["an old wooden trading floor filled with flying paper slips, warm dusty light, slow dolly forward",
                "a brass pocket watch spins backwards above an antique desk covered with old coins, macro shot",
                "an old steam train loaded with gold bars crosses a tall bridge at dawn, wide aerial shot",
                "a dusty ledger book opens by itself and golden light rises from its pages, slow push-in"],
    "facts": ["a single golden coin spins on a dark table and throws light across the room, macro shot",
              "a small round robot opens a treasure chest and its face lights up, slow push-in",
              "a maze made of gold blocks seen from above while a glowing ball rolls through it, top-down shot",
              "a row of dominoes shaped like coins falls one after another, tracking shot"],
    "collectibles": ["a sealed glass display case with a glowing trading card slowly turning inside, museum lighting, orbit shot",
                     "a small round robot holds a magnifying glass over a shining card on a velvet table, macro shot",
                     "a vault door opens to reveal shelves of softly glowing collectible cards, slow dolly in",
                     "an auction hammer falls in slow motion and sparks of gold fly up, close-up"],
    "buzz": ["thousands of small glowing speech bubbles rise from a miniature city at night, wide shot",
             "a small round robot scrolls a giant floating screen while hearts and arrows fly past, medium shot",
             "a megaphone on a rooftop sends out rings of light across a toy city, slow orbit",
             "a crowd of tiny cartoon robots all look up at the same glowing sign in the sky, low angle"],
}


# The made-up guide that walks through a film. One per short (by its number), so the videos don't all look the same.
GUIDES = ("a small round orange robot with big curious eyes", "a clever red fox in a tiny navy suit", "a chubby pink piggy bank with little legs",
          "a wise grey owl wearing round golden glasses", "a tiny astronaut cat in a white space suit", "a cheerful blue penguin with a yellow scarf",
          "a raccoon detective in a brown trench coat", "a small green dragon who hoards shiny coins")


def guide(job: dict[str, Any]) -> str:
    return GUIDES[(int(job.get("job_id") or 0) + int(job.get("generation") or 1) - 1) % len(GUIDES)]


def scenes_for(seconds: int) -> int:
    return max(3, min(12, round(seconds / SECONDS_PER_SCENE)))


def _prompt(job: dict[str, Any], feedback: list[str]) -> tuple[str, str]:
    b, brief, n = job["brand"], job["brief"], scenes_for(int(job["seconds"]))
    words = int(job["seconds"] * WORDS_PER_SECOND)
    system = (
        f"You write short vertical video stories about money for {b['name']}. Audience: {b.get('audience') or 'curious people scrolling'}. "
        f"Language: {b.get('language') or 'English'}. The video is a 3D animated film with a narrator.\n"
        "RULES (all mandatory):\n"
        "- Use ONLY the facts in the brief. Never add a number, price, date, name, quote or claim that is not in the brief, and never "
        "explain background the brief doesn't give. Say each number the way the brief gives it (3.65 percent stays 3.65 percent, "
        "never 'three percent' or 'about four').\n"
        "- No predictions, no advice, no hype words (guaranteed, to the moon, buy now, will hit...). Say where news comes from.\n"
        "- It is a STORY told to a friend, not a list: scene 1 is a hook that stops the scroll (a surprise, a contrast or a question, "
        "at most 12 words); the middle builds it one beat at a time; near the end comes the turn or the detail people will repeat; "
        "the last scene lands it in one line and ends with 'Follow for more.'\n"
        f"- Exactly {n} scenes, about {words} words in total. Each scene's \"text\" is one or two short spoken sentences, 10 to 16 "
        "words, plain words, contractions, no emojis.\n"
        "- \"screen\" is 2 to 4 punchy words shown big on screen for that scene. They describe what is being said, they never "
        "predict. A number on screen is copied exactly from the scene's text (never rounded to a different number, never a new one).\n"
        "- \"visual\" describes one 3D animated shot for that scene in 15 to 30 words: one clear subject, what it does, where, and a "
        "camera move. Make the idea visible with objects and symbols: coins, vaults, glowing charts, scales, trains, rockets, "
        "weather, cities. NEVER real people or their names, never company names or logos, never written words, letters or numbers "
        "in the picture.\n"
        f"- The guide of this film is {guide(job)}. It appears in most scenes, reacting to what happens: name it by exactly that "
        "description in those visuals.\n"
        "- Never copy wording from other creators."
    )
    user = (
        f"Kind of story: {job.get('angle') or 'a short money story'}\nTopic: {job['topic']}\n"
        f"Brief (the only facts you may use):\n{json.dumps({k: brief.get(k) for k in ('headline', 'facts', 'numbers', 'sources', 'note')}, ensure_ascii=False)}\n\n"
        "Return JSON only, exactly this shape:\n"
        '{"segments": [' + ", ".join('{"text": "...", "screen": "...", "visual": "..."}' for _ in range(2)) + ", ...], "
        '"title": "max 80 chars, makes people curious", "description": "1-2 sentences, name the source", "hashtags": ["#..."]}\n'
        f"Exactly {n} segments."
    )
    if feedback:
        user += "\n\nYour previous story was rejected. Fix ALL of these:\n" + "\n".join(f"- {f}" for f in feedback)
    return system, user


def _parse(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    data = json.loads(m.group(0))
    segs = [{"text": str(s.get("text") or "").strip(), "screen": str(s.get("screen") or "").strip(), "visual": str(s.get("visual") or "").strip()}
            for s in data.get("segments") or [] if isinstance(s, dict) and str(s.get("text", "")).strip()]
    if not segs:
        raise ValueError("no segments")
    return {"hook": segs[0]["text"], "segments": segs,
            "title": str(data.get("title") or "").strip(), "description": str(data.get("description") or "").strip(),
            "hashtags": [str(h) for h in data.get("hashtags") or []][:6]}


def _ollama(job: dict[str, Any], feedback: list[str], base: str, model: str) -> dict[str, Any]:
    system, user = _prompt(job, feedback)
    r = httpx.post(f"{base.rstrip('/')}/api/chat", timeout=300.0, json={
        "model": model, "stream": False, "format": "json", "options": {"temperature": 0.75},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return _parse(r.json()["message"]["content"])


def _anthropic(job: dict[str, Any], feedback: list[str], key: str, model: str) -> dict[str, Any]:
    system, user = _prompt(job, feedback)
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=120.0, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": model, "max_tokens": 2000, "system": system, "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return _parse("".join(b.get("text", "") for b in r.json().get("content") or []))


def template_story(job: dict[str, Any]) -> dict[str, Any]:
    """No AI: the brief's own sentences, one per scene, over stock pictures (every number is the brief's)."""
    brief = job["brief"]
    facts = [str(f["text"] if isinstance(f, dict) else f).strip() for f in brief.get("facts") or []]
    src = (brief.get("sources") or [{}])[0].get("name", "")
    headline = str(brief.get("headline") or job["topic"]).rstrip(".")
    pics = STOCK_VISUALS.get(job.get("category") or "", STOCK_VISUALS["facts"])
    lines = [f"{headline}."] + [short(f, 170) for f in facts[:8]]
    if src:
        lines.append(f"That's according to {src}.")
    lines.append("That's the story. Follow for more.")
    segs = [{"text": t, "screen": short(re.sub(r"[^\w\s%$.,'-]", "", t), 28).rstrip(".,"), "visual": pics[k % len(pics)]} for k, t in enumerate(lines)]
    return {"hook": lines[0], "segments": segs, "title": short(headline, 80),
            "description": f"{headline}. Source: {src}." if src else f"{headline}.", "hashtags": ["#money", "#finance"], "engine": "template"}


def write(job: dict[str, Any], feedback: list[str], attempt: int) -> dict[str, Any]:
    """One story from the best available engine. The 4th attempt (or no AI available) uses the template story."""
    if attempt >= 4:
        return template_story(job)
    base = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.environ.get("OLLAMA_MODEL") or "gemma4:31b"
    try:
        return {**_ollama(job, feedback, base, model), "engine": f"ollama:{model}"}
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
        try:
            return {**_anthropic(job, feedback, key, model), "engine": f"anthropic:{model}"}
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return template_story(job)


def release() -> None:
    """Take the writing model out of GPU memory: the video model needs the room (a 27B writer plus the video model
    overflow a 32 GB card and filming crawls). Ollama loads it again by itself the next time a story is written."""
    base = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    try:
        httpx.post(f"{base.rstrip('/')}/api/generate", timeout=30.0, json={"model": os.environ.get("OLLAMA_MODEL") or "gemma4:31b", "keep_alive": 0})
    except httpx.HTTPError:
        pass  # not running, or already unloaded


def finish(script: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """The fields the backend checks: voiceover, storyboard (the words shown on screen), sources."""
    segs = script["segments"]
    if "follow" not in segs[-1]["text"].lower():  # the last line always asks for the follow
        segs[-1]["text"] = segs[-1]["text"].rstrip() + " Follow for more."
    return {**script, "character": guide(job), "voiceover": " ".join(s["text"].strip() for s in segs),
            "storyboard": [{"scene": "story", "text": short(str(s.get("screen") or ""), 60)} for s in segs],
            "sources": job["brief"].get("sources") or []}


def shot_prompt(seg: dict[str, Any], script: dict[str, Any], style: dict[str, Any]) -> str:
    """What the video model is asked to draw for one scene."""
    visual = re.sub(r"\s+", " ", str(seg.get("visual") or "")).strip().rstrip(".")
    character = str(script.get("character") or "").strip().rstrip(".")
    known = re.sub(r"^(a|an|the)\s+", "", character.lower())[:24]
    cast = f" The guide of the story is {character}." if character and known not in visual.lower() else ""
    look = str(style["prompt"]).strip().rstrip(".")  # the look comes first: the model weighs the start of a prompt most
    return f"{look[:1].upper()}{look[1:]}. {visual}.{cast} Rendered in 3D with depth, soft shadows and volumetric light. Vertical composition, no text."
