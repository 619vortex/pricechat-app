"""Content Studio v2 production: script v2 → narrator voice → word timing → storyboard → motion render → creative QC.

The research brief and the backend's fact checks are unchanged. A "rerender" job (Content Studio: regenerate a scene,
the captions, the voice or the whole video with new creative settings) reuses the approved script, so nothing is
re-researched or re-written; the voice comes from the cache unless the narrator or the text changed."""

import shutil
import time
from pathlib import Path
from typing import Any, Callable

from pricechat_worker import creative, llm, media, motion, qc2, render, script2, storyboard, timing, voice2

LEGACY_VISUAL = {"hook": "price_reveal", "card": "card_entrance", "chart": "chart", "source": "evidence", "list": "list", "facts": "punchline",
                 "outro": "punchline", "thumbnail": "punchline"}


def segments_of(script: dict[str, Any], job: dict[str, Any]) -> list[dict[str, Any]]:
    """An approved script's segments, with v2 beats and visuals (older v1 scripts get them from their scene names)."""
    segs = [dict(s) for s in script.get("segments") or [] if str(s.get("text", "")).strip()]
    ok = set(script2.available_visuals(job))
    for k, s in enumerate(segs):
        if not s.get("visual"):
            v = LEGACY_VISUAL.get(str(s.get("scene") or ""), "punchline")
            s["visual"] = v if v in ok else "punchline"
        if not s.get("beat"):
            s["beat"] = "hook" if k == 0 else "ending" if k == len(segs) - 1 else "development"
    return segs


def write_script(job: dict[str, Any], cr: dict[str, Any], approve: Callable[[dict[str, Any]], dict[str, Any]],
                 step: Callable[[str, str, str], None]) -> dict[str, Any]:
    """Up to 5 AI drafts, then the facts-only template. Feedback accumulates (a local model otherwise fixes one word and
    brings back the last one). Unsupported claims are never sent for approval; style problems are tolerated on the 4th try."""
    feedback: list[str] = []

    def note(problems: list[str]) -> None:
        for p in problems:
            if p not in feedback:
                feedback.append(p)
        del feedback[:-6]

    for attempt in range(1, 7):
        script = script2.finish(script2.write(job, feedback, attempt, cr), job)
        if script.get("engine_errors"):
            step("script", "retrying", "AI writer unavailable, using the facts-only script: " + script["engine_errors"][-1])
        ai = script.get("engine") != "template"
        hard = (script2.claim_problems(script, job.get("brief") or {}) or llm.fact_check(job, script)) if ai else []
        soft = script2.style_problems(script) if ai else []
        if hard or (soft and attempt < 4):
            note(hard + soft)
            step("script", "retrying", (hard + soft)[0])
            continue
        verdict = approve(script)
        if verdict.get("ok"):
            return {**script, **verdict["script"], "segments": script["segments"]}
        note(verdict.get("problems") or [])
        step("script", "retrying", feedback[-1] if feedback else "")
    raise RuntimeError("Couldn't write a script that passes the fact and safety checks: " + "; ".join(feedback[-2:]))


def produce(job: dict[str, Any], ff: str, workdir: Path, approve: Callable[[dict[str, Any]], dict[str, Any]],
            step: Callable[[str, str, str], None]) -> dict[str, Any]:
    cr = creative.resolve(job)
    timings: dict[str, float] = {}
    t = time.monotonic()
    # real pictures and live data first, so the script can choose the scenes that show them
    pub = workdir / "public"
    pub.mkdir(parents=True, exist_ok=True)
    step("visuals", "running", "finding photos and live data")
    job = {**job, "media": media.gather(job, pub)}
    md = job["media"]
    timings["media"] = round(time.monotonic() - t, 1)
    step("visuals", "running", f"{len(md.get('photos') or [])} licensed photos" + (f" · {md['coin']['symbol']} live chart" if md.get("coin") else ""))
    t = time.monotonic()
    step("script", "running", f"{cr['tone']} tone")
    if job.get("mode") == "rerender" and (job.get("script") or {}).get("segments"):
        script = dict(job["script"])
        step("script", "done", "kept the approved script")
    else:
        script = write_script(job, cr, approve, step)
    segments = segments_of(script, job)
    timings["script"] = round(time.monotonic() - t, 1)
    t = time.monotonic()
    step("voice", "running", creative.NARRATORS[cr["narrator"]]["label"])
    rec = voice2.record(segments, cr, ff, workdir)
    timings["voice"] = round(time.monotonic() - t, 1)
    step("voice", "done", f"{rec['voice']} · {rec['duration']:.1f}s" + (" · cached" if rec.get("cached") else ""))
    t = time.monotonic()
    step("subtitles", "running", "word timing")
    asr = voice2.asr(workdir / "voice.wav", rec["key"], rec["spans"])
    words, stats = timing.words(segments, [tuple(x) for x in rec["spans"]], asr, cr["captions"])
    timings["timing"] = round(time.monotonic() - t, 1)
    step("subtitles", "done", f"{len(words)} words · {stats['matched'] * 100:.0f}% matched to the voice" if stats["asr"] else f"{len(words)} words")
    t = time.monotonic()
    step("visuals", "running", f"{cr['style']} style")
    music = None if cr["music"] == "none" else cr["music"]
    pub = motion.public_dir(workdir, music)
    shutil.copy(workdir / "voice.wav", pub / "voice.wav")
    props = storyboard.build(job, cr, segments, [tuple(x) for x in rec["spans"]], words, pub)
    bad = storyboard.unsupported(props, job.get("brief") or {}, job.get("media"))
    if bad:
        raise RuntimeError("Storyboard shows numbers that aren't in the research: " + ", ".join(bad[:3]))
    step("visuals", "done", f"{len(props['scenes'])} scenes · {len(props['audio']['sfx'])} sound effects")
    step("assemble", "running", "motion render")
    out = workdir / "video.mp4"
    stills = [(f"{i + 1} {s['kind']}", workdir / f"still_{i:02d}.png", int((s["start"] + s["end"]) / 2 * 30)) for i, s in enumerate(props["scenes"])]
    last = [0.0]

    def progress(msg: dict[str, Any]) -> None:
        if msg.get("step") == "render" and msg.get("progress", 0) - last[0] >= 0.05:  # the Studio progress bar shows these
            last[0] = msg["progress"]
            step("assemble", "running", f"motion render {msg['progress'] * 100:.0f}%")

    done = motion.render(props, pub, out, stills=[(f, p) for _, p, f in stills], on_progress=progress)
    timings["render"] = round(time.monotonic() - t, 1)
    meta = render.probe(ff, out)
    t = time.monotonic()
    report = qc2.run(ff, out, workdir / "voice.wav", meta, props, job.get("brief") or {}, stats, script, [(n, p) for n, p, _ in stills], md)
    timings["qc"] = round(time.monotonic() - t, 1)
    if not report["ok"]:
        raise RuntimeError("Quality check: " + "; ".join(report["failed"][:3]))
    thumb = workdir / "thumb.jpg"
    render.poster(ff, workdir, out, thumb, at=min(1.4, props["duration"] / 3))
    step("assemble", "done", f"{meta['duration']:.1f}s · {meta['size_bytes'] / 1e6:.1f} MB · {done.get('seconds', 0):.0f}s render")
    meta.update({"subtitles": bool(words), "tts": f"kokoro:{rec['voice']}", "llm": script.get("engine"), "renderer": "remotion", "cost_usd": 0.0,
                 "creative": {k: v for k, v in cr.items() if k not in ("pronunciations", "scenes")},
                 "storyboard": [{"kind": s["kind"], "start": s["start"], "end": s["end"], "transition": s["transition"], "screen": s.get("screen") or ""}
                                for s in props["scenes"]],
                 "segments": [{"visual": s.get("visual"), "screen": s.get("screen"), "beat": s.get("beat")} for s in segments],
                 "photo_credits": [{"credit": p["credit"], "page": p["page"]} for p in md.get("photos") or []],
                 "checks": report["checks"], "warnings": report["warnings"], "timings": timings, "narration_match": stats.get("matched"),
                 "heard": stats.get("heard", "")[:2000]})
    return {"out": out, "thumb": thumb, "meta": meta, "props": props, "qc": report, "script": script}
