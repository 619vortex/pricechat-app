"""Price Chat reward sounds: soft, short, synthesized here (no samples, no runtime fetch from anywhere else).

    python scripts/brand/make-sounds.py   ->  frontend/public/website/sounds/{xp,tick,levelup,streak}.wav

The Pack Giveaway sounds (rip, swish, rise, shine, chase, first) are layered ones from make-pack-sounds.py.

Mono 22.05 kHz 16-bit WAV, each a few KB. Bell-like sines (fundamental + quiet 2nd/3rd partials) with a soft attack and
an exponential tail: a light digital "ding", not an arcade jingle.
"""

import math
import struct
import wave
from pathlib import Path

RATE = 22050
OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "website" / "sounds"


def note(buf: list[float], start: float, freq: float, length: float, amp: float, decay: float) -> None:
    s0 = int(start * RATE)
    for i in range(int(length * RATE)):
        t = i / RATE
        env = min(1.0, t / 0.004) * math.exp(-t / decay)  # 4 ms attack, no click
        v = math.sin(2 * math.pi * freq * t) + 0.28 * math.sin(4 * math.pi * freq * t) + 0.08 * math.sin(6 * math.pi * freq * t)
        j = s0 + i
        if j < len(buf):
            buf[j] += amp * env * v / 1.36


def write(name: str, seconds: float, notes: list[tuple[float, float, float, float, float]]) -> None:
    buf = [0.0] * int(seconds * RATE)
    for n in notes:
        note(buf, *n)
    fade = int(0.03 * RATE)  # always end in silence
    for i in range(fade):
        buf[-1 - i] *= i / fade
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 32767)) for v in buf))


# XP pickup: E6 then B6, overlapping (~0.38 s)
write("xp", 0.38, [(0.0, 1318.5, 0.16, 0.42, 0.05), (0.07, 1975.5, 0.31, 0.5, 0.09)])
# halfway tick: one tiny, quiet E7 (~0.07 s)
write("tick", 0.07, [(0.0, 2637.0, 0.07, 0.16, 0.015)])
# level up: E5 G#5 B5 rising to a held E6 (~0.85 s), a little fuller than the XP ding
write("levelup", 0.85, [(0.0, 659.3, 0.3, 0.36, 0.08), (0.09, 830.6, 0.3, 0.36, 0.08), (0.18, 987.8, 0.3, 0.38, 0.09),
                         (0.27, 1318.5, 0.58, 0.48, 0.2), (0.27, 1975.5, 0.5, 0.12, 0.14)])
# streak: a quick, warm rising run E5 B5 E6 then a bright G#6 + B6 shimmer (~0.7 s): "the flame grows"
write("streak", 0.7, [(0.0, 659.3, 0.18, 0.3, 0.05), (0.06, 987.8, 0.18, 0.32, 0.05), (0.12, 1318.5, 0.22, 0.36, 0.07),
                       (0.2, 1661.2, 0.48, 0.4, 0.16), (0.2, 1975.5, 0.45, 0.18, 0.12), (0.26, 2637.0, 0.3, 0.06, 0.08)])
def _save(name: str, buf: list[float]) -> None:
    fade = int(0.02 * RATE)
    for i in range(min(fade, len(buf))):
        buf[-1 - i] *= i / fade
    peak = max(1e-9, max(abs(v) for v in buf))
    if peak > 0.98:
        buf = [v * 0.98 / peak for v in buf]
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 32767)) for v in buf))


def noise_layer(seconds: float, seed: int, cutoff_from: float, cutoff_to: float, env) -> list[float]:  # type: ignore[no-untyped-def]
    """Low-passed white noise with a moving cutoff (bright -> dull or the reverse) under an envelope function."""
    import random

    rnd = random.Random(seed)
    n = int(seconds * RATE)
    out, lp, hp_prev, x_prev = [], 0.0, 0.0, 0.0
    for i in range(n):
        t = i / RATE
        c = cutoff_from + (cutoff_to - cutoff_from) * (t / seconds)
        x = rnd.uniform(-1, 1)
        lp += c * (x - lp)
        hp = 0.92 * (hp_prev + lp - x_prev)  # gentle high-pass: no rumble
        hp_prev, x_prev = hp, lp
        out.append(hp * env(t))
    return out


def rip() -> None:
    """Opening a foil booster: crinkle crackles, then the tear (a bright noise sweep) and a soft thump."""
    import random

    rnd = random.Random(11)
    secs = 0.75
    buf = [0.0] * int(secs * RATE)
    for _ in range(26):  # foil crinkles: tiny random crackles in the first 0.3 s
        at = rnd.uniform(0.0, 0.3)
        burst = noise_layer(0.012, rnd.randint(0, 10_000), 0.9, 0.6, lambda t: math.exp(-t / 0.003))
        s0 = int(at * RATE)
        amp = rnd.uniform(0.25, 0.6)
        for j, v in enumerate(burst):
            if s0 + j < len(buf):
                buf[s0 + j] += amp * v
    tear = noise_layer(0.42, 5, 0.75, 0.18, lambda t: min(1.0, t / 0.015) * math.exp(-t / 0.16) * (1 + 0.5 * math.sin(2 * math.pi * 47 * t)))
    s0 = int(0.28 * RATE)
    for j, v in enumerate(tear):
        if s0 + j < len(buf):
            buf[s0 + j] += 1.4 * v
    for j in range(int(0.12 * RATE)):  # the pack giving way
        t = j / RATE
        k = s0 + int(0.04 * RATE) + j
        if k < len(buf):
            buf[k] += 0.45 * math.sin(2 * math.pi * (110 - 260 * t) * t) * math.exp(-t / 0.05)
    _save("rip", buf)


def swish() -> None:
    """A card sliding / turning over: a short airy noise swell."""
    buf = noise_layer(0.18, 3, 0.25, 0.7, lambda t: math.sin(math.pi * min(1.0, t / 0.18)) ** 2 * 0.9)
    _save("swish", buf)


def rise() -> None:
    """Anticipation before the hit card: a whoosh that rises, with a faint rising tone."""
    secs = 0.6
    air = noise_layer(secs, 9, 0.08, 0.6, lambda t: (t / secs) ** 2 * 0.7)
    buf = []
    for i, v in enumerate(air):
        t = i / RATE
        f = 300 + 900 * (t / secs) ** 2
        buf.append(v + 0.12 * (t / secs) * math.sin(2 * math.pi * f * t))
    _save("rise", buf)


print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.wav"))))
