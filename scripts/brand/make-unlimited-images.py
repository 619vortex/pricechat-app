"""Unlimited / Shadowless card pictures for the Pack Giveaway.

The public card scans for the vintage sets (Scrydex, TCGdex, Pokémon TCG API) are the 1st Edition prints, and for
Jungle → Neo the only visual difference from the Unlimited print is the small black "Edition 1" stamp left of the
Pokémon's description (Base Set Shadowless has no stamp either). This makes the Unlimited / Shadowless picture of each
card from its scan by removing exactly that stamp: it finds the round black stamp by shape (where each card layout
prints it: Pokémon, Trainer, basic Energy), then replaces only the dark ink
pixels around it with the frame colour (a gradient sampled just outside, with matching grain). Nothing else changes.

    backend/.venv/Scripts/python scripts/brand/make-unlimited-images.py <catalog.db>
        ->  frontend/public/cards/u/<hash>.webp  and  backend/app/members/unlimited_images.json (scan URL -> picture)

A card whose stamp isn't found (e.g. a layout without one) is left out of the map: it keeps its original scan.
"""

import hashlib
import io
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "public" / "cards" / "u"
MAP = ROOT / "backend" / "app" / "members" / "unlimited_images.json"
SETS = ["Base Set", "Jungle", "Fossil", "Team Rocket", "Gym Heroes", "Gym Challenge", "Neo Genesis", "Neo Discovery", "Neo Revelation", "Neo Destiny"]
WIDTH = 600


# where the stamp is printed (fractions of the card), by card layout: tried in this order
BANDS = {
    "pokemon": (0.025, 0.10, 0.535, 0.585),  # left end of the description bar (measured: x 7.2-7.7%, y 55.9-56.1%; Shining cards further left)
    "trainer": (0.15, 0.26, 0.88, 0.94),  # in a Trainer's text box, bottom left, next to the last bullet (measured ~21%, 91%)
    "energy": (0.78, 0.97, 0.03, 0.15),  # top-right of a basic Energy card
}


def find_stamp(im: Image.Image, energy: bool = False, contrast: float = 70) -> tuple[float, float, float] | None:
    """The stamp, found by shape: a dark disc (radius ~1.7% of the card width) with lighter surroundings on its lower
    right (its EDITION lettering arcs over the top left). Returns (cx, cy, r) in pixels, or None."""
    L = np.asarray(im.convert("L"), dtype=np.float32)
    h, w = L.shape
    best = None
    for kind in (("energy",) if energy else ("pokemon", "trainer")):
        bx0, bx1, by0, by1 = BANDS[kind]
        xa, xb, ya, yb = int(bx0 * w), int(bx1 * w), int(by0 * h), int(by1 * h)
        for r in (0.015 * w, 0.0167 * w, 0.0185 * w):
            R = int(np.ceil(1.75 * r))
            xa2, xb2, ya2, yb2 = max(xa, R), min(xb, w - R - 1), max(ya, R), min(yb, h - R - 1)
            if xb2 <= xa2 or yb2 <= ya2:
                continue
            inner = np.zeros((yb2 - ya2, xb2 - xa2), np.float32)
            outer = np.zeros_like(inner)
            ni = no = 0
            for dy in range(-R, R + 1):
                for dx in range(-R, R + 1):
                    d = (dx * dx + dy * dy) ** 0.5
                    if 0.6 * r <= d <= 0.95 * r:
                        inner += L[ya2 + dy:yb2 + dy, xa2 + dx:xb2 + dx]
                        ni += 1
                    elif 1.3 * r <= d <= 1.7 * r and dx + dy > 0:
                        outer += L[ya2 + dy:yb2 + dy, xa2 + dx:xb2 + dx]
                        no += 1
            inner /= ni
            outer /= no
            score = np.where(inner < (85 if contrast >= 70 else 45), outer - inner, -1)
            iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
            if score[iy, ix] > contrast and (best is None or score[iy, ix] > best[0]):
                best = (float(score[iy, ix]), (xa2 + ix, ya2 + iy, r))
        if best:
            break  # found in this layout's place: don't look elsewhere
    return best[1] if best else None


def destamp(im: Image.Image, energy: bool = False) -> Image.Image | None:
    im = im.convert("RGB")
    found = find_stamp(im, energy)
    if not found and not energy:
        # Dark-type cards: a black stamp on a near-black frame. Only in the Pokémon band (tight), with less contrast
        found = find_stamp(im, False, contrast=30)
        if found and found[1] > 0.7 * im.height:
            found = None  # the relaxed pass never looks at the Trainer place
    if not found:
        return None
    cx, cy, r = found
    w, h = im.size
    # the stamp zone: the disc + the EDITION lettering arcing over its top left
    X0, X1, Y0, Y1 = int(cx - 2.55 * r), int(cx + 2.5 * r), int(cy - 2.65 * r), int(cy + 1.45 * r)
    m = 4
    ax0, ay0, ax1, ay1 = max(0, X0 - m), max(0, Y0 - m), min(w, X1 + m + 1), min(h, Y1 + m + 1)
    A = np.asarray(im.crop((ax0, ay0, ax1, ay1)), dtype=np.float32).copy()
    lw = np.array([0.3, 0.59, 0.11], dtype=np.float32)
    L = A @ lw
    yy, xx = np.mgrid[ay0:ay1, ax0:ax1]
    zone = (xx >= X0) & (xx <= X1) & (yy >= Y0) & (yy <= Y1)
    disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= (1.2 * r) ** 2  # the whole disc, its white "1" included
    ref = float(np.median(L[zone & ~disc]))  # the frame's brightness here
    ink = zone & (L < ref - 32)  # the lettering: clearly darker than the frame
    mask = np.asarray(Image.fromarray(((ink | disc) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0  # + the soft rim
    mask &= zone & ~(~disc & (L > ref + 30))  # outside the disc, never anything lighter than the frame (gold border, bar)
    # rebuild the hidden pixels from the frame around them: diffusion inward from the edge (keeps colour and gradients)
    out = A.copy()
    known = ~mask
    fill = np.where(known[..., None], out, np.mean(out[known & zone], axis=0) if (known & zone).any() else out.mean(axis=(0, 1)))
    for _ in range(400):
        nb = (np.roll(fill, 1, 0) + np.roll(fill, -1, 0) + np.roll(fill, 1, 1) + np.roll(fill, -1, 1)) / 4
        fill = np.where(mask[..., None], nb, out)
    grain = float(min(8.0, np.std(L[zone & ~mask] - np.median(L[zone & ~mask])))) if (zone & ~mask).any() else 3.0
    rng = np.random.default_rng(int(cx * 1000 + cy))
    noise = rng.normal(0.0, grain * 0.8, L.shape).astype(np.float32)[..., None]
    out[mask] = (fill + noise)[mask]
    im.paste(Image.fromarray(out.clip(0, 255).astype(np.uint8)), (ax0, ay0))
    return im


def one(job: tuple[str, bool]) -> tuple[str, str | None]:
    url, energy = job
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".webp"
    dest = OUT / name
    if dest.exists():
        return url, f"/cards/u/{name}"
    try:
        raw = httpx.get(url, timeout=40, follow_redirects=True).content
        im = Image.open(io.BytesIO(raw))
        if im.width != WIDTH:
            im = im.convert("RGB").resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
        clean = destamp(im, energy)
    except Exception as exc:  # noqa: BLE001 - one bad image never stops the batch
        print("failed", url, type(exc).__name__)
        return url, None
    if clean is None:
        return url, None
    clean.save(dest, "WEBP", quality=80, method=6)
    return url, f"/cards/u/{name}"


def main() -> None:
    db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    q = (f"select image_url, max(name like '%Energy%' and coalesce(rarity, '') in ('', 'Common')) from pokemon_cards where language='English' "
         f"and image_url is not null and set_name in ({','.join('?' * len(SETS))}) group by image_url")
    jobs = [(r[0], bool(r[1])) for r in db.execute(q, SETS)]
    urls = [u for u, _ in jobs]
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as pool:
        done = list(pool.map(one, jobs))
    mapping = {u: p for u, p in done if p}
    MAP.write_text(json.dumps(dict(sorted(mapping.items())), indent=0), encoding="utf-8")
    print(f"{len(mapping)} of {len(urls)} scans cleaned; {len(urls) - len(mapping)} kept as they are")


if __name__ == "__main__":
    main()
