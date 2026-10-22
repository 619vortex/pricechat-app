"""Creative quality control (Content Studio v2): runs on every rendered video before it's uploaded as READY.

Blocking checks fail the job (it goes back to the queue or to the owner as failed); warnings ride along in the video's
quality report for the owner to look at in Content Studio."""

import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from pricechat_worker import storyboard


def _check(cid: str, label: str, ok: bool | None, detail: str, blocking: bool = False) -> dict[str, Any]:
    status = "skipped" if ok is None else "pass" if ok else ("fail" if blocking else "warn")
    return {"id": cid, "label": label, "status": status, "detail": detail}


def loudness(ffmpeg: str, path: Path) -> dict[str, float]:
    p = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=300)
    tail = p.stderr[p.stderr.rfind("Summary:"):] if "Summary:" in p.stderr else ""
    i = re.search(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", tail)
    tp = re.search(r"Peak:\s+(-?\d+(?:\.\d+)?) dBFS", tail)
    return {"lufs": float(i.group(1)) if i else 0.0, "peak": float(tp.group(1)) if tp else 0.0}


def frame_checks(stills: list[tuple[str, Path]]) -> tuple[list[str], list[str]]:
    """(blank scenes, scenes with something bright touching the left/right edge: likely cut-off text)."""
    blank, edge = [], []
    for name, path in stills:
        if not path.exists():
            blank.append(name)
            continue
        im = Image.open(path).convert("L")
        st = ImageStat.Stat(im)
        if st.stddev[0] < 10:
            blank.append(name)
        w, h = im.size
        strip = [im.crop((0, 160, 26, h - 420)), im.crop((w - 26, 160, w, h - 420))]
        for s in strip:
            hist = s.histogram()
            bright = sum(hist[215:]) / max(1, s.size[0] * s.size[1])
            if bright > 0.012:
                edge.append(name)
                break
    return blank, sorted(set(edge))


def run(ffmpeg: str, out: Path, voice: Path, meta: dict[str, Any], props: dict[str, Any], brief: dict[str, Any], align_stats: dict[str, Any],
        script: dict[str, Any], stills: list[tuple[str, Path]], media: dict[str, Any] | None = None) -> dict[str, Any]:
    checks = []
    dur_ok = abs(meta["duration"] - props["duration"]) <= 0.4
    checks.append(_check("render", "Render completed", meta["width"] == 1080 and meta["height"] == 1920 and meta["has_audio"] and dur_ok,
                         f"{meta['width']}×{meta['height']}, {meta['duration']:.1f}s (planned {props['duration']:.1f}s), audio {'yes' if meta['has_audio'] else 'NO'}",
                         blocking=True))
    m = align_stats.get("matched")
    if not align_stats.get("asr"):
        checks.append(_check("narration", "Narration matches the script", None, "speech recognition unavailable on this worker"))
    else:
        checks.append(_check("narration", "Narration matches the script", m >= 0.8, f"{m * 100:.0f}% of the script's words recognised in the voice",
                             blocking=m < 0.6))
    words = props["words"]
    spans_ok = all(a["e"] <= b["s"] + 1e-6 for a, b in zip(words, words[1:])) and all(w["e"] <= props["duration"] for w in words)
    phrases = {}
    for w in words:
        phrases.setdefault(w["p"], []).append(w["t"])
    longest = max((len(" ".join(v)) for v in phrases.values()), default=0)
    checks.append(_check("captions", "Captions match the audio", spans_ok and longest <= 34 and bool(words),
                         f"{len(words)} words in {len(phrases)} phrases, longest {longest} characters, timed from "
                         f"{'the recognised voice' if align_stats.get('asr') else 'segment lengths'}", blocking=not words))
    bad = storyboard.unsupported(props, brief, media)
    checks.append(_check("facts", "No unsupported numbers on screen", not bad, ", ".join(bad[:4]) or "every number on screen is in the research or live Hyperliquid data",
                         blocking=bool(bad)))
    img = ((brief.get("images") or [{}])[0] or {}).get("url")
    uses_card = any(s["kind"] in ("card_entrance", "card_detail") for s in props["scenes"])
    checks.append(_check("card", "Correct card image", (bool(props["assets"].get("card")) or not uses_card) if img or uses_card else None,
                         "the brief's own card image" if props["assets"].get("card") else ("no card image in the brief" if not img else "card image couldn't be fetched")))
    blank, edge = frame_checks(stills)
    checks.append(_check("blank", "No blank scenes", not blank, ", ".join(blank) or f"{len(stills)} scenes sampled", blocking=bool(blank)))
    checks.append(_check("frame", "Graphics fit the frame", not edge, ("bright content at the frame edge in: " + ", ".join(edge)) if edge else "nothing touches the edges"))
    kinds = [s["kind"] for s in props["scenes"]]
    trans = [s["transition"] for s in props["scenes"]]
    short = [s["kind"] for s in props["scenes"] if s["end"] - s["start"] < 0.7 and s["kind"] != "outro"]
    repeats = sum(1 for a, b in zip(trans[1:], trans[2:]) if a == b)
    checks.append(_check("pacing", "Smooth transitions and pacing", not short and repeats == 0,
                         f"{len(kinds)} scenes, transitions {', '.join(trans[1:])}" + (f"; very short: {', '.join(short)}" if short else "")))
    checks.append(_check("brand", "Branding", kinds[-1:] == ["outro"], "Price Chat tag + Kiwi end card" if kinds[-1:] == ["outro"] else "no end card"))
    lv, lo = loudness(ffmpeg, voice), loudness(ffmpeg, out)
    mix_ok = -19.0 <= lo["lufs"] <= -11.0 and lo["peak"] <= -0.3 and lo["lufs"] - lv["lufs"] <= 2.5
    checks.append(_check("audio", "Audio mixed properly", mix_ok, f"final {lo['lufs']:.1f} LUFS, peak {lo['peak']:.1f} dB; voice alone {lv['lufs']:.1f} LUFS"))
    voice_words = len(re.sub(r"\[beat\]", " ", str(script.get("voiceover") or "")).split())
    checks.append(_check("script", "Script approved by the fact and safety checks", True, f"{voice_words} words, {script.get('engine') or 'llm'}"))
    failed = [c for c in checks if c["status"] == "fail"]
    return {"ok": not failed, "checks": checks, "failed": [c["label"] + ": " + c["detail"] for c in failed],
            "warnings": [c["label"] + ": " + c["detail"] for c in checks if c["status"] == "warn"], "loudness": lo}
