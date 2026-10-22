"""Kokoro speech (a local neural voice, Apache-2.0), run as its own process with the GPU environment's Python:

    python kokoro_tts.py <kokoro.onnx> <voices.bin> <voice> <speed>   < [{"text": "...", "out": "a.wav"}, ...]

One process speaks every scene of a story, so the model loads once. Writes 24 kHz mono 16-bit WAV files."""

import json
import sys
import wave

import numpy as np
from kokoro_onnx import Kokoro


def main() -> None:
    model, voices, voice, speed = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
    kokoro = Kokoro(model, voices)
    for item in json.load(sys.stdin):
        # an item may bring its own narrator, speed and pause lengths (Content Studio v2); else the command line's
        v = item.get("voice") or voice
        lang = "en-gb" if v[:1] == "b" else "en-us"
        samples, rate = kokoro.create(item["text"], voice=v, speed=float(item.get("speed") or speed), lang=lang,
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
