"""Word timestamps for a voiceover (Whisper, run as its own process with the GPU environment's Python):

    python align_asr.py <audio.wav> [model]   ->   {"words": [{"w": "Someone", "s": 0.12, "e": 0.41}, ...], "text": "..."}

Used to sync captions to the actual narration and to check that the narration says the script (quality control).
Default model: openai/whisper-small.en (downloaded once into the Hugging Face cache, ~0.5 GB; runs on the GPU)."""

import json
import sys
import wave

import numpy as np


def load(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2" if width == 2 else "<i4").astype(np.float32)
    x /= 32768.0 if width == 2 else 2147483648.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16000:  # linear resample is fine for speech recognition
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x, 16000


def main() -> None:
    """argv: <audio.wav> [windows.json]. With windows ([[start, end], ...] seconds: one per narration segment) each
    segment is recognised on its own, which keeps Whisper from merging or skipping lines across long pauses."""
    import os

    import torch
    from transformers import pipeline

    path = sys.argv[1]
    windows = json.loads(open(sys.argv[2], encoding="utf-8").read()) if len(sys.argv) > 2 else None
    model = os.environ.get("PCW_ASR_MODEL", "openai/whisper-small.en")
    dev = 0 if torch.cuda.is_available() else -1
    asr = pipeline("automatic-speech-recognition", model=model, device=dev, torch_dtype=torch.float16 if dev == 0 else torch.float32)
    audio, sr = load(path)
    pieces = [(0.0, audio)] if not windows else [
        (max(0.0, s - 0.15), audio[int(max(0.0, s - 0.15) * sr):int((e + 0.2) * sr)]) for s, e in windows]
    words, texts = [], []
    for offset, clip in pieces:
        if len(clip) < sr * 0.2:
            continue
        out = asr({"raw": clip, "sampling_rate": sr}, return_timestamps="word", chunk_length_s=30, batch_size=4)
        texts.append(str(out.get("text", "")).strip())
        for c in out.get("chunks") or []:
            s, e = c.get("timestamp") or (None, None)
            if s is None:
                continue
            words.append({"w": str(c.get("text", "")).strip(), "s": round(offset + float(s), 3),
                          "e": round(offset + float(e if e is not None else s + 0.3), 3)})
    print(json.dumps({"words": words, "text": " ".join(texts)}))


if __name__ == "__main__":
    main()
