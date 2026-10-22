"""The GPU process for Short Market: loads Wan 2.2 TI2V-5B once and draws every scene of one story as a short clip.

    python gpu_clips.py <model dir> <job.json>

job.json: {"width", "height", "steps", "fps", "negative", "clips": [{"prompt", "out", "frames", "seed"}]}
Runs with the GPU environment's Python (see gpu.py), as its own process, so the GPU memory is fully released when the
story is done. Reports one JSON object per line on stdout: {"ready"}, {"clip", "step", "steps"}, {"done"}, {"error"}.
A scene that fails is reported and skipped; the worker draws a still for it.
"""

import json
import sys
import time
import traceback
from pathlib import Path


def emit(**kw) -> None:
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def main() -> None:
    model_dir = sys.argv[1]
    job = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    import torch
    from diffusers import AutoencoderKLWan, WanPipeline
    from diffusers.utils import export_to_video

    t0 = time.time()
    vae = AutoencoderKLWan.from_pretrained(model_dir, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanPipeline.from_pretrained(model_dir, vae=vae, torch_dtype=torch.bfloat16)
    pipe.enable_model_cpu_offload()  # text encoder / transformer / VAE take turns on the GPU: about 14 GB at the peak
    pipe.vae.enable_tiling()
    pipe.set_progress_bar_config(disable=True)
    emit(ready=True, load_seconds=round(time.time() - t0, 1), gpu=torch.cuda.get_device_name(0))

    steps = int(job["steps"])
    for k, clip in enumerate(job["clips"]):
        try:
            def on_step(p, i, t, kw, k=k):  # noqa: ANN001
                emit(clip=k, step=i + 1, steps=steps)
                return kw

            t1 = time.time()
            frames = pipe(prompt=clip["prompt"], negative_prompt=job["negative"], height=int(job["height"]), width=int(job["width"]),
                          num_frames=int(clip["frames"]), num_inference_steps=steps, guidance_scale=5.0, callback_on_step_end=on_step,
                          generator=torch.Generator("cpu").manual_seed(int(clip["seed"]))).frames[0]
            export_to_video(frames, clip["out"], fps=int(job["fps"]))
            emit(done=k, seconds=round(time.time() - t1, 1))
        except Exception as exc:  # noqa: BLE001 - reported; the worker draws a still for this scene
            emit(error=f"{type(exc).__name__}: {exc}", clip=k, trace=traceback.format_exc()[-1200:])
        finally:
            torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
