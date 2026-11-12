"""News Desk picture backgrounds on this PC's GPU (backend: app/newsdesk/render_queue.py).

Runs on its own, beside the Brand Manager worker, with the GPU Python environment (the one with PyTorch and diffusers):

    <gpu python> -m pricechat_worker.newsdesk_images            (reads worker.env: PRICECHAT_SERVER, PRICECHAT_WORKER_TOKEN)

It only ever calls out (HTTPS to the server, the worker's own token); nothing on this PC is reachable from outside. Loop: claim a job,
render the 16:9 picture with the chosen open-weight model, check it, upload it, repeat. The model stays loaded while there is work and is
unloaded after PCW_IMAGE_IDLE_UNLOAD seconds idle, so the video jobs can have the GPU. Network errors back off; a failed render is
reported (the server retries it with a new seed, or the post goes out with the desk's own drawn visual).

Models (all Apache-2.0, commercial use allowed): klein = black-forest-labs/FLUX.2-klein-4B, zimage = Tongyi-MAI/Z-Image-Turbo,
qwen = Qwen/Qwen-Image-2512, qwen_edit = Qwen/Qwen-Image-Edit-2511 (photo editing only). One model is held on the GPU at a time.

Job contract (fields the server sends; a job without "task" is a plain generate job, exactly as in 1.1):
  task      "generate" (default): a text-free background from "prompt" (+ "negative", "width", "height", "seed", "model").
            "edit": work from a licensed photo: "image_b64" (JPEG/PNG bytes, base64) or "image_url" (https), "edit", "prompt" (the scene,
            optional), "seed", "negative" (optional).
  edit      "extend":     fit the photo into the 1536x864 canvas, let the edit model paint only the empty margins, then paste the ORIGINAL
                          (scaled) pixels back over their region, so the photo's content is untouched. Refused when the margins would be
                          more than MAX_AI_AREA_PCT of the canvas. Lettering is looked for in the painted margins only. Optional
                          "fill" (white | grey | blur, default PCW_EXTEND_FILL): what the model sees in the margins; if its output margins
                          barely differ from that input (it "kept" the bars), the job fails instead of shipping bars.
            "illustrate": a clearly-AI editorial illustration derived from the photo; the server labels it "AI illustration".
  model     authoritative when it names a known model; an edit job with no (or an unknown) model uses qwen_edit; a generate job with an
            unknown model uses the default (--model). A generate job naming qwen_edit, or an edit job naming a generator, fails clearly.
Every picture passes cheap local checks (exposure, contrast, sharpness) and EasyOCR (the model must never draw lettering: re-rendered with
a new seed up to PCW_IMAGE_TRIES times) before upload. Result fields: task, model, seconds (render), total_seconds, steps, seed, gpu,
vram_gb (peak this job), quality {mean, spread, sharpness}, ocr {checked, found, area}, tries; extend adds edit, preserved_box (normalised
x0,y0,x1,y1), preserved_max_diff (always 0, else the job fails), ai_area_pct, scale, fill, margin_change; illustrate adds edit, ai_illustration: true.
  copy_request  optional {"system", "user"}: BEFORE the picture, the post's copy is drafted on the local Ollama (PCW_COPY_MODEL, default
            gpt-oss:20b, JSON mode), fact-checked against the user message, rewritten once if the check finds problems, and checked again;
            result "copy": {draft, ok, problems, rewrites, model, seconds} or {draft: null, error}. Copy trouble never fails the job.
"""

import argparse
import base64
import io
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx

log = logging.getLogger("newsdesk_images")
ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.2.1"
STOP = False
CANVAS = (1536, 864)
MAX_AI_AREA_PCT = 40.0  # extend: more invented scenery than this and the picture is no longer the photo
MAX_PHOTO_BYTES = 25_000_000
FILL = (127, 127, 127)  # blanks the real photo for the margins-only text check
EXTEND_FILL = os.environ.get("PCW_EXTEND_FILL", "blur")  # margins as the model sees them: blur (bench: grey and white came back unpainted)
MIN_MARGIN_CHANGE = 5.0  # mean abs margin change, model input vs output. Bench: unpainted grey/white 1-1.5; painted blur 12-79 (calm sea/sky lowest)
SEAM = 12  # px of the PAINTED margin blended toward the photo's edge pixels (the photo itself is never blended)

EDIT_DEFAULTS = {"num_inference_steps": 40, "true_cfg_scale": 4.0, "guidance_scale": 1.0, "negative_prompt": " "}  # 2511 model card
MODELS = {
    "klein": ("black-forest-labs/FLUX.2-klein-4B", {"num_inference_steps": 4, "guidance_scale": 1.0}),
    "zimage": ("Tongyi-MAI/Z-Image-Turbo", {"num_inference_steps": 9, "guidance_scale": 0.0}),
    "qwen": ("Qwen/Qwen-Image-2512", {"num_inference_steps": 30, "true_cfg_scale": 4.0}),
    "qwen_edit": ("Qwen/Qwen-Image-Edit-2511", EDIT_DEFAULTS),
}
EDIT_MODELS = ("qwen_edit",)
EXTEND_PROMPT = ("Outpaint this photograph: {what}{sides} by continuing the existing scene naturally{scene}. "
                 "Keep the original photograph in the centre exactly as it is. Match its lighting, perspective, colour and film grain. "
                 "Photorealistic. No text, no letters, no logos, no watermark, no frame, no borders.")
EXTEND_WHAT = {"white": "replace the blank white border areas", "grey": "replace the flat grey border areas",
               "blur": "replace the blurred, smeared border areas with sharp detailed scenery"}
ILLUSTRATE_PROMPT = ("Repaint this photo as an editorial illustration{scene}: painterly brush strokes, simplified shapes, muted magazine "
                     "palette, clearly a painting, not a photograph. No text, no letters, no logos, no watermark.")
NEG_EDIT = "text, letters, words, numbers, watermark, logo, caption, signature, border, frame, grey bars, blurry, lowres, deformed"

# local quality gate (mirrors the server's app/newsdesk/render_queue.quality) plus a sharpness floor
MEAN_RANGE = (18.0, 225.0)
MIN_SPREAD = 22.0
MIN_SHARPNESS = 15.0  # variance of the Laplacian on a 768-wide grey copy. 2026-11-11 calibration on 33 bench renders: lowest 28
#                       (shallow depth of field), median 267; the same renders under a 3 px Gaussian blur: 3-31, mostly < 15


class JobError(Exception):
    """A job that can't be done as asked: reported to the server with this message."""


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


# ---------------------------------------------------------------- picture maths (PIL only, no GPU; tests/test_newsdesk_images.py)

def fit_box(size: tuple[int, int], canvas: tuple[int, int] = CANVAS) -> tuple[int, int, int, int]:
    """Where a photo of this size lands when scaled to fit the canvas (full height or full width), centred: x0, y0, x1, y1 in pixels."""
    w, h = size
    cw, ch = canvas
    s = min(cw / w, ch / h)
    sw, sh = min(cw, round(w * s)), min(ch, round(h * s))
    x0, y0 = (cw - sw) // 2, (ch - sh) // 2
    return x0, y0, x0 + sw, y0 + sh


def ai_area_pct(box: tuple[int, int, int, int], canvas: tuple[int, int] = CANVAS) -> float:
    x0, y0, x1, y1 = box
    return round(100.0 * (1 - (x1 - x0) * (y1 - y0) / (canvas[0] * canvas[1])), 2)


def norm_box(box: tuple[int, int, int, int], canvas: tuple[int, int] = CANVAS) -> list[float]:
    return [round(box[0] / canvas[0], 4), round(box[1] / canvas[1], 4), round(box[2] / canvas[0], 4), round(box[3] / canvas[1], 4)]


def margin_sides(box: tuple[int, int, int, int], canvas: tuple[int, int] = CANVAS) -> list[str]:
    x0, y0, x1, y1 = box
    return [s for s, gap in (("left", x0), ("right", canvas[0] - x1), ("top", y0), ("bottom", canvas[1] - y1)) if gap > 0]


def outpaint_input(photo: Any, canvas: tuple[int, int] = CANVAS, fill: str = "grey") -> tuple[Any, Any, tuple[int, int, int, int]]:
    """The scaled photo, the canvas the edit model sees (photo centred; margins white, grey, or the photo's edges stretched and blurred)
    and the photo's box."""
    from PIL import Image, ImageFilter

    box = fit_box(photo.size, canvas)
    scaled = photo.convert("RGB").resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
    if fill == "blur":
        base = _edge_stretch(scaled, box, canvas).filter(ImageFilter.GaussianBlur(24))
    else:
        base = Image.new("RGB", canvas, (255, 255, 255) if fill == "white" else FILL)
    base.paste(scaled, box[:2])
    return scaled, base, box


def margin_change(painted: Any, base: Any, box: tuple[int, int, int, int]) -> float:
    """Mean abs pixel change of the margins (outside box) between the model's input canvas and its output."""
    from PIL import Image, ImageChops, ImageStat

    out = painted.convert("RGB")
    if out.size != base.size:
        out = out.resize(base.size, Image.Resampling.LANCZOS)
    mask = Image.new("L", base.size, 255)
    mask.paste(0, box)
    return round(sum(ImageStat.Stat(ImageChops.difference(out, base.convert("RGB")), mask).mean) / 3, 2)


def _edge_stretch(scaled: Any, box: tuple[int, int, int, int], canvas: tuple[int, int]) -> Any:
    """The canvas with each margin filled by the photo's own edge column/row stretched outwards (used for seam blending only)."""
    from PIL import Image

    x0, y0, x1, y1 = box
    sw, sh = scaled.size
    out = Image.new("RGB", canvas, FILL)
    if x0 > 0:
        out.paste(scaled.crop((0, 0, 1, sh)).resize((x0, sh)), (0, y0))
    if canvas[0] - x1 > 0:
        out.paste(scaled.crop((sw - 1, 0, sw, sh)).resize((canvas[0] - x1, sh)), (x1, y0))
    if y0 > 0:
        out.paste(scaled.crop((0, 0, sw, 1)).resize((sw, y0)), (x0, 0))
    if canvas[1] - y1 > 0:
        out.paste(scaled.crop((0, sh - 1, sw, sh)).resize((sw, canvas[1] - y1)), (x0, y1))
    out.paste(scaled, (x0, y0))
    return out


def paste_back(painted: Any, scaled: Any, box: tuple[int, int, int, int], canvas: tuple[int, int] = CANVAS, seam: int = SEAM) -> tuple[Any, int]:
    """The painted canvas with the original pixels pasted back exactly over the photo's box. A narrow band of the PAINTED margin next to
    the photo is blended toward the photo's edge pixels so the join is soft; the photo region itself is the original, bit for bit.
    Returns the picture and the max abs pixel difference of the photo region vs the original (0 unless something is wrong)."""
    from PIL import Image, ImageDraw

    out = painted.convert("RGB")
    if out.size != canvas:
        out = out.resize(canvas, Image.Resampling.LANCZOS)
    x0, y0, x1, y1 = box
    if seam > 0:
        mask = Image.new("L", canvas, 0)  # 255 = the stretched photo edge, 0 = the painting
        d = ImageDraw.Draw(mask)
        for i in range(seam):
            d.rectangle((x0 - 1 - i, y0 - 1 - i, x1 + i, y1 + i), outline=round(255 * (1 - (i + 1) / (seam + 1))))
        out = Image.composite(_edge_stretch(scaled, box, canvas), out, mask)
    out.paste(scaled, (x0, y0))
    return out, region_max_diff(out, scaled, box)


def region_max_diff(img: Any, ref: Any, box: tuple[int, int, int, int]) -> int:
    from PIL import ImageChops

    diff = ImageChops.difference(img.convert("RGB").crop(box), ref.convert("RGB"))
    return max(hi for _, hi in diff.getextrema())


def cover(photo: Any, canvas: tuple[int, int] = CANVAS) -> Any:
    """Scale to cover the canvas and centre-crop (illustrate: the whole picture is repainted anyway)."""
    from PIL import ImageOps

    return ImageOps.fit(photo.convert("RGB"), canvas)


def image_quality(img: Any) -> tuple[list[str], dict[str, Any]]:
    """Near-black, blown-out, flat or blurry pictures: problems (empty = fine) and the measurements. PIL only (no numpy needed)."""
    from PIL import Image, ImageFilter, ImageStat

    grey = img.convert("L")
    st = ImageStat.Stat(grey)
    mean, spread = st.mean[0], st.stddev[0]
    small = grey.resize((768, max(1, round(768 * grey.height / grey.width))), Image.Resampling.BILINEAR) if grey.width > 768 else grey
    # 4-neighbour Laplacian stored as lap/8 + 128 so it fits 8 bits without clipping: var(lap) = 64 * var(stored)
    lap = small.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=8, offset=128))
    sharp = ImageStat.Stat(lap.crop((1, 1, lap.width - 1, lap.height - 1))).var[0] * 64
    problems = []
    if mean < MEAN_RANGE[0]:
        problems.append(f"near black (mean {mean:.0f})")
    elif mean > MEAN_RANGE[1]:
        problems.append(f"blown out (mean {mean:.0f})")
    if spread < MIN_SPREAD:
        problems.append(f"flat, no contrast (spread {spread:.0f})")
    if sharp < MIN_SHARPNESS:
        problems.append(f"blurry (sharpness {sharp:.0f})")
    return problems, {"mean": round(mean, 1), "spread": round(spread, 1), "sharpness": round(sharp, 1)}


def pick_model(job: dict[str, Any], default: str) -> str:
    """The job's model when it names a known one; an edit job with none (or an unknown one) gets qwen_edit; a generate job with an
    unknown one gets the default."""
    name = str(job.get("model") or "")
    if name in MODELS:
        return name
    if str(job.get("task") or "generate") == "edit":
        return EDIT_MODELS[0]
    return default if default in MODELS and default not in EDIT_MODELS else "klein"


def decode_photo(job: dict[str, Any], fetch: Callable[[str], bytes] | None = None) -> Any:
    from PIL import Image, ImageOps

    if job.get("image_b64"):
        try:
            data = base64.b64decode(job["image_b64"])
        except Exception as exc:  # noqa: BLE001
            raise JobError(f"image_b64 isn't base64: {exc}") from exc
    elif job.get("image_url"):
        url = str(job["image_url"])
        if not url.lower().startswith("https://"):
            raise JobError("image_url must be https")
        data = (fetch or _fetch)(url)
    else:
        raise JobError("an edit job needs image_b64 or image_url")
    if len(data) > MAX_PHOTO_BYTES:
        raise JobError(f"photo too large ({len(data)} bytes)")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise JobError(f"photo isn't a readable image: {type(exc).__name__}") from exc
    return ImageOps.exif_transpose(img).convert("RGB")


def _fetch(url: str) -> bytes:
    # a plain client: the worker's token is for our server only, never sent to a photo host
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": f"PriceChatWorker-newsdesk/{VERSION}"}) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_PHOTO_BYTES:
                    raise JobError("photo too large")
            return bytes(buf)


def _jpeg(img: Any) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=95)
    return out.getvalue()


def _next_seed(seed: int) -> int:
    return (seed * 1103515245 + 12345) % 2**31 or 1


# ---------------------------------------------------------------- GPU


class Renderer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.repo, self.defaults = MODELS[name]
        self.pipe: Any = None
        self.loaded_at = 0.0
        self.used_at = 0.0

    @property
    def edits(self) -> bool:
        return self.name in EDIT_MODELS

    def load(self) -> None:
        if self.pipe is not None:
            return
        import torch

        t = time.time()
        if self.name == "klein":
            from diffusers import Flux2KleinPipeline as P
        elif self.name == "zimage":
            from diffusers import ZImagePipeline as P
        elif self.name == "qwen_edit":
            from diffusers import QwenImageEditPlusPipeline as P
        else:
            from diffusers import QwenImagePipeline as P
        pipe = P.from_pretrained(self.repo, torch_dtype=torch.bfloat16)
        if self.name in ("qwen", "qwen_edit"):  # 20B transformer: fp8 storage; the 7B text encoder waits on the CPU while it runs
            pipe.transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16)
            pipe.enable_model_cpu_offload()
        else:
            pipe = pipe.to("cuda")
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:  # noqa: BLE001
            pass
        self.pipe, self.loaded_at = pipe, time.time()
        log.info("loaded %s in %.1fs", self.repo, time.time() - t)

    def unload(self) -> None:
        if self.pipe is None:
            return
        import gc

        import torch

        self.pipe = None
        gc.collect()
        torch.cuda.empty_cache()
        log.info("unloaded %s", self.name)

    def paint(self, prompt: str, width: int, height: int, seed: int, image: Any = None, negative: str | None = None,
              steps: int | None = None) -> tuple[Any, dict[str, Any]]:
        """One picture: (PIL image, {seconds, steps, seed}). An edit model needs image; a generator never gets one."""
        import torch

        if self.edits and image is None:
            raise JobError(f"{self.name} edits a photo; it can't generate from text alone")
        self.load()
        kw = dict(self.defaults)
        if steps:
            kw["num_inference_steps"] = int(steps)
        if negative and self.name in ("qwen", "qwen_edit"):
            kw["negative_prompt"] = negative
        if self.edits:
            kw["image"] = image
        g = torch.Generator("cuda").manual_seed(int(seed))
        torch.cuda.synchronize()
        t = time.time()
        img = self.pipe(prompt=prompt, width=width, height=height, generator=g, **kw).images[0]
        torch.cuda.synchronize()
        self.used_at = time.time()
        return img, {"seconds": round(time.time() - t, 2), "steps": kw.get("num_inference_steps"), "seed": seed}


class TextCheck:
    """EasyOCR on the GPU: any word-like lettering the image model drew. Small or low-confidence marks (texture) pass."""

    def __init__(self) -> None:
        self.reader: Any = None
        self.ok = True

    def load(self) -> bool:
        if self.reader is None and self.ok:
            try:
                import easyocr

                self.reader = easyocr.Reader(["en"], gpu=True, verbose=False)
            except Exception as exc:  # noqa: BLE001 - no OCR: the server's own checks still run
                log.warning("text check unavailable: %s", exc)
                self.ok = False
        return self.reader is not None

    def lettering(self, data: Any, ignore_box: tuple[int, int, int, int] | None = None) -> list[str]:
        """Lettering in a picture (JPEG bytes or a PIL image). ignore_box (pixels): a real photo's region, blanked first so only the
        painted areas are read (a real photo may legitimately carry signage)."""
        if not self.load():
            return []
        import numpy as np
        from PIL import Image

        img = (Image.open(io.BytesIO(data)) if isinstance(data, (bytes, bytearray)) else data).convert("RGB")
        if ignore_box is not None:
            img = img.copy()
            img.paste(FILL, ignore_box)
        found = []
        for box, text, conf in self.reader.readtext(np.asarray(img)):
            ys = [p[1] for p in box]
            h = max(ys) - min(ys)
            letters = sum(ch.isalnum() for ch in text)
            if conf >= 0.5 and letters >= 3 and h >= img.height * 0.02:
                found.append(f"{text} ({conf:.2f})")
        return found

    def unload(self) -> None:
        self.reader = None


def _cuda(fn: Callable[[Any], Any]) -> Any:
    try:
        import torch

        return fn(torch) if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001 - no torch / no GPU (tests)
        return None


def _gpu_name() -> str | None:
    return _cuda(lambda t: t.cuda.get_device_name(0))


def process(job: dict[str, Any], renderer: Any, ocr: Any = None, tries: int = 3, fetch: Callable[[str], bytes] | None = None) -> tuple[bytes, dict[str, Any]]:
    """Do one job: (JPEG bytes, result). Raises JobError (or anything else) for a failure the server should hear about.
    renderer needs .repo, .edits and .paint(prompt, width, height, seed, image=, negative=) -> (PIL image, meta)."""
    started = time.time()
    _cuda(lambda t: t.cuda.reset_peak_memory_stats())
    task = str(job.get("task") or "generate")
    seed = int(job.get("seed") or 1)
    prompt = str(job.get("prompt") or "").strip()
    extra: dict[str, Any] = {"task": task}
    ignore: tuple[int, int, int, int] | None = None
    if task == "generate":
        if renderer.edits:
            raise JobError(f"{renderer.repo} edits photos; a generate job needs a generator model")
        width, height = int(job.get("width") or CANVAS[0]), int(job.get("height") or CANVAS[1])

        def once(s: int) -> tuple[Any, dict[str, Any]]:
            return renderer.paint(prompt, width, height, s, negative=job.get("negative"))
    elif task == "edit":
        mode = str(job.get("edit") or "")
        if mode not in ("extend", "illustrate"):
            raise JobError(f"unknown edit {mode!r} (extend or illustrate)")
        if not renderer.edits:
            raise JobError(f"{renderer.repo} can't edit photos; use qwen_edit")
        photo = decode_photo(job, fetch)
        scene = f" ({prompt})" if prompt else ""
        neg = job.get("negative") or NEG_EDIT
        if mode == "extend":
            pct = ai_area_pct(fit_box(photo.size))
            if pct > MAX_AI_AREA_PCT:
                raise JobError(f"refused: extending this {photo.width}x{photo.height} photo to 16:9 would invent {pct:.0f}% of the picture "
                               f"(limit {MAX_AI_AREA_PCT:.0f}%)")
            fill = str(job.get("fill") or EXTEND_FILL)
            fill = fill if fill in EXTEND_WHAT else "blur"
            scaled, base, box = outpaint_input(photo, fill=fill)
            sides = margin_sides(box)
            text = EXTEND_PROMPT.format(what=EXTEND_WHAT[fill], sides=(" on the " + " and ".join(sides)) if sides else "", scene=scene)
            ignore = box
            extra.update(edit="extend", ai_area_pct=pct, preserved_box=norm_box(box), scale=round(scaled.width / photo.width, 3))

            def once(s: int) -> tuple[Any, dict[str, Any]]:
                if not sides:  # already 16:9: nothing to invent, the photo goes out as it is (scaled)
                    return base, {"seconds": 0.0, "steps": 0, "seed": s, "preserved_max_diff": region_max_diff(base, scaled, box)}
                img, meta = renderer.paint(text, CANVAS[0], CANVAS[1], s, image=base, negative=neg)
                changed = margin_change(img, base, box)
                if changed < MIN_MARGIN_CHANGE:
                    raise JobError(f"the edit model left the margins unpainted (margin change {changed})")
                img, diff = paste_back(img, scaled, box)
                if diff != 0:
                    raise JobError(f"paste-back failed: the photo region differs by {diff}")
                return img, {**meta, "preserved_max_diff": diff, "margin_change": changed, "fill": fill}
        else:
            src = cover(photo)
            text = ILLUSTRATE_PROMPT.format(scene=scene)
            extra.update(edit="illustrate", ai_illustration=True)

            def once(s: int) -> tuple[Any, dict[str, Any]]:
                return renderer.paint(text, CANVAS[0], CANVAS[1], s, image=src, negative=neg)
    else:
        raise JobError(f"unknown task {task!r} (generate or edit)")

    found: list[str] = []
    for attempt in range(max(1, tries)):
        img, meta = once(seed)
        problems, measured = image_quality(img)
        if problems:  # dark, flat or blurry: reported; the server tries a fresh direction or uses its own visual
            raise JobError("quality: " + "; ".join(problems))
        found = ocr.lettering(img, ignore_box=ignore) if ocr is not None and meta.get("steps") else []
        if not found:
            break
        log.info("lettering in the picture %s, new seed", found[:3])
        seed = _next_seed(seed)
    else:
        raise JobError(f"lettering in every try: {', '.join(found[:3])}")
    peak = _cuda(lambda t: round(t.cuda.max_memory_allocated() / 1e9, 1))
    result = {"model": renderer.repo, **meta, **extra, "gpu": _gpu_name(), "vram_gb": peak, "quality": measured,
              "ocr": {"checked": bool(ocr is not None and getattr(ocr, "ok", True)), "found": found[:5],
                      "area": "margins" if ignore is not None else "all"},
              "tries": attempt + 1, "total_seconds": round(time.time() - started, 2)}
    return _jpeg(img), result


# ---------------------------------------------------------------- post copy on the local LLM (job["copy_request"])

OLLAMA = os.environ.get("PCW_OLLAMA_URL", "http://localhost:11434")
COPY_VRAM_GB = 14.0  # gpt-oss:20b needs about this much free; less and the image model is unloaded first
COPY_MAX_TOKENS = 2048  # per call, reasoning included (~10 s on the 5090). 2026-11-12, first live job: one call ran 150 s and timed out
COPY_TIMEOUT_S = 90.0   # (gpt-oss in JSON mode can run away on a long prompt); now it stops, the job goes on, the desk writes the copy
FACT_CHECK = ("You are a fact checker. Compare the DRAFT with the REPORTING. List every statement in the draft that the reporting does not "
              "directly support (a changed verb like 'fell' for 'gave back gains' counts, a merged claim from two outlets counts, any cause or "
              "consequence not stated counts). Reply JSON {\"ok\": true|false, \"problems\": [..]}")


def _ollama_chat(model: str, system: str, user: str, keep_alive: Any, post: Callable[..., Any] | None = None) -> dict[str, Any]:
    """One JSON-mode chat call; the reply parsed as a JSON object (ValueError when it isn't one)."""
    import json

    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "format": "json",
            "stream": False, "keep_alive": keep_alive, "options": {"temperature": 0.4, "num_predict": COPY_MAX_TOKENS}}
    if post is None:
        r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=COPY_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
    else:
        data = post(f"{OLLAMA}/api/chat", body)
    out = json.loads(((data or {}).get("message") or {}).get("content") or "")
    if not isinstance(out, dict):
        raise ValueError("the model's reply isn't a JSON object")
    return out


def write_copy(req: dict[str, Any], *, post: Callable[..., Any] | None = None, free_gb: Callable[[], float | None] | None = None,
               unload_image_model: Callable[[], None] | None = None) -> dict[str, Any]:
    """Draft -> fact-check -> (one rewrite -> fact-check). Never raises: any trouble is {"draft": None, "error": ...}.
    The last call has keep_alive 0 (and a failure unloads too), so the LLM's VRAM is free again before the image model runs."""
    import json

    model = os.environ.get("PCW_COPY_MODEL", "gpt-oss:20b")
    t = time.time()
    used = False
    try:
        system, user = str(req.get("system") or ""), str(req.get("user") or "")
        if not user:
            return {"draft": None, "error": "copy_request has no user message", "model": model, "seconds": 0.0}
        measure = free_gb or (lambda: _cuda(lambda t_: t_.cuda.mem_get_info()[0] / 1e9))
        free = measure()
        if free is not None and free < COPY_VRAM_GB and free_gb is None:  # an offloaded Qwen leaves only cache behind: release that first
            _cuda(lambda t_: t_.cuda.empty_cache())
            free = measure()
        if free is not None and free < COPY_VRAM_GB:
            if unload_image_model is not None:
                unload_image_model()
                free = measure()
            if free is not None and free < COPY_VRAM_GB:
                return {"draft": None, "error": "no vram", "model": model, "seconds": round(time.time() - t, 2)}
        used = True
        draft = _ollama_chat(model, system, user, "2m", post)
        check = _ollama_chat(model, FACT_CHECK, f"REPORTING:\n{user}\n\nDRAFT:\n{json.dumps(draft, ensure_ascii=False)}", "2m", post)
        rewrites = 0
        if check.get("ok") is not True:
            probs = [str(p) for p in (check.get("problems") or [])]
            draft = _ollama_chat(model, system, f"{user}\n\nFix these problems: " + "; ".join(probs), "2m", post)
            rewrites = 1
            check = _ollama_chat(model, FACT_CHECK, f"REPORTING:\n{user}\n\nDRAFT:\n{json.dumps(draft, ensure_ascii=False)}", 0, post)
            used = False  # that call unloaded it
        problems = [str(p) for p in (check.get("problems") or [])][:12]
        return {"draft": draft, "ok": check.get("ok") is True and not problems, "problems": problems, "rewrites": rewrites, "model": model,
                "seconds": round(time.time() - t, 2)}
    except Exception as exc:  # noqa: BLE001 - copy never fails the picture job
        return {"draft": None, "error": f"{type(exc).__name__}: {exc}"[:300], "model": model, "seconds": round(time.time() - t, 2)}
    finally:
        if used:  # free the LLM's VRAM now, not in 2 minutes
            try:
                body = {"model": model, "keep_alive": 0}
                if post is None:
                    httpx.post(f"{OLLAMA}/api/generate", json=body, timeout=30.0)
                else:
                    post(f"{OLLAMA}/api/generate", body)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- server + health


class Server:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/") + "/api/newsdesk-worker"
        self.h = {"Authorization": f"Bearer {token}", "User-Agent": f"PriceChatWorker-newsdesk/{VERSION}"}
        self.c = httpx.Client(timeout=60.0)

    def claim(self, info: dict[str, Any]) -> dict[str, Any] | None:
        r = self.c.post(f"{self.base}/claim", json={"info": info}, headers=self.h)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    def result(self, job_id: int, data: bytes, result: dict[str, Any]) -> dict[str, Any]:
        r = self.c.post(f"{self.base}/jobs/{job_id}/result", headers=self.h, json={"image_b64": base64.b64encode(data).decode(), "result": result})
        r.raise_for_status()
        return r.json()

    def fail(self, job_id: int, error: str) -> None:
        self.c.post(f"{self.base}/jobs/{job_id}/fail", headers=self.h, json={"error": error[:480]})


def hub_cache() -> Path:
    try:
        from huggingface_hub import constants

        return Path(constants.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


STALE_PARTIAL_S = 6 * 3600  # a .incomplete blob untouched this long is an abandoned download, not one in progress


def on_disk(repo: str, cache: Path | None = None) -> bool:
    """A complete diffusers snapshot of repo in the HF cache: model_index.json present and no download in progress (a
    .incomplete blob written to in the last 6 hours). 2026-11-12: klein's 2-day-old abandoned partial made Admin show it
    missing while it rendered fine from its snapshot."""
    d = (cache or hub_cache()) / ("models--" + repo.replace("/", "--"))
    now = time.time()
    busy = any(now - p.stat().st_mtime < STALE_PARTIAL_S for p in d.glob("blobs/*.incomplete"))
    return any(d.glob("snapshots/*/model_index.json")) and not busy


class Health:
    """The claim's info: small, refreshed every poll (the disk scan every 5 minutes)."""

    def __init__(self, default: str, ocr: bool, cache: Path | None = None) -> None:
        self.default, self.ocr, self.cache = default, ocr, cache
        self.gpu = _gpu_name()
        self.done = self.failed = 0
        self.last_seconds: float | None = None
        self._disk: tuple[float, list[str]] = (-1e9, [])

    def available(self) -> list[str]:
        if time.time() - self._disk[0] > 300:
            self._disk = (time.time(), sorted(n for n, (repo, _) in MODELS.items() if on_disk(repo, self.cache)))
        return self._disk[1]

    def info(self, loaded: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {"version": VERSION, "model": MODELS[self.default][0], "models": sorted(MODELS), "on_disk": self.available(),
                               "loaded": loaded, "gpu": self.gpu, "ocr": self.ocr, "done": self.done, "failed": self.failed,
                               "last_s": self.last_seconds}
        mem = _cuda(lambda t: t.cuda.mem_get_info())
        if mem:
            out["vram_free_gb"], out["vram_total_gb"] = round(mem[0] / 1e9, 1), round(mem[1] / 1e9, 1)
        return out


def main(argv: list[str] | None = None) -> int:
    load_env(ROOT / "worker.env")
    ap = argparse.ArgumentParser(prog="newsdesk_images")
    ap.add_argument("--server", default=os.environ.get("PRICECHAT_SERVER", "https://api.pricechat.live"))
    ap.add_argument("--token", default=os.environ.get("PRICECHAT_WORKER_TOKEN"))
    ap.add_argument("--model", default=os.environ.get("PCW_IMAGE_MODEL", "klein"), choices=sorted(n for n in MODELS if n not in EDIT_MODELS),
                    help="when a generate job names none (or an unknown one)")
    ap.add_argument("--idle", type=float, default=5.0)
    ap.add_argument("--unload-after", type=float, default=float(os.environ.get("PCW_IMAGE_IDLE_UNLOAD", "600")))
    ap.add_argument("--tries", type=int, default=int(os.environ.get("PCW_IMAGE_TRIES", "3")))
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "newsdesk_images.log", encoding="utf-8")])
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a poll every few seconds: only problems are worth a line
    if not a.token:
        print("No worker token (PRICECHAT_WORKER_TOKEN in worker.env, or --token)", file=sys.stderr)
        return 2

    def _stop(*_: Any) -> None:
        global STOP
        STOP = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    server = Server(a.server, a.token)
    renderers: dict[str, Renderer] = {name: Renderer(name) for name in MODELS}
    ocr = None if a.no_ocr else TextCheck()
    health = Health(a.model, ocr is not None)
    log.info("newsdesk images ready: %s", health.info(None))

    def loaded() -> str | None:
        return next((n for n, r in renderers.items() if r.pipe is not None), None)

    def renderer_for(name: str) -> Renderer:
        for other, r in renderers.items():  # one model on the GPU at a time
            if other != name and r.pipe is not None:
                r.unload()
        return renderers[name]

    backoff = a.idle
    while not STOP:
        try:
            job = server.claim(health.info(loaded()))
        except httpx.HTTPError as exc:
            log.warning("server: %s (retrying in %.0fs)", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        backoff = a.idle
        if job is None:
            for r in renderers.values():
                if r.pipe is not None and time.time() - r.used_at > a.unload_after:
                    r.unload()
                    if ocr is not None:
                        ocr.unload()
            if a.once:
                return 0
            time.sleep(a.idle)
            continue
        renderer = renderer_for(pick_model(job, a.model))
        log.info("job %s (event %s, %s, %s%s): %s", job["id"], job.get("event_id"), renderer.name, job.get("task") or "generate",
                 f"/{job['edit']}" if job.get("edit") else "", job.get("scene"))
        t = time.time()
        copy = None
        if isinstance(job.get("copy_request"), dict):  # the post's words first, on the local LLM, then the picture
            copy = write_copy(job["copy_request"], unload_image_model=lambda: [r.unload() for r in renderers.values()])
            log.info("job %s: copy %s in %.1fs", job["id"], "ok" if copy.get("ok") else (copy.get("error") or f"problems {copy.get('problems')}"),
                     copy.get("seconds") or 0)
        try:
            data, result = process(job, renderer, ocr, a.tries)
            if copy is not None:
                result["copy"] = copy
        except Exception as exc:  # noqa: BLE001 - reported; the server retries or the post uses its own visual
            health.failed += 1
            health.last_seconds = round(time.time() - t, 1)
            if isinstance(exc, JobError):
                log.warning("job %s failed: %s", job["id"], exc)
            else:
                log.exception("job %s failed", job["id"])
            _cuda(lambda t: t.cuda.empty_cache())
            try:
                server.fail(job["id"], str(exc) if isinstance(exc, JobError) else f"{type(exc).__name__}: {exc}")
            except httpx.HTTPError:
                pass
            continue
        health.done += 1
        health.last_seconds = result.get("total_seconds")
        try:
            got = server.result(job["id"], data, result)
            log.info("job %s: %s in %.1fs", job["id"], "accepted" if got.get("ok") else f"rejected {got.get('rejected')}", result["seconds"])
        except httpx.HTTPError as exc:
            log.warning("job %s: upload failed (%s); the lease will expire and it goes back to the queue", job["id"], exc)
        if a.once:
            return 0
    for r in renderers.values():
        r.unload()
    return 0


if __name__ == "__main__":
    sys.exit(main())
