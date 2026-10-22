"""Voiceover v2: a narrator personality (Kokoro voice + speed + pauses), pronunciation fixes, designed pauses, one clean
master, and word-level timing for the captions.

- Pronunciation: DEFAULT_PRONUNCIATIONS + the brand's own (Content Studio). Tested with Whisper: Kokoro says "Pokémon" as
  "Paceman" and "Mewtwo" as "mute-woe", but gets "Pokey-mon" and "Mew two" right. Acronyms (PSA, BTC, ETF) are read
  correctly as written; spelling them out with spaces ("P S A", the v1 voice) made them worse.
- Pacing: each segment is spoken as one take (natural sentence flow), [beat] marks become real pauses, and segments are
  joined with the narrator's own gap. The hook is said a touch faster and the payoff a touch slower.
- Master: the takes are joined raw and processed once (high-pass, presence, gentle compression, loudness to -16 LUFS),
  so there are no level jumps between segments.
- Timing: Whisper word timestamps (align_asr.py), mapped onto the script's written words (timing.py).
- Cache: the same text + narrator gives the same voice from disk (re-rendering a scene or captions doesn't re-voice)."""

import hashlib
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from pricechat_worker import creative, gpu, tts

DEFAULT_PRONUNCIATIONS = {
    "Pokémon": "Pokey-mon", "Pokemon": "Pokey-mon", "POKÉMON": "Pokey-mon", "Mewtwo": "Mew two", "Hyperliquid": "Hyper liquid",
    "Umbreon": "Um-bree-on", "Gyarados": "Gyara-doss", "Rayquaza": "Ray-kwaza", "Lugia": "Loo-gee-ah", "Mew": "Mew",
    "Eevee": "Ee-vee", "Gengar": "Gen-gar", "VMAX": "V max", "VSTAR": "V star", "GX": "G X", "Dogecoin": "Dohj-coin",
}
CACHE = Path(__file__).resolve().parent.parent / "cache" / "voice"
BEAT = re.compile(r"\s*\[beat\]\s*", re.I)
_UNITS = {"k": "thousand", "m": "million", "b": "billion", "t": "trillion"}


def pronounce(text: str, overrides: dict[str, str] | None = None) -> str:
    table = {**DEFAULT_PRONUNCIATIONS, **(overrides or {})}
    for word in sorted(table, key=len, reverse=True):
        say = table[word]
        if not word or not say:
            continue
        text = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", say, text, flags=re.I)
    return text


def spoken(text: str, overrides: dict[str, str] | None = None) -> str:
    """How the narrator should read it: '$17,225' -> '17,225 dollars', '+3.2%' -> 'plus 3.2 percent', '#1' -> 'number 1',
    names respelled; captions keep the written form."""
    t = re.sub(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(?:([kmbt])\b|(thousand|million|billion|trillion)\b)?",
               lambda m: " ".join(x for x in (m.group(1), _UNITS.get((m.group(2) or "").lower()) or m.group(3), "dollars") if x), text, flags=re.I)
    t = re.sub(r"([+-])(\d[\d.,]*)\s?%", lambda m: ("plus " if m.group(1) == "+" else "minus ") + m.group(2) + " percent", t)
    t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    t = re.sub(r"#(\d+)", r"number \1", t)
    t = re.sub(r"\b(\d+)\s?h\b", r"\1 hours", t)
    t = t.replace("&", " and ").replace("·", ",").replace("—", ", ").replace("–", ", ").replace("…", "...")
    return re.sub(r"\s{2,}", " ", pronounce(t, overrides)).strip()


def plan(segments: list[dict[str, Any]], cr: dict[str, Any]) -> list[dict[str, Any]]:
    """The takes to record: one per [beat]-separated part of each segment, with its speed and the silence after it."""
    nar = creative.NARRATORS[cr["narrator"]]
    pace = {"slow": 0.96, "fast": 1.05}.get(cr.get("pace") or "normal", 1.0)
    takes = []
    for k, seg in enumerate(segments):
        beat = str(seg.get("beat") or "")
        mult = {"hook": 1.03, "payoff": 0.95 if cr["tone"] in ("dramatic", "collectible") else 0.98}.get(beat, 1.0)
        parts = [p for p in BEAT.split(str(seg["text"])) if p.strip()]
        for j, part in enumerate(parts):
            last_part = j == len(parts) - 1
            gap = nar["beat_gap"] if not last_part else (nar["sentence_gap"] * (1.6 if beat in ("development", "payoff") else 1.25))
            if k == len(segments) - 1 and last_part:
                gap = 0.0
            takes.append({"seg": k, "text": spoken(part, cr.get("pronunciations")), "speed": round(nar["speed"] * pace * mult, 3),
                          "voice": nar["voice"], "sentence_pause": nar["sentence_gap"], "gap": round(gap, 3)})
    return takes


def _key(takes: list[dict[str, Any]]) -> str:
    model = tts.kokoro_files()[0]
    blob = json.dumps({"t": takes, "m": model.stat().st_size if model.exists() else 0, "v": 2}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _master(ffmpeg: str, src: Path, dst: Path) -> None:
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-af",
                    "highpass=f=70,equalizer=f=180:t=q:w=1:g=1.5,equalizer=f=3200:t=q:w=1.2:g=2.5,deesser=i=0.3,"
                    "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=120,loudnorm=I=-16:TP=-1.5:LRA=8",
                    "-ar", "44100", "-ac", "1", "-sample_fmt", "s16", str(dst)], check=True, capture_output=True, timeout=180)


def record(segments: list[dict[str, Any]], cr: dict[str, Any], ffmpeg: str, workdir: Path) -> dict[str, Any]:
    """voice.wav in workdir + {"spans": per-segment (start, end), "takes": per-take (start, end, seg), "cached": bool}."""
    takes = plan(segments, cr)
    key = _key(takes)
    cdir = CACHE / key
    out = workdir / "voice.wav"
    if (cdir / "voice.wav").exists() and (cdir / "meta.json").exists():
        shutil.copy(cdir / "voice.wav", out)
        return {**json.loads((cdir / "meta.json").read_text(encoding="utf-8")), "cached": True, "key": key}
    raws = [workdir / f"take_{i:02d}.wav" for i in range(len(takes))]
    model, voices = tts.kokoro_files()
    p = subprocess.run([str(gpu.python()), str(Path(__file__).with_name("kokoro_tts.py")), str(model), str(voices), takes[0]["voice"], "1.0"],
                       input=json.dumps([{**t, "out": str(r)} for t, r in zip(takes, raws)]).encode(), capture_output=True, timeout=900)
    if p.returncode != 0 or not all(r.exists() for r in raws):
        raise RuntimeError("Kokoro voice failed: " + (p.stderr.decode(errors="replace").strip().splitlines() or ["no output"])[-1][:300])
    joined = workdir / "voice_raw.wav"
    tspans, t = [], 0.0
    with wave.open(str(joined), "wb") as dst:
        params = None
        for take, raw in zip(takes, raws):
            with wave.open(str(raw), "rb") as src:
                if params is None:
                    params = src.getparams()
                    dst.setparams(params)
                frames = src.readframes(src.getnframes())
                d = src.getnframes() / src.getframerate()
            dst.writeframes(frames)
            tspans.append((round(t, 3), round(t + d, 3), take["seg"]))
            t += d
            if take["gap"]:
                dst.writeframes(b"\x00" * int(params.framerate * take["gap"]) * params.sampwidth * params.nchannels)
                t += take["gap"]
    _master(ffmpeg, joined, out)
    spans = []
    for k in range(len(segments)):
        mine = [s for s in tspans if s[2] == k]
        spans.append((mine[0][0], mine[-1][1]) if mine else (t, t))
    meta = {"spans": spans, "takes": tspans, "narrator": cr["narrator"], "voice": takes[0]["voice"], "duration": round(t, 3)}
    cdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, cdir / "voice.wav")
    (cdir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {**meta, "cached": False, "key": key}


def asr(voice: Path, cache_key: str | None = None, spans: list[Any] | None = None) -> dict[str, Any] | None:
    """Whisper's word timestamps for the voice, each segment recognised on its own (cached with the voice). None when the
    GPU environment can't run it."""
    if cache_key and (CACHE / cache_key / "asr2.json").exists():
        return json.loads((CACHE / cache_key / "asr2.json").read_text(encoding="utf-8"))
    py = gpu.python()
    if not py:
        return None
    args = [str(py), str(Path(__file__).with_name("align_asr.py")), str(voice)]
    if spans:
        win = voice.with_name("asr_windows.json")
        win.write_text(json.dumps([list(x)[:2] for x in spans]), encoding="utf-8")
        args.append(str(win))
    p = subprocess.run(args, capture_output=True, timeout=600)
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout.decode(errors="replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if cache_key:
        (CACHE / cache_key).mkdir(parents=True, exist_ok=True)
        (CACHE / cache_key / "asr2.json").write_text(json.dumps(data), encoding="utf-8")
    return data
