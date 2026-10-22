"""Pack Giveaway sounds: layered and synthesized here (no samples, nothing fetched at runtime).

    backend/.venv/Scripts/python scripts/brand/make-pack-sounds.py
        ->  frontend/public/website/sounds/{rip,swish,rise,shine,chase,first,coin,cash,listed,nope}.wav

Mono 32 kHz 16-bit WAV. Each sound is built from a few layers and placed in a small room (a Schroeder reverb), so it
sounds like a thing in a space rather than a beep:
  rip    foil crinkles that build up, a grainy sweeping tear, and the pack giving way (a soft thump)
  swish  a card sliding out of the pack and flicking over (paper slide + a crisp snap)
  rise   anticipation before the hit card: a rising, trembling whoosh
  shine  a holo: a cascade of glassy bell chimes with a shimmer
  chase  the chase card: an impact, a bright brass-like major chord, a rising sparkle run and a cymbal swell
  first  a 1st Edition / Shadowless pack: a deep shimmering gong with a warm pad underneath
  coin   marketplace buy: a bright "cha-ching"         cash    marketplace sell: a register drawer + coins
  listed marketplace list: a card slap + a chime       nope    refused: a soft low double thud
The quieter interface sounds (xp, tick, levelup, streak) come from make-sounds.py.
"""

from pathlib import Path
import wave

import numpy as np

RATE = 32000
OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "website" / "sounds"
rng = np.random.default_rng(7)


def t_axis(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * RATE)) / RATE


def onepole_lp(x: np.ndarray, cutoff_hz: np.ndarray | float) -> np.ndarray:
    c = np.broadcast_to(1 - np.exp(-2 * np.pi * np.asarray(cutoff_hz, dtype=float) / RATE), x.shape)
    y, acc = np.empty_like(x), 0.0
    for i in range(len(x)):
        acc += c[i] * (x[i] - acc)
        y[i] = acc
    return y


def bandpass(x: np.ndarray, center: np.ndarray | float, q: float = 2.0) -> np.ndarray:
    """A resonant band-pass (RBJ biquad) whose centre can move sample by sample."""
    f = np.broadcast_to(np.asarray(center, dtype=float), x.shape)
    y = np.empty_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(len(x)):
        w = 2 * np.pi * min(f[i], RATE * 0.45) / RATE
        alpha = np.sin(w) / (2 * q)
        a0 = 1 + alpha
        b0, b2 = alpha / a0, -alpha / a0
        a1, a2 = -2 * np.cos(w) / a0, (1 - alpha) / a0
        out = b0 * x[i] + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, x[i], y1, out
        y[i] = out
    return y


def highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    return x - onepole_lp(x, cutoff)


def reverb(x: np.ndarray, mix: float, size: float = 1.0, tail: float = 0.6) -> np.ndarray:
    """Schroeder reverb: four combs in parallel, two all-passes in series. size scales the room, tail the decay."""
    pad = np.concatenate([x, np.zeros(int(tail * RATE))])
    wet = np.zeros_like(pad)
    for d_ms, g in ((29.7, 0.80), (37.1, 0.78), (41.1, 0.76), (43.7, 0.74)):
        d = int(d_ms * size * RATE / 1000)
        buf = np.zeros_like(pad)
        for i in range(len(pad)):
            buf[i] = pad[i] + (g * buf[i - d] if i >= d else 0.0)
        wet += buf
    for d_ms, g in ((5.0, 0.7), (1.7, 0.7)):
        d = int(d_ms * RATE / 1000)
        out = np.zeros_like(wet)
        for i in range(len(wet)):
            xd = wet[i - d] if i >= d else 0.0
            yd = out[i - d] if i >= d else 0.0
            out[i] = -g * wet[i] + xd + g * yd
        wet = out
    wet = onepole_lp(wet, 5200.0) / 4
    return (1 - mix) * pad + mix * wet


def env_ad(t: np.ndarray, attack: float, decay: float) -> np.ndarray:
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-np.maximum(0.0, t - attack) / decay)


def place(buf: np.ndarray, sound: np.ndarray, at: float, gain: float = 1.0) -> None:
    s = int(at * RATE)
    n = min(len(sound), len(buf) - s)
    if n > 0:
        buf[s:s + n] += gain * sound[:n]


def bell(freq: float, seconds: float, decay: float, bright: float = 1.0) -> np.ndarray:
    """A glassy bell: inharmonic partials (struck-bar ratios), the high ones fading faster."""
    t = t_axis(seconds)
    out = np.zeros_like(t)
    for ratio, amp, dk in ((1.0, 1.0, 1.0), (2.76, 0.45 * bright, 0.55), (5.40, 0.22 * bright, 0.32), (8.93, 0.10 * bright, 0.2)):
        if freq * ratio < RATE * 0.45:
            out += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t / (decay * dk))
    return out * np.minimum(1.0, t / 0.002)


def brass(freq: float, seconds: float, attack: float = 0.06, decay: float = 0.9) -> np.ndarray:
    """A bright brass-like tone: a band-limited saw with a little vibrato and a swelling brightness."""
    t = t_axis(seconds)
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t) * np.minimum(1.0, t / 0.3)
    phase = 2 * np.pi * freq * np.cumsum(vib) / RATE
    bright = np.minimum(1.0, 0.35 + t / 0.25)
    out = np.zeros_like(t)
    for n in range(1, 12):
        if freq * n > RATE * 0.45:
            break
        out += (bright ** (n * 0.5)) * np.sin(n * phase) / n
    return out * env_ad(t, attack, decay)


def save(name: str, x: np.ndarray, peak: float = 0.9) -> None:
    x = x - np.mean(x)
    fade = int(0.03 * RATE)
    x[-fade:] *= np.linspace(1, 0, fade)
    x *= peak / max(1e-9, np.max(np.abs(x)))
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def rip() -> np.ndarray:
    buf = np.zeros(int(0.95 * RATE))
    # foil crinkles: tiny resonant clicks that get denser and louder as hands grip the pack
    for k in range(70):
        at = 0.34 * (k / 70) ** 0.7 + rng.uniform(-0.01, 0.01)
        n = int(rng.uniform(0.003, 0.009) * RATE)
        click = bandpass(rng.uniform(-1, 1, n) * np.exp(-np.arange(n) / (0.0015 * RATE)), rng.uniform(2500, 7000), q=4)
        place(buf, click, max(0.0, at), gain=rng.uniform(0.3, 1.0) * (0.4 + 0.6 * k / 70))
    # the tear: grainy noise through a band-pass sweeping up, roughened by a fast random "fibre" modulation
    secs = 0.42
    t = t_axis(secs)
    grain = np.abs(onepole_lp(rng.uniform(-1, 1, len(t)), 90.0)) * 6 + 0.25
    tear = bandpass(rng.uniform(-1, 1, len(t)), 900 + 2800 * (t / secs) ** 0.6, q=1.3) * grain
    tear *= np.minimum(1.0, t / 0.02) * np.exp(-t / 0.2) * (1 + 0.6 * np.sin(2 * np.pi * 38 * t))
    place(buf, tear, 0.3, gain=2.4)
    # the pack gives way: a soft paper thump
    th = t_axis(0.16)
    thump = np.sin(2 * np.pi * (140 - 300 * th) * th) * np.exp(-th / 0.045)
    place(buf, thump, 0.62, gain=0.9)
    place(buf, highpass(rng.uniform(-1, 1, int(0.02 * RATE)), 2000) * np.exp(-np.arange(int(0.02 * RATE)) / (0.004 * RATE)), 0.62, gain=0.6)
    return reverb(buf, 0.18, size=0.7, tail=0.25)


def swish() -> np.ndarray:
    secs = 0.2
    t = t_axis(secs)
    slide = bandpass(rng.uniform(-1, 1, len(t)), 1800 + 3200 * t / secs, q=0.9) * np.sin(np.pi * np.minimum(1.0, t / 0.17)) ** 2
    buf = np.zeros(int(0.26 * RATE))
    place(buf, slide, 0.0, gain=1.0)
    n = int(0.012 * RATE)
    snap = bandpass(rng.uniform(-1, 1, n), 3200, q=3) * np.exp(-np.arange(n) / (0.0022 * RATE))
    place(buf, snap, 0.16, gain=1.6)
    return reverb(buf, 0.12, size=0.5, tail=0.12)


def rise() -> np.ndarray:
    secs = 0.7
    t = t_axis(secs)
    p = t / secs
    air = bandpass(rng.uniform(-1, 1, len(t)), 500 + 5000 * p ** 1.6, q=0.8) * p ** 2
    trem = 1 + 0.35 * np.sin(2 * np.pi * np.cumsum(6 + 18 * p) / RATE)
    tone_phase = 2 * np.pi * np.cumsum(220 + 1400 * p ** 2) / RATE
    tone = (np.sin(tone_phase) + 0.3 * np.sin(2 * tone_phase)) * p ** 2.2 * 0.35
    return reverb((air + tone) * trem, 0.2, size=0.8, tail=0.2)


def shine() -> np.ndarray:
    buf = np.zeros(int(1.0 * RATE))
    notes = [1318.5, 1661.2, 1975.5, 2217.5, 2637.0, 3322.4, 3951.1]  # E major pentatonic, upwards
    for i, f in enumerate(notes):
        place(buf, bell(f, 0.8, 0.35), i * 0.045, gain=0.55 - i * 0.04)
    t = t_axis(0.9)
    shimmer = highpass(rng.uniform(-1, 1, len(t)), 6000) * env_ad(t, 0.12, 0.25) * (1 + np.sin(2 * np.pi * 13 * t))
    place(buf, shimmer, 0.05, gain=0.12)
    return reverb(buf, 0.32, size=1.0, tail=0.5)


def chase() -> np.ndarray:
    buf = np.zeros(int(2.3 * RATE))
    t = t_axis(0.6)
    boom = np.sin(2 * np.pi * np.cumsum(90 * np.exp(-t / 0.15) + 42) / RATE) * np.exp(-t / 0.22)
    place(buf, boom, 0.0, gain=1.0)
    place(buf, bandpass(rng.uniform(-1, 1, len(t)), 1800, q=0.7) * np.exp(-t / 0.05), 0.0, gain=0.8)
    for f in (329.6, 415.3, 493.9, 659.3):  # E major: E4 G#4 B4 E5
        place(buf, brass(f, 1.7, attack=0.05, decay=0.8), 0.04, gain=0.32)
    for i, f in enumerate((1318.5, 1661.2, 1975.5, 2637.0, 3322.4, 3951.1, 5274.0)):  # the sparkle run upwards
        place(buf, bell(f, 0.7, 0.3), 0.3 + i * 0.06, gain=0.28)
    ct = t_axis(1.6)
    cymbal = highpass(rng.uniform(-1, 1, len(ct)), 5000) * np.minimum(1.0, ct / 0.5) * np.exp(-np.maximum(0, ct - 0.5) / 0.35)
    place(buf, cymbal, 0.1, gain=0.22)
    return reverb(buf, 0.3, size=1.3, tail=0.9)


def first() -> np.ndarray:
    buf = np.zeros(int(2.2 * RATE))
    t = t_axis(2.2)
    gong = np.zeros_like(t)
    for ratio, amp, dk in ((1.0, 1.0, 1.4), (1.99, 0.5, 1.0), (2.76, 0.4, 0.8), (4.07, 0.25, 0.6), (5.43, 0.18, 0.45)):
        wobble = 1 + 0.002 * np.sin(2 * np.pi * 3.1 * t * ratio)
        gong += amp * np.sin(2 * np.pi * 98 * ratio * np.cumsum(wobble) / RATE) * np.exp(-t / dk)
    place(buf, gong * np.minimum(1.0, t / 0.004), 0.0, gain=0.9)
    pad = sum(np.sin(2 * np.pi * f * d * t) for f in (164.8, 246.9, 329.6) for d in (0.997, 1.003))
    place(buf, pad * np.minimum(1.0, t / 0.6) * np.exp(-np.maximum(0, t - 0.8) / 0.7), 0.1, gain=0.12)
    for i, f in enumerate((2637.0, 3322.4, 3951.1, 5274.0)):
        place(buf, bell(f, 0.8, 0.4, bright=0.6), 0.35 + i * 0.09, gain=0.18)
    return reverb(buf, 0.35, size=1.4, tail=1.0)


def coin() -> np.ndarray:
    """Buy: a bright coin "cha-ching": two metallic strikes (inharmonic, fast decay) and a little shimmer."""
    buf = np.zeros(int(0.9 * RATE))
    for at, f, g in ((0.0, 2093.0, 0.8), (0.09, 3136.0, 1.0)):  # C7 then G7
        place(buf, bell(f, 0.6, 0.18, bright=1.4), at, gain=g)
    t = t_axis(0.5)
    place(buf, highpass(rng.uniform(-1, 1, len(t)), 7000) * env_ad(t, 0.005, 0.08), 0.09, gain=0.25)
    return reverb(buf, 0.22, size=0.6, tail=0.35)


def cash() -> np.ndarray:
    """Sell: a cash-register drawer (a mechanical clack + slide) and a coin spill into the drawer."""
    buf = np.zeros(int(1.1 * RATE))
    n = int(0.03 * RATE)
    clack = bandpass(rng.uniform(-1, 1, n), 1400, q=2) * np.exp(-np.arange(n) / (0.006 * RATE))
    place(buf, clack, 0.0, gain=1.6)
    t = t_axis(0.22)
    slide = bandpass(rng.uniform(-1, 1, len(t)), 700 + 900 * t / 0.22, q=1.2) * np.sin(np.pi * t / 0.22)
    place(buf, slide, 0.02, gain=0.7)
    place(buf, bell(1568.0, 0.7, 0.25), 0.18, gain=0.7)  # the bell: ding
    for k in range(9):  # coins landing
        place(buf, bell(rng.uniform(2600, 5200), 0.12, 0.03, bright=1.6), 0.3 + k * 0.045 + rng.uniform(0, 0.02), gain=rng.uniform(0.15, 0.35))
    return reverb(buf, 0.2, size=0.7, tail=0.4)


def listed() -> np.ndarray:
    """List: a card slapped onto the table and a soft confirming two-note chime."""
    buf = np.zeros(int(0.8 * RATE))
    n = int(0.05 * RATE)
    slap = bandpass(rng.uniform(-1, 1, n), 1100, q=0.9) * np.exp(-np.arange(n) / (0.008 * RATE))
    place(buf, slap, 0.0, gain=1.5)
    th = t_axis(0.12)
    place(buf, np.sin(2 * np.pi * 120 * th) * np.exp(-th / 0.03), 0.0, gain=0.6)
    place(buf, bell(1318.5, 0.5, 0.2, bright=0.5), 0.08, gain=0.5)
    place(buf, bell(1975.5, 0.5, 0.22, bright=0.5), 0.16, gain=0.45)
    return reverb(buf, 0.2, size=0.6, tail=0.3)


def nope() -> np.ndarray:
    """Refused: a soft low double thud (not alarming)."""
    buf = np.zeros(int(0.45 * RATE))
    t = t_axis(0.16)
    th = np.sin(2 * np.pi * (180 - 200 * t) * t) * np.exp(-t / 0.05)
    place(buf, th, 0.0, gain=0.9)
    place(buf, th, 0.12, gain=0.7)
    return reverb(buf, 0.12, size=0.5, tail=0.15)


for name, make in (("rip", rip), ("swish", swish), ("rise", rise), ("shine", shine), ("chase", chase), ("first", first),
                   ("coin", coin), ("cash", cash), ("listed", listed), ("nope", nope)):
    save(name, make())
print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.wav"))))
