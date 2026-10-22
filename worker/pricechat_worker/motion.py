"""The motion renderer (worker/motion, Remotion): storyboard JSON -> 1080×1920 30 fps H.264/AAC MP4.

Needs Node.js 18+ and `npm install` in worker/motion (Remotion downloads its own headless Chrome on first use). Without it
the worker keeps using the v1 Pillow + FFmpeg renderer."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

MOTION = Path(__file__).resolve().parent.parent / "motion"
STATIC = MOTION / "static"


def available() -> bool:
    return bool(shutil.which("node")) and (MOTION / "node_modules" / "@remotion" / "renderer").exists()


def public_dir(workdir: Path, music: str | None) -> Path:
    """The render's public folder: fonts, Kiwi, sound effects, the chosen music bed (the voice and card are added later)."""
    pub = workdir / "public"
    for sub in ("fonts", "sfx"):
        shutil.copytree(STATIC / sub, pub / sub, dirs_exist_ok=True)
    shutil.copy(STATIC / "kiwi.png", pub / "kiwi.png")
    if music:
        (pub / "music").mkdir(parents=True, exist_ok=True)
        shutil.copy(STATIC / "music" / f"{music}.wav", pub / "music" / f"{music}.wav")
    return pub


def render(props: dict[str, Any], public: Path, out: Path, stills: list[tuple[int, Path]] | None = None, video: bool = True,
           on_progress: Any = None, timeout: int = 1500) -> dict[str, Any]:
    pfile = public.parent / "props.json"
    pfile.write_text(json.dumps(props), encoding="utf-8")
    args = ["node", str(MOTION / "render.mjs"), str(pfile), str(public), str(out)]
    for frame, path in stills or []:
        args += ["--still", str(frame), str(path)]
    if not video:
        args.append("--no-video")
    proc = subprocess.Popen(args, cwd=str(MOTION), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    done: dict[str, Any] = {}
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("done"):
            done = msg
        elif on_progress:
            on_progress(msg)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("The motion render took too long.") from None
    err = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0 or not done:
        tail = [x for x in err.strip().splitlines() if x.strip()][-3:]
        raise RuntimeError("Motion render failed: " + (" | ".join(tail)[:400] or f"exit {proc.returncode}"))
    return done
