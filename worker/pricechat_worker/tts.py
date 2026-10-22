"""Voiceover. Engines (strategy.voice = auto picks the first available, most natural first):
- kokoro  local neural TTS, the most natural (Brand Manager's narrator + Short Market): the GPU environment (gpu.python())
          + the model files in worker/models/kokoro (or KOKORO_MODEL / KOKORO_VOICES); strategy.voice_name / voice_speed
- openai  cloud TTS (OPENAI_API_KEY on the worker; OPENAI_VOICE, default "alloy")
- piper   local neural TTS: `piper` on PATH + PIPER_MODEL=/path/voice.onnx
- system  the operating system's voice: Windows SAPI (PowerShell) or espeak-ng on Linux (always works, robotic)
Each segment is spoken separately so the scenes and subtitles line up exactly with the narration."""

import json
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import httpx

from pricechat_worker import gpu

KOKORO_SPEED = 1.08  # a little brisk: short videos are watched fast


def kokoro_files() -> tuple[Path, Path]:
    d = gpu.MODELS / "kokoro"
    return Path(os.environ.get("KOKORO_MODEL") or d / "kokoro-v1.0.onnx"), Path(os.environ.get("KOKORO_VOICES") or d / "voices-v1.0.bin")


def available() -> list[str]:
    """Engines on this worker, most natural first ("auto" picks the first)."""
    out = []
    if gpu.python() and all(f.exists() for f in kokoro_files()):
        out.append("kokoro")
    if os.environ.get("OPENAI_API_KEY"):
        out.append("openai")
    if shutil.which("piper") and os.environ.get("PIPER_MODEL"):
        out.append("piper")
    if sys.platform == "win32" or shutil.which("espeak-ng"):
        out.append("system")  # robotic: only when nothing else is installed
    return out


_UNITS = {"k": "thousand", "m": "million", "b": "billion", "t": "trillion"}


def spoken(text: str) -> str:
    """The narration as a voice should read it ("$150,000" → "150,000 dollars", "+3.2%" → "plus 3.2 percent", "#1" → "number 1",
    "BTC" → "B T C"); captions keep the written form."""
    t = re.sub(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(?:([kmbt])\b|(thousand|million|billion|trillion)\b)?",
               lambda m: " ".join(x for x in (m.group(1), _UNITS.get((m.group(2) or "").lower()) or m.group(3), "dollars") if x), text, flags=re.I)
    t = re.sub(r"([+-])(\d[\d.,]*)\s?%", lambda m: ("plus " if m.group(1) == "+" else "minus ") + m.group(2) + " percent", t)
    t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    t = re.sub(r"#(\d+)", r"number \1", t)
    t = re.sub(r"\b(\d+)\s?h\b", r"\1 hours", t)
    t = re.sub(r"\b(BTC|ETH|SOL|XRP|BNB|ADA|TON|NFT|ETF|SEC|PSA|BGS|CGC|TCG)s?\b", lambda m: " ".join(m.group(1)) + m.group(0)[len(m.group(1)):], t)
    t = t.replace("&", " and ").replace("·", ",").replace("—", ", ").replace("–", ", ")
    return re.sub(r"\s{2,}", " ", t).strip()


def kokoro_batch(texts: list[str], outs: list[Path], voice: str, ffmpeg: str, speed: float = KOKORO_SPEED) -> None:
    """Every scene of a story in one process (the model loads once)."""
    model, voices = kokoro_files()
    raws = [o.with_suffix(".raw.wav") for o in outs]
    p = subprocess.run([str(gpu.python()), str(Path(__file__).with_name("kokoro_tts.py")), str(model), str(voices), voice, str(speed)],
                       input=json.dumps([{"text": t, "out": str(r)} for t, r in zip(texts, raws)]).encode(), capture_output=True, timeout=600)
    if p.returncode != 0 or not all(r.exists() for r in raws):
        raise RuntimeError("Kokoro voice failed: " + (p.stderr.decode(errors="replace").strip().splitlines() or ["no output"])[-1][:300])
    for raw, out in zip(raws, outs):
        _normalize(ffmpeg, raw, out)


def _piper(text: str, out: Path) -> None:
    subprocess.run(["piper", "--model", os.environ["PIPER_MODEL"], "--output_file", str(out)], input=text.encode(), check=True,
                   capture_output=True, timeout=120)


def _openai(text: str, out: Path, ffmpeg: str) -> None:
    r = httpx.post("https://api.openai.com/v1/audio/speech", timeout=120.0, headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                   json={"model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"), "voice": os.environ.get("OPENAI_VOICE", "alloy"),
                         "input": text, "response_format": "wav"})
    r.raise_for_status()
    raw = out.with_suffix(".raw.wav")
    raw.write_bytes(r.content)
    _normalize(ffmpeg, raw, out)


def _system(text: str, out: Path, ffmpeg: str) -> None:
    raw = out.with_suffix(".raw.wav")
    if sys.platform == "win32":
        voice = os.environ.get("SAPI_VOICE", "").replace("'", "")
        txt = out.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  + (f"$s.SelectVoice('{voice}'); " if voice else "") + "$s.Rate = 1; "
                  f"$s.SetOutputToWaveFile('{raw}'); $s.Speak((Get-Content -Raw -Encoding UTF8 '{txt}')); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, capture_output=True, timeout=120)
        txt.unlink(missing_ok=True)
    else:
        subprocess.run(["espeak-ng", "-v", "en-us", "-s", "165", "-w", str(raw), text], check=True, capture_output=True, timeout=120)
    _normalize(ffmpeg, raw, out)


TRIM = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,areverse,"
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.06,areverse,")


def _normalize(ffmpeg: str, src: Path, dst: Path) -> None:
    """Every engine's output -> 44.1 kHz mono 16-bit WAV: the silence at both ends trimmed (tight pacing), a touch of
    presence and compression (a voice that cuts through phone speakers), loudness-normalized so segments join cleanly."""
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-af",
                    TRIM + "highpass=f=70,equalizer=f=3000:t=q:w=1.2:g=2.5,acompressor=threshold=-20dB:ratio=3:attack=5:release=80,"
                    "loudnorm=I=-14:TP=-1.5:LRA=9", "-ar", "44100", "-ac", "1",
                    "-sample_fmt", "s16", str(dst)], check=True, capture_output=True, timeout=120)
    src.unlink(missing_ok=True)


def speak(text: str, out: Path, engine: str, ffmpeg: str) -> None:
    if engine == "kokoro":
        kokoro_batch([text], [out], os.environ.get("KOKORO_VOICE", "am_michael"), ffmpeg)
    elif engine == "piper":
        raw = out.with_suffix(".raw.wav")
        _piper(text, raw)
        _normalize(ffmpeg, raw, out)
    elif engine == "openai":
        _openai(text, out, ffmpeg)
    else:
        _system(text, out, ffmpeg)


def pick(preference: str) -> str:
    have = available()
    if not have:
        raise RuntimeError("No text-to-speech engine on this worker (install piper + PIPER_MODEL, set OPENAI_API_KEY, or espeak-ng).")
    if preference in have:
        return preference
    return have[0]


def duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def join(parts: list[Path], out: Path, gap: float = 0.18) -> list[tuple[float, float]]:
    """Concatenate segment WAVs with a short pause; returns each segment's (start, end) in the result."""
    spans = []
    t = 0.0
    with wave.open(str(out), "wb") as dst:
        params = None
        for i, p in enumerate(parts):
            with wave.open(str(p), "rb") as src:
                if params is None:
                    params = src.getparams()
                    dst.setparams(params)
                frames = src.readframes(src.getnframes())
                d = src.getnframes() / src.getframerate()
            dst.writeframes(frames)
            spans.append((t, t + d))
            t += d
            if i < len(parts) - 1:
                assert params is not None
                silence = int(params.framerate * gap)
                dst.writeframes(b"\x00" * silence * params.sampwidth * params.nchannels)
                t += gap
    return spans
