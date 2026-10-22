"""Price Chat's video audio: every music bed and sound effect is synthesized here from scratch (no samples, no third-party
recordings), so it is original and free to use on any platform. Run once with the GPU environment's Python (it has numpy):

    worker/.venv-gpu/Scripts/python worker/motion/make_audio.py

Writes 44.1 kHz stereo 16-bit WAVs into worker/motion/static/{music,sfx}. Deterministic (fixed seeds): re-running gives the
same files. Each bed records its tempo in music/beds.json so the renderer can cut on the beat."""

import json
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).with_name("static")
rng = np.random.default_rng(7)


# --- building blocks -----------------------------------------------------------------------------------------------------

def t_(sec: float) -> np.ndarray:
    return np.arange(int(sec * SR)) / SR


def env(n: int, a: float, d: float, s: float = 0.0, r: float = 0.0) -> np.ndarray:
    """Attack/decay/sustain-level/release (seconds) over n samples."""
    e = np.full(n, s, dtype=np.float64)
    ai, di, ri = int(a * SR), int(d * SR), int(r * SR)
    ai = min(ai, n)
    e[:ai] = np.linspace(0, 1, ai, endpoint=False)
    dd = min(di, n - ai)
    e[ai:ai + dd] = np.linspace(1, s, dd, endpoint=False) if dd > 0 else e[ai:ai + dd]
    if ri:
        ri = min(ri, n)
        e[n - ri:] *= np.linspace(1, 0, ri)
    return e


def expdecay(n: int, tau: float) -> np.ndarray:
    return np.exp(-np.arange(n) / SR / tau)


def fft_filter(x: np.ndarray, lo: float | None = None, hi: float | None = None, slope: float = 2.0) -> np.ndarray:
    """A smooth band filter in the frequency domain (fine for whole sounds: no real-time needed)."""
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    if lo:
        g *= 1 / (1 + (lo / np.maximum(f, 1e-3)) ** (2 * slope))
    if hi:
        g *= 1 / (1 + (f / hi) ** (2 * slope))
    return np.fft.irfft(X * g, n)


def sweep_filter(x: np.ndarray, centers: np.ndarray, q: float = 1.5, block: int = 1024) -> np.ndarray:
    """A band-pass whose centre moves over time (whooshes): overlap-add of filtered blocks."""
    out = np.zeros(len(x) + block)
    win = np.hanning(block)
    hop = block // 2
    f = np.fft.rfftfreq(block, 1 / SR)
    for i in range(0, len(x) - block, hop):
        c = centers[min(i + hop, len(centers) - 1)]
        bw = c / q
        g = np.exp(-0.5 * ((f - c) / bw) ** 2)
        seg = np.fft.irfft(np.fft.rfft(x[i:i + block] * win) * g, block)
        out[i:i + block] += seg
    return out[:len(x)]


def reverb(x: np.ndarray, seconds: float = 2.2, wet: float = 0.25, seed: int = 1) -> np.ndarray:
    r = np.random.default_rng(seed)
    n = int(seconds * SR)
    ir = r.standard_normal(n) * expdecay(n, seconds / 6)
    ir = fft_filter(ir, lo=200, hi=7000)
    ir /= np.sqrt(np.sum(ir ** 2))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:len(x)]
    return x * (1 - wet) + y * wet * 0.8


def note(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def saw(freq: float, t: np.ndarray, detune: float = 0.0) -> np.ndarray:
    """A soft (band-limited-ish) saw from a few harmonics."""
    y = np.zeros_like(t)
    for k in range(1, 12):
        y += np.sin(2 * np.pi * freq * (1 + detune) * k * t) / k * (0.85 ** k)
    return y


def stereo(left: np.ndarray, right: np.ndarray | None = None) -> np.ndarray:
    return np.stack([left, left if right is None else right], axis=1)


def pan(x: np.ndarray, p: float) -> np.ndarray:
    """p in [-1, 1]."""
    a = (p + 1) * np.pi / 4
    return stereo(x * np.cos(a), x * np.sin(a))


def write(path: Path, x: np.ndarray, peak: float = 0.89) -> None:
    if x.ndim == 1:
        x = stereo(x)
    m = np.max(np.abs(x)) or 1.0
    x = x / m * peak
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def fade(x: np.ndarray, fin: float = 0.005, fout: float = 0.05) -> np.ndarray:
    n = len(x)
    e = np.ones(n)
    a, b = int(fin * SR), int(fout * SR)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if b:
        e[n - b:] = np.linspace(1, 0, b)
    return x * (e[:, None] if x.ndim == 2 else e)


# --- sound effects -------------------------------------------------------------------------------------------------------

def whoosh(dur: float, f0: float, f1: float, seed: int, p0: float, p1: float) -> np.ndarray:
    r = np.random.default_rng(seed)
    n = int(dur * SR)
    noise = r.standard_normal(n)
    pos = np.linspace(0, 1, n)
    centers = f0 * (f1 / f0) ** (np.sin(pos * np.pi / 2) ** 1.5)
    body = sweep_filter(noise, centers, q=1.2)
    shape = np.sin(np.pi * pos) ** 1.6 * (0.4 + 0.6 * pos)
    y = body * shape
    p = p0 + (p1 - p0) * pos
    a = (p + 1) * np.pi / 4
    return fade(stereo(y * np.cos(a), y * np.sin(a)), 0.01, 0.08)


def impact() -> np.ndarray:
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    f = 38 + 90 * np.exp(-t * 18)
    boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * expdecay(n, 0.42)
    crack = fft_filter(rng.standard_normal(n), lo=900, hi=7000) * expdecay(n, 0.05) * 0.6
    body = fft_filter(rng.standard_normal(n), lo=80, hi=600) * expdecay(n, 0.18) * 0.5
    y = reverb(boom * 1.2 + crack + body, 1.8, 0.22, 3)
    return fade(stereo(y), 0.0, 0.2)


def shine() -> np.ndarray:
    """A bright, glassy price-reveal chime (inharmonic bell partials + shimmer)."""
    n = int(2.2 * SR)
    t = np.arange(n) / SR
    base = note(88)
    y = np.zeros(n)
    for ratio, amp, tau in ((1.0, 1.0, 0.9), (2.76, 0.5, 0.5), (5.4, 0.25, 0.3), (8.93, 0.12, 0.18), (1.5, 0.3, 0.7)):
        y += amp * np.sin(2 * np.pi * base * ratio * t) * expdecay(n, tau)
    shimmer = fft_filter(rng.standard_normal(n), lo=6000, hi=14000) * expdecay(n, 0.35) * 0.12
    y = reverb((y + shimmer) * env(n, 0.002, 0.0, 1.0), 2.5, 0.35, 5)
    return fade(stereo(y * 0.9, np.roll(y, 90)), 0.0, 0.3)


def tick() -> np.ndarray:
    n = int(0.06 * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * 2400 * t) * expdecay(n, 0.008) + fft_filter(rng.standard_normal(n), lo=3000) * expdecay(n, 0.004) * 0.4
    return fade(stereo(y), 0.0, 0.01)


def pop() -> np.ndarray:
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    f = 320 + 900 * np.exp(-t * 40)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * expdecay(n, 0.05)
    return fade(stereo(reverb(y, 0.6, 0.15, 9)), 0.0, 0.05)


def riser(dur: float = 1.4) -> np.ndarray:
    n = int(dur * SR)
    pos = np.linspace(0, 1, n)
    centers = 400 * (9000 / 400) ** pos
    noise = sweep_filter(rng.standard_normal(n), centers, q=2.0) * pos ** 2
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * np.cumsum(220 * 2 ** (pos * 2)) / SR) * pos ** 3 * 0.25
    y = noise + tone
    return fade(stereo(y, np.roll(y, 120)), 0.2, 0.02)


def boing() -> np.ndarray:
    """A small comic 'boing' for the funny tone (a pitch-bent spring)."""
    n = int(0.55 * SR)
    t = np.arange(n) / SR
    f = 220 + 140 * np.sin(2 * np.pi * 9 * t) * np.exp(-t * 4) + 160 * np.exp(-t * 6)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * expdecay(n, 0.2)
    return fade(stereo(y), 0.002, 0.05)


def record_scratch() -> np.ndarray:
    """A short rewind/stop for comedic beats (filtered noise with a falling pitch)."""
    n = int(0.45 * SR)
    pos = np.linspace(0, 1, n)
    centers = 3500 * (300 / 3500) ** pos
    y = sweep_filter(rng.standard_normal(n), centers, q=4.0) * (1 - pos) ** 0.6
    return fade(stereo(y), 0.002, 0.05)


# --- music beds ----------------------------------------------------------------------------------------------------------

def kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    f = 45 + 120 * np.exp(-t * 35)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * expdecay(n, 0.16)


def hat(n: int, tau: float = 0.025) -> np.ndarray:
    return fft_filter(rng.standard_normal(n), lo=7000) * expdecay(n, tau)


def clap(n: int) -> np.ndarray:
    y = fft_filter(rng.standard_normal(n), lo=900, hi=5000) * expdecay(n, 0.09)
    return reverb(y, 0.8, 0.2, 11)


def pluck(freq: float, n: int, seed: int = 0) -> np.ndarray:
    """Karplus-Strong string (a plucked, slightly woody tone)."""
    r = np.random.default_rng(seed)
    period = max(2, int(SR / freq))
    buf = r.uniform(-1, 1, period)
    out = np.zeros(n)
    for i in range(0, n, period):
        k = min(period, n - i)
        out[i:i + k] = buf[:k]
        buf = 0.497 * (buf + np.roll(buf, 1))
    return out * expdecay(n, 0.6)


def place(track: np.ndarray, sound: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR)
    if i >= len(track):
        return
    k = min(len(sound), len(track) - i)
    if track.ndim == 2 and sound.ndim == 1:
        track[i:i + k] += sound[:k, None] * gain
    else:
        track[i:i + k] += sound[:k] * gain


CHORDS = {  # midi roots + qualities, one chord per bar
    "minor": [(57, "m"), (53, ""), (48, ""), (55, "")],     # Am F C G
    "major": [(48, ""), (55, ""), (57, "m"), (53, "")],     # C G Am F
    "dark": [(52, "m"), (48, ""), (55, ""), (50, "")],      # Em C G D
}


def chord_notes(root: int, q: str) -> list[int]:
    return [root, root + (3 if q == "m" else 4), root + 7, root + 12]


def bed(name: str, bpm: float, bars: int, chords: str, drums: str, lead: str, seed: int) -> dict[str, float]:
    beat = 60.0 / bpm
    bar = beat * 4
    total = bar * bars
    n = int(total * SR) + SR
    mix = np.zeros((n, 2))
    prog = CHORDS[chords]
    for b in range(bars):
        root, q = prog[b % len(prog)]
        notes = chord_notes(root, q)
        t0 = b * bar
        # pad: soft detuned saws, low-passed, slow attack (the bed's warmth)
        m = int(bar * SR) + int(0.6 * SR)
        tt = np.arange(m) / SR
        pad = sum(saw(note(x), tt, d) for x in notes for d in (-0.003, 0.003))
        pad = fft_filter(pad, lo=120, hi=1800 if drums != "none" else 1300) * env(m, 0.5, 0.0, 1.0, 0.6) * 0.06
        place(mix, pan(pad, -0.25 if b % 2 else 0.25) * 1.0, t0)
        # bass on the root
        if drums != "none":
            for k in range(4 if drums == "four" else 2):
                bn = int(beat * (1 if drums == "four" else 2) * SR * 0.9)
                bt = np.arange(bn) / SR
                bass = np.sin(2 * np.pi * note(root - 12) * bt) + 0.3 * np.sin(2 * np.pi * note(root - 12) * 2 * bt)
                place(mix, stereo(bass * env(bn, 0.01, 0.0, 1.0, 0.08) * 0.28), t0 + k * beat * (1 if drums == "four" else 2))
        # lead / arpeggio
        if lead == "arp":
            for k in range(8):
                nn = notes[(k * 3) % 4] + 12
                s = pluck(note(nn), int(beat * 0.6 * SR), seed + b * 8 + k)
                place(mix, pan(s * 0.18, (-0.5 + (k % 4) / 3)), t0 + k * beat / 2)
        elif lead == "pluck":
            pattern = [0, 0.75, 1.5, 2, 3, 3.5]
            for k, off in enumerate(pattern):
                nn = notes[k % 4] + 12
                s = pluck(note(nn), int(beat * 0.8 * SR), seed + b * 6 + k)
                place(mix, pan(s * 0.22, 0.3 if k % 2 else -0.3), t0 + off * beat)
        elif lead == "bell":
            if b % 2 == 0:
                bn = int(bar * 2 * SR)
                bt = np.arange(bn) / SR
                bell = sum(a * np.sin(2 * np.pi * note(notes[2] + 24) * r * bt) * expdecay(bn, tau)
                           for r, a, tau in ((1.0, 1.0, 1.2), (2.76, 0.3, 0.6), (5.4, 0.1, 0.3)))
                place(mix, pan(bell * 0.05, 0.4), t0 + beat)
        # drums
        if drums in ("four", "half"):
            kn = int(0.35 * SR)
            for k in range(4):
                if drums == "four" or k in (0, 2):
                    place(mix, stereo(kick(kn) * (0.55 if drums == "four" else 0.4)), t0 + k * beat)
                if drums == "four" and k in (1, 3):
                    place(mix, pan(clap(int(0.4 * SR)) * 0.16, 0.05), t0 + k * beat)
            for k in range(8):
                place(mix, pan(hat(int(0.08 * SR), 0.02 if k % 2 else 0.035) * (0.05 if k % 2 else 0.08), 0.35), t0 + k * beat / 2)
        elif drums == "soft":
            kn = int(0.5 * SR)
            place(mix, stereo(fft_filter(kick(kn), hi=200) * 0.35), t0)
            place(mix, stereo(fft_filter(kick(kn), hi=200) * 0.22), t0 + 2.5 * beat)
    mix = reverb(mix[:, 0], 2.0, 0.18, seed)[:, None] * np.array([1.0, 0.0]) + reverb(mix[:, 1], 2.0, 0.18, seed + 1)[:, None] * np.array([0.0, 1.0])
    mix = mix[: int(total * SR)]
    # loop-friendly: a short crossfade of the tail into the head
    xf = int(0.5 * SR)
    mix[:xf] = mix[:xf] * np.linspace(0, 1, xf)[:, None] + mix[-xf:] * np.linspace(1, 0, xf)[:, None]
    mix = mix[:-xf]
    write(OUT / "music" / f"{name}.wav", mix, 0.7)
    return {"bpm": bpm, "seconds": round(len(mix) / SR, 3), "offset": 0.0}


def main() -> None:
    sfx = OUT / "sfx"
    write(sfx / "whoosh1.wav", whoosh(0.55, 300, 5000, 1, -0.6, 0.6))
    write(sfx / "whoosh2.wav", whoosh(0.42, 600, 7000, 2, 0.5, -0.5))
    write(sfx / "whoosh3.wav", whoosh(0.7, 200, 2500, 3, -0.2, 0.3), 0.8)
    write(sfx / "impact.wav", impact())
    write(sfx / "shine.wav", shine(), 0.8)
    write(sfx / "tick.wav", tick(), 0.6)
    write(sfx / "pop.wav", pop(), 0.8)
    write(sfx / "riser.wav", riser(), 0.75)
    write(sfx / "boing.wav", boing(), 0.75)
    write(sfx / "scratch.wav", record_scratch(), 0.7)
    beds = {
        "documentary": bed("documentary", 84, 24, "minor", "soft", "bell", 21),
        "viral": bed("viral", 124, 40, "dark", "four", "arp", 31),
        "playful": bed("playful", 108, 32, "major", "half", "pluck", 41),
        "market": bed("market", 100, 32, "minor", "half", "arp", 51),
    }
    (OUT / "music" / "beds.json").write_text(json.dumps(beds, indent=1), encoding="utf-8")
    print(json.dumps(beds))


if __name__ == "__main__":
    main()
