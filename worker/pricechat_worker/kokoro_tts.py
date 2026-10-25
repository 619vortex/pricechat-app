"""Kokoro speech (a local neural voice, Apache-2.0), run as its own process with the GPU environment's Python:

    python kokoro_tts.py <kokoro.onnx> <voices.bin> <voice> <speed>   < [{"text": "...", "out": "a.wav"}, ...]

One process speaks every scene of a story, so the model loads once. Writes 24 kHz mono 16-bit WAV files.
A voice is a name ("am_michael") or a mix of names by weight ("am_michael:60+bm_george:40"): their style vectors blended."""

import json
import re
import sys
import wave

_PART = re.compile(r"^([a-z]{2}_[a-z]+)(?::(\d{1,3}))?$")


def mix(voice: str) -> list[tuple[str, float]]:
    """[(name, weight 0..1)] of a voice or a mix; weights are normalised. Raises ValueError on a malformed key."""
    out = []
    for part in voice.split("+"):
        m = _PART.match(part.strip())
        if not m:
            raise ValueError(f"not a voice: {voice!r}")
        out.append((m.group(1), float(m.group(2) or 100)))
    total = sum(w for _, w in out)
    if total <= 0:
        raise ValueError(f"not a voice: {voice!r}")
    return [(n, w / total) for n, w in out]


def lang(voice: str) -> str:
    """The first voice's accent ("b…" = British) decides the pronunciation."""
    return "en-gb" if voice.strip()[:1] == "b" else "en-us"


def style(kokoro, voice: str):
    parts = mix(voice)
    if len(parts) == 1:
        return parts[0][0]
    import numpy as np

    return sum(kokoro.get_voice_style(n) * w for n, w in parts).astype(np.float32)


def main() -> None:
    import numpy as np  # the GPU environment's (this file's helpers import without it)
    from kokoro_onnx import Kokoro

    model, voices, voice, speed = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
    kokoro = Kokoro(model, voices)
    styles: dict[str, object] = {}
    for item in json.load(sys.stdin):
        # an item may bring its own narrator, speed and pause lengths (Content Studio v2); else the command line's
        v = item.get("voice") or voice
        if v not in styles:
            styles[v] = style(kokoro, v)
        samples, rate = kokoro.create(item["text"], voice=styles[v], speed=float(item.get("speed") or speed), lang=lang(v),
                                      sentence_pause=float(item.get("sentence_pause", 0.25)), clause_pause=float(item.get("clause_pause", 0.1)))
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(item["out"], "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        print(json.dumps({"done": item["out"]}), flush=True)


if __name__ == "__main__":
    main()
