"""Short Market on the worker: one claimed short → story (local LLM, checked by the backend) → voice (Kokoro) → a 3D
animated clip per scene (Wan 2.2, in the GPU process) → cut, captioned and voiced with FFmpeg → uploaded.

Nothing leaves this machine except the finished MP4 going to the Price Chat backend, where the owner downloads it.
A machine without the GPU environment or the video model still finishes the short, with still scenes, and says so.
"""

import json
import logging
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFilter

from pricechat_worker import gpu, render, story, tts, visuals
from pricechat_worker.client import Client

log = logging.getLogger("pricechat_worker")

NEGATIVE = ("text, letters, numbers, words, captions, subtitles, watermark, logo, signature, real person, celebrity, photorealistic, "
            "live action, photograph, 2D, flat illustration, hand-drawn, sketch, line art, anime, cel shading, blurry, distorted, deformed, extra limbs, bad anatomy, flicker, jitter, static, frozen, "
            "low quality, worst quality, overexposed, nudity, gore")
FRAMING = "The subject sits in the upper two thirds of the frame."  # captions cover the lower third


class Cancelled(Exception):
    pass


def still(accent: tuple[int, int, int], k: int, out: Path) -> Path:
    """A scene without a clip: a soft abstract backdrop in the style's colour (the scene's words are drawn by the captions)."""
    w, h = 1080, 1920
    rng = random.Random(k)
    img = Image.new("RGB", (w, h), (8, 10, 14))
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    for _ in range(4):
        x, y, r = rng.randint(-100, w), rng.randint(0, h - 500), rng.randint(320, 620)
        shade = rng.uniform(0.25, 0.6)
        g.ellipse((x, y, x + r, y + r), fill=tuple(int(c * shade) for c in accent))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(180)), 0.8)
    img.save(out)
    return out


def film(prompts: list[str], seconds: list[float], quality: str, workdir: Path, seed: int,
         on_progress: Callable[[int, float], None]) -> list[Path | None]:
    """One clip per scene from the video model. A scene the model couldn't draw comes back as None."""
    py, model = gpu.python(), gpu.video_model()
    assert py is not None and model is not None
    width, height, steps = gpu.QUALITY.get(quality, gpu.QUALITY["standard"])
    outs = [workdir / f"shot_{k:02d}.mp4" for k in range(len(prompts))]
    spec = workdir / "shots.json"
    spec.write_text(json.dumps({"width": width, "height": height, "steps": steps, "fps": gpu.FPS, "negative": NEGATIVE, "clips": [
        {"prompt": p, "out": str(o), "frames": gpu.frames_for(s), "seed": seed + k} for k, (p, o, s) in enumerate(zip(prompts, outs, seconds))]}),
        encoding="utf-8")
    errlog = (workdir / "gpu.err").open("w", encoding="utf-8")
    proc = subprocess.Popen([str(py), "-u", str(Path(__file__).with_name("gpu_clips.py")), str(model), str(spec)], stdout=subprocess.PIPE,
                            stderr=errlog, text=True, encoding="utf-8", errors="replace")
    last_report = 0.0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "error" in msg:
                log.warning("scene %s: %s", msg.get("clip"), msg["error"])
            if msg.get("ready"):
                log.info("video model loaded in %ss on %s", msg.get("load_seconds"), msg.get("gpu"))
                on_progress(0, 0.0)
            elif "step" in msg and time.monotonic() - last_report > 20:  # also keeps the job's lease alive
                last_report = time.monotonic()
                on_progress(int(msg["clip"]), msg["step"] / max(1, msg["steps"]))
            elif "done" in msg:
                last_report = time.monotonic()
                on_progress(int(msg["done"]) + 1, 0.0)
        proc.wait(timeout=60)
    except BaseException:
        proc.kill()
        raise
    finally:
        errlog.close()
    if proc.returncode != 0 and not any(o.exists() for o in outs):
        tail = (workdir / "gpu.err").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
        raise RuntimeError("The video model stopped: " + (tail[0][:300] if tail else f"exit code {proc.returncode}"))
    return [o if o.exists() and o.stat().st_size > 2000 else None for o in outs]


def process(client: Client, job: dict[str, Any], keep: Path | None = None) -> None:
    jid = job["job_id"]
    t0 = time.monotonic()

    def step(stage: str, status: str, note: str = "") -> None:
        if not client.progress(jid, stage, status, note):
            raise Cancelled("taken back by the server (remade, deleted or timed out)")

    workdir = Path(tempfile.mkdtemp(prefix=f"pcs_{jid}_"))
    try:
        ff = render.ffmpeg_exe()
        style = job["style"]
        accent = visuals.hex_rgb(style.get("accent") or "#ffd23f")
        # 1. the story (the backend checks it; rewrite with its feedback, then fall back to the facts-only story)
        step("script", "running")
        feedback: list[str] = []
        approved = None
        for attempt in range(1, 5):
            script = story.finish(story.write(job, feedback, attempt), job)
            verdict = client.script(jid, script)
            if verdict.get("ok"):
                approved = {**script, **verdict["script"]}
                break
            feedback = verdict.get("problems") or []
            step("script", "retrying", feedback[0] if feedback else "")
        if approved is None:
            raise RuntimeError("Couldn't write a story that passes the fact and safety checks: " + "; ".join(feedback[:2]))
        segments = [s for s in approved["segments"] if str(s.get("text", "")).strip()]
        # 2. the voice
        engine = tts.pick("kokoro")
        step("voice", "running", engine)
        parts = [workdir / f"voice_{k:02d}.wav" for k in range(len(segments))]
        if engine == "kokoro":
            tts.kokoro_batch([s["text"] for s in segments], parts, job.get("voice") or "am_michael", ff)
        else:
            for seg, p in zip(segments, parts):
                tts.speak(seg["text"], p, engine, ff)
        voice = workdir / "voice.wav"
        spans = tts.join(parts, voice, gap=0.14)
        step("voice", "done", f"{engine}{' · ' + job['voice'] if engine == 'kokoro' and job.get('voice') else ''} · {spans[-1][1]:.1f}s")
        # 3. the scenes
        n = len(segments)
        seconds = [(spans[k + 1][0] if k + 1 < n else spans[k][1] + 0.6) - (spans[k][0] if k else 0.0) for k in range(n)]
        clips: list[Path | None] = [None] * n
        if gpu.video_ready():
            step("visuals", "running", f"loading the video model · {n} scenes")
            if str(approved.get("engine") or "").startswith("ollama"):
                story.release()
            prompts = [f"{story.shot_prompt(seg, approved, style)} {FRAMING}" for seg in segments]
            started = time.monotonic()

            def on_progress(done: int, part: float) -> None:
                spent = time.monotonic() - started
                total = spent / max(0.02, (done + part) / n) if done + part > 0 else 0
                left = f" · about {max(1, round((total - spent) / 60))} min left" if total else ""
                step("visuals", "running", f"scene {min(n, done + 1)} of {n}{left}")

            clips = film(prompts, seconds, job.get("quality") or "standard", workdir, seed=jid * 1000 + int(job.get("generation") or 1), on_progress=on_progress)
        made = sum(1 for c in clips if c)
        media = [c if c else still(accent, k, workdir / f"still_{k:02d}.png") for k, c in enumerate(clips)]
        clip_seconds = [gpu.frames_for(s) / gpu.FPS if c else None for c, s in zip(clips, seconds)]
        step("visuals", "done", f"{made} of {n} scenes animated" if gpu.video_ready() else "still scenes: this worker has no video model")
        # 4. cut, captions, voice
        step("assemble", "running")
        bold = visuals.font_path("bold")
        n_caps = render.captions(segments, spans, accent, bold, workdir / "captions.ass", titles=True)
        out = workdir / "short.mp4"
        render.assemble_story(ff, workdir, media, clip_seconds, spans, voice, workdir / "captions.ass", bold, out)
        thumb = workdir / "thumb.jpg"
        render.poster(ff, workdir, out, thumb)
        meta = render.probe(ff, out)
        step("assemble", "done", f"{meta['duration']:.1f}s · {meta['size_bytes'] / 1e6:.1f} MB")
        width, height, steps = gpu.QUALITY.get(job.get("quality") or "standard", gpu.QUALITY["standard"])
        meta.update({"subtitles": n_caps > 0, "tts": f"{engine}:{job.get('voice')}" if engine == "kokoro" else engine, "llm": approved.get("engine"),
                     "seconds": round(time.monotonic() - t0, 1), "scenes": n, "ai_scenes": made,
                     "renderer": f"wan2.2-ti2v-5b {width}x{height} + ffmpeg" if made else "stills + ffmpeg"})
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy(out, keep / f"short{jid}.mp4")
        res = client.result(jid, out, thumb, meta)
        log.info("short %s uploaded: %s (%s of %s scenes animated, %.0fs)", jid, res.get("status"), made, n, meta["seconds"])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
