"""Price Chat AI worker: `python -m pricechat_worker --server https://api.pricechat.live --token pcw_...`

Runs on the DGX Spark (or any machine with Python 3.11+ and FFmpeg). Connects OUT to the backend over HTTPS with its own
token, so the Spark exposes no endpoint. Loop: claim a job → write the script (local LLM, checked by the backend) →
voiceover → scene images → video + animated captions → quality check → upload. Progress keeps the job's lease alive;
if the worker dies, the lease expires and the job goes back to the queue. Ctrl+C finishes nothing half-way: the job
simply returns to the queue.

When Brand Manager has nothing to do, the same loop asks Short Market for a short (pricechat_worker/shorts.py: story →
voice → 3D animated scenes → edit).
"""

import argparse
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from pricechat_worker import creative, gpu, llm, motion, pipeline2, pipeline4, render, shorts, tts, visuals
from pricechat_worker.client import Client, ServerError

log = logging.getLogger("pricechat_worker")
STOP = False


class Cancelled(Exception):
    pass


def capabilities() -> dict[str, Any]:
    info: dict[str, Any] = {"platform": f"{platform.system()} {platform.machine()}", "python": platform.python_version(), "tts": tts.available()}
    try:
        info["ffmpeg"] = subprocess.run([render.ffmpeg_exe(), "-version"], capture_output=True, text=True, timeout=10).stdout.split("\n")[0][:80]
    except Exception as exc:  # noqa: BLE001
        info["ffmpeg"] = f"missing ({exc})"
    try:
        r = httpx.get(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/tags", timeout=3.0)
        info["llm"] = [m["name"] for m in r.json().get("models", [])][:10]
    except Exception:  # noqa: BLE001
        info["llm"] = []
    info["anthropic"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    info["gpu"] = bool(shutil.which("nvidia-smi"))
    model = gpu.video_model()
    info["video_ai"] = {"model": model.name} if gpu.python() and model else False  # Short Market's 3D scenes
    info["motion"] = motion.available()  # Content Studio v2 renderer (and v4's editorial composition)
    info["editorial"] = motion.available() and "kokoro" in info["tts"]
    return info


def v4_ready(job: dict[str, Any]) -> bool:
    """Content Studio v4 (editorial: real footage first) is the default engine; it needs the same renderer and voice as v2."""
    return str((job.get("creative") or {}).get("engine") or "editorial") == "editorial" and motion.available() and "kokoro" in tts.available()


def v2_ready(job: dict[str, Any]) -> bool:
    """The v2 pipeline needs the motion renderer and the Kokoro voice; otherwise (or when the owner picks the classic
    engine) the v1 pipeline below makes the video exactly as before."""
    return creative.resolve(job)["engine"] == "motion" and motion.available() and "kokoro" in tts.available()


def process(client: Client, job: dict[str, Any], keep: Path | None = None) -> None:
    jid = job["job_id"]
    t0 = time.monotonic()

    def step(stage: str, status: str, note: str = "") -> None:
        if not client.progress(jid, stage, status, note):
            raise Cancelled("cancelled by the server (emergency stop, regenerate or timeout)")

    workdir = Path(tempfile.mkdtemp(prefix=f"pcw_{jid}_"))
    try:
        ff = render.ffmpeg_exe()
        if v4_ready(job):  # Content Studio v4: authentic media research, footage-first script, editorial timeline, authenticity score
            made = pipeline4.produce(job, ff, workdir, lambda s: client.script(jid, s), step, on_library=lambda lib: client.media(jid, lib))
            made["meta"]["seconds"] = round(time.monotonic() - t0, 1)
            if keep:
                keep.mkdir(parents=True, exist_ok=True)
                shutil.copy(made["out"], keep / f"job{jid}.mp4")
                shutil.copy(made["thumb"], keep / f"job{jid}.jpg")
            res = client.result(jid, made["out"], made["thumb"], made["meta"])
            log.info("job %s uploaded: %s", jid, res.get("status"))
            return
        if v2_ready(job):  # Content Studio v2: motion graphics, narrator voices, word-synced captions, creative QC
            made = pipeline2.produce(job, ff, workdir, lambda s: client.script(jid, s), step)
            made["meta"]["seconds"] = round(time.monotonic() - t0, 1)
            if keep:
                keep.mkdir(parents=True, exist_ok=True)
                shutil.copy(made["out"], keep / f"job{jid}.mp4")
                shutil.copy(made["thumb"], keep / f"job{jid}.jpg")
            res = client.result(jid, made["out"], made["thumb"], made["meta"])
            log.info("job %s uploaded: %s", jid, res.get("status"))
            return
        # 1. script (the backend checks it; rewrite with its feedback, then fall back to the template script)
        step("script", "running")
        feedback: list[str] = []
        approved = None
        for attempt in range(1, 6):
            script = llm.finish(llm.write(job, feedback, attempt), job)
            craft = (llm.style_problems(script) or llm.fact_check(job, script)) if script.get("engine") != "template" else []
            if craft and attempt < 4:
                feedback = craft
                step("script", "retrying", craft[0])
                continue
            verdict = client.script(jid, script)
            if verdict.get("ok"):
                approved = {**script, **verdict["script"]}
                break
            feedback = verdict.get("problems") or []
            step("script", "retrying", feedback[0] if feedback else "")
        if approved is None:
            raise RuntimeError("Couldn't write a script that passes the fact and safety checks: " + "; ".join(feedback[:2]))
        segments = [s for s in approved["segments"] if str(s.get("text", "")).strip()]
        # 2. voice
        st = job["strategy"]
        engine = tts.pick(st.get("voice") or "auto")
        narrator = str(st.get("voice_name") or os.environ.get("KOKORO_VOICE") or "am_michael")
        step("voice", "running", engine)
        parts = [workdir / f"voice_{k:02d}.wav" for k in range(len(segments))]
        if engine == "kokoro":
            tts.kokoro_batch([tts.spoken(s["text"]) for s in segments], parts, narrator, ff, float(st.get("voice_speed") or tts.KOKORO_SPEED))
        else:
            for seg, p in zip(segments, parts):
                tts.speak(tts.spoken(seg["text"]), p, engine, ff)
        voice = workdir / "voice.wav"
        spans = tts.join(parts, voice, gap=0.12)
        engine = f"kokoro:{narrator}" if engine == "kokoro" else engine
        step("voice", "done", f"{engine} · {spans[-1][1]:.1f}s")
        # 3. visuals
        step("visuals", "running")
        scenes = visuals.render_scenes(job, segments, workdir)
        step("visuals", "done", f"{len(scenes)} scenes")
        # 4. captions + assembly
        step("subtitles", "running")
        bold = visuals.font_path("bold")
        accent = visuals.hex_rgb((job.get("template_style") or {}).get("accent") or "#00e58f")
        n_caps = render.captions(segments, spans, accent, bold, workdir / "captions.ass")
        step("subtitles", "done", f"{n_caps} caption lines")
        step("assemble", "running")
        out = workdir / "video.mp4"
        render.assemble(ff, workdir, scenes, spans, voice, workdir / "captions.ass", bold, job["strategy"].get("quality", "standard"),
                        job["strategy"].get("music") or None, out, accent)
        thumb = workdir / "thumb.jpg"
        render.thumbnail(ff, workdir, scenes[0], thumb)
        meta = render.probe(ff, out)
        step("assemble", "done", f"{meta['duration']:.1f}s · {meta['size_bytes'] / 1e6:.1f} MB")
        meta.update({"subtitles": n_caps > 0, "tts": engine, "llm": approved.get("engine"), "seconds": round(time.monotonic() - t0, 1),
                     "renderer": "pillow+ffmpeg", "cost_usd": 0.0})
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy(out, keep / f"job{jid}.mp4")
            shutil.copy(thumb, keep / f"job{jid}.jpg")
        res = client.result(jid, out, thumb, meta)
        log.info("job %s uploaded: %s", jid, res.get("status"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pricechat_worker", description="Price Chat Brand Manager AI worker")
    ap.add_argument("--server", default=os.environ.get("PRICECHAT_SERVER", "https://api.pricechat.live"))
    ap.add_argument("--token", default=os.environ.get("PRICECHAT_WORKER_TOKEN"))
    ap.add_argument("--once", action="store_true", help="process at most one job, then exit")
    ap.add_argument("--keep", type=Path, help="also keep a copy of each finished video in this folder")
    ap.add_argument("--idle", type=float, default=15.0, help="seconds between polls when there is nothing to do")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not a.token:
        print("Give the worker token: --token pcw_... (Brand Manager › Settings › AI › Add worker)", file=sys.stderr)
        return 2

    def _stop(*_: Any) -> None:
        global STOP
        STOP = True
        log.info("stopping after the current poll; an unfinished job returns to the queue")

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    client = Client(a.server, a.token)
    short_market = Client(a.server, a.token, product="short-market")
    caps = capabilities()
    log.info("worker ready: %s", caps)
    backoff = a.idle
    shorts_after = 0.0  # a server without Short Market (not deployed yet) is asked again later, not every poll
    while not STOP:
        try:
            job = client.claim(caps)
            if job is None and time.monotonic() >= shorts_after:
                try:
                    job = short_market.claim(caps)
                except ServerError as exc:
                    if "HTTP 404" not in str(exc):
                        raise
                    shorts_after = time.monotonic() + 600
        except (ServerError, httpx.HTTPError) as exc:
            log.warning("server: %s (retrying in %.0fs)", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        backoff = a.idle
        if job is None:
            if a.once:
                return 0
            time.sleep(a.idle)
            continue
        log.info("job %s: %s", job["job_id"], job["topic"])
        owner = short_market if job.get("kind") == "short" else client
        try:
            if owner is short_market:
                shorts.process(owner, job, a.keep)
            else:
                process(owner, job, a.keep)
        except (Cancelled, shorts.Cancelled) as exc:
            log.warning("job %s: %s", job["job_id"], exc)
        except pipeline4.EditorialReview as exc:  # not enough real media: an editor decides (add footage or skip), no retries
            log.warning("job %s: %s", job["job_id"], exc)
            owner.fail(job["job_id"], str(exc), retryable=False)
        except Exception as exc:  # noqa: BLE001 - reported to the server, which retries or fails it
            log.exception("job %s failed", job["job_id"])
            owner.fail(job["job_id"], f"{type(exc).__name__}: {exc}", retryable=not isinstance(exc, RuntimeError) or "fact" not in str(exc))
        if a.once:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
