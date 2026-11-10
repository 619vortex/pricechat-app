"""News Desk picture backgrounds on this PC's GPU (backend: app/newsdesk/render_queue.py).

Runs on its own, beside the Brand Manager worker, with the GPU Python environment (the one with PyTorch and diffusers):

    <gpu python> -m pricechat_worker.newsdesk_images            (reads worker.env: PRICECHAT_SERVER, PRICECHAT_WORKER_TOKEN)

It only ever calls out (HTTPS to the server, the worker's own token); nothing on this PC is reachable from outside. Loop: claim a job,
render the text-free 16:9 background with the chosen open-weight model, upload it, repeat. The model stays loaded while there is work
and is unloaded after PCW_IMAGE_IDLE_UNLOAD seconds idle, so the video jobs can have the GPU. Network errors back off; a failed render
is reported (the server retries it with a new seed, or the post goes out with the desk's own drawn visual).

Models (all Apache-2.0, commercial use allowed): klein = black-forest-labs/FLUX.2-klein-4B (default), zimage = Tongyi-MAI/Z-Image-Turbo,
qwen = Qwen/Qwen-Image-2512.
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
from typing import Any

import httpx

log = logging.getLogger("newsdesk_images")
ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.0"
STOP = False

MODELS = {
    "klein": ("black-forest-labs/FLUX.2-klein-4B", {"num_inference_steps": 4, "guidance_scale": 1.0}),
    "zimage": ("Tongyi-MAI/Z-Image-Turbo", {"num_inference_steps": 9, "guidance_scale": 0.0}),
    "qwen": ("Qwen/Qwen-Image-2512", {"num_inference_steps": 30, "true_cfg_scale": 4.0}),
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class Renderer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.repo, self.defaults = MODELS[name]
        self.pipe: Any = None
        self.loaded_at = 0.0
        self.used_at = 0.0

    def load(self) -> None:
        if self.pipe is not None:
            return
        import torch

        t = time.time()
        if self.name == "klein":
            from diffusers import Flux2KleinPipeline as P
        elif self.name == "zimage":
            from diffusers import ZImagePipeline as P
        else:
            from diffusers import QwenImagePipeline as P
        pipe = P.from_pretrained(self.repo, torch_dtype=torch.bfloat16)
        if self.name == "qwen":
            pipe.transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16)
            pipe.enable_model_cpu_offload()
        else:
            pipe = pipe.to("cuda")
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
        log.info("unloaded %s (idle)", self.name)

    def render(self, job: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        import torch

        self.load()
        kw = dict(self.defaults)
        if self.name == "qwen" and job.get("negative"):
            kw["negative_prompt"] = job["negative"]
        g = torch.Generator("cuda").manual_seed(int(job.get("seed") or 1))
        torch.cuda.synchronize()
        t = time.time()
        img = self.pipe(prompt=job["prompt"], width=int(job.get("width") or 1536), height=int(job.get("height") or 864), generator=g, **kw).images[0]
        torch.cuda.synchronize()
        secs = time.time() - t
        self.used_at = time.time()
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=95)
        return out.getvalue(), {"model": self.repo, "seconds": round(secs, 2), "steps": kw.get("num_inference_steps"), "seed": job.get("seed"),
                                "gpu": torch.cuda.get_device_name(0), "vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 1)}


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


def main(argv: list[str] | None = None) -> int:
    load_env(ROOT / "worker.env")
    ap = argparse.ArgumentParser(prog="newsdesk_images")
    ap.add_argument("--server", default=os.environ.get("PRICECHAT_SERVER", "https://api.pricechat.live"))
    ap.add_argument("--token", default=os.environ.get("PRICECHAT_WORKER_TOKEN"))
    ap.add_argument("--model", default=os.environ.get("PCW_IMAGE_MODEL", "klein"), choices=sorted(MODELS))
    ap.add_argument("--idle", type=float, default=5.0)
    ap.add_argument("--unload-after", type=float, default=float(os.environ.get("PCW_IMAGE_IDLE_UNLOAD", "600")))
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "newsdesk_images.log", encoding="utf-8")])
    if not a.token:
        print("No worker token (PRICECHAT_WORKER_TOKEN in worker.env, or --token)", file=sys.stderr)
        return 2

    def _stop(*_: Any) -> None:
        global STOP
        STOP = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    server, renderer = Server(a.server, a.token), Renderer(a.model)
    try:
        import torch

        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001
        gpu = None
    info = {"version": VERSION, "model": MODELS[a.model][0], "gpu": gpu}
    log.info("newsdesk images ready: %s", info)
    backoff = a.idle
    while not STOP:
        try:
            job = server.claim({**info, "loaded": renderer.pipe is not None})
        except httpx.HTTPError as exc:
            log.warning("server: %s (retrying in %.0fs)", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        backoff = a.idle
        if job is None:
            if renderer.pipe is not None and time.time() - renderer.used_at > a.unload_after:
                renderer.unload()
            if a.once:
                return 0
            time.sleep(a.idle)
            continue
        log.info("job %s (event %s): %s", job["id"], job["event_id"], job.get("scene"))
        try:
            data, result = renderer.render(job)
        except Exception as exc:  # noqa: BLE001 - reported; the server retries or the post uses its own visual
            log.exception("job %s failed", job["id"])
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            try:
                server.fail(job["id"], f"{type(exc).__name__}: {exc}")
            except httpx.HTTPError:
                pass
            continue
        try:
            got = server.result(job["id"], data, result)
            log.info("job %s: %s in %.1fs", job["id"], "accepted" if got.get("ok") else f"rejected {got.get('rejected')}", result["seconds"])
        except httpx.HTTPError as exc:
            log.warning("job %s: upload failed (%s); the lease will expire and it goes back to the queue", job["id"], exc)
        if a.once:
            return 0
    renderer.unload()
    return 0


if __name__ == "__main__":
    sys.exit(main())
