"""Where the worker's GPU things live. The worker itself stays light (httpx, Pillow, FFmpeg); the video model and the
neural voice run in their own processes with a second Python environment that has PyTorch (worker/.venv-gpu, or
PCW_GPU_PYTHON). On a machine without it the worker still works: Short Market then draws still scenes and says so.

Set up (once):  python -m venv .venv-gpu   →   pip install -r requirements-gpu.txt   (see worker/README.md)
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # worker/
MODELS = Path(os.environ.get("PCW_MODELS") or ROOT / "models")
VIDEO_MODEL = "Wan2.2-TI2V-5B-Diffusers"  # Wan-AI/Wan2.2-TI2V-5B-Diffusers: Apache-2.0, text -> video, fits a 24 GB GPU

# (width, height, denoising steps): what one scene costs. Width and height are multiples of 32.
QUALITY = {"fast": (480, 832, 20), "standard": (576, 1024, 22), "best": (704, 1280, 26)}
FPS = 24
MIN_FRAMES, MAX_FRAMES = 49, 121  # 2 s .. 5 s; the model wants 4n+1 frames


def python() -> Path | None:
    """The Python of the GPU environment, or None when this machine has none."""
    if os.environ.get("PCW_GPU_PYTHON"):
        p = Path(os.environ["PCW_GPU_PYTHON"])
        return p if p.exists() else None
    p = ROOT / ".venv-gpu" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return p if p.exists() else None


def video_model() -> Path | None:
    p = Path(os.environ.get("PCW_VIDEO_MODEL") or MODELS / VIDEO_MODEL)
    return p if (p / "model_index.json").exists() else None


def video_ready() -> bool:
    return python() is not None and video_model() is not None


def frames_for(seconds: float) -> int:
    n = int(seconds * FPS + 0.999)
    return max(MIN_FRAMES, min(MAX_FRAMES, (n + 2) // 4 * 4 + 1))
