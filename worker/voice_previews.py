"""Preview clips of every narrator voice, for the voice menus' play buttons (Brand Manager, Content Studio, Short Market).

    .venv/Scripts/python voice_previews.py [--out ../frontend/public/voices] [--force]

Voices come from the backend's catalog (app/content/voices.py) plus the editorial narrator presets; each clip is one short
line read by the local Kokoro model, mastered like a real video's voice (voice2._master) and saved as a small mono MP3 at
<out>/<preview_slug(key)>.mp3. Existing clips are kept unless --force."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

from app.content import voices  # noqa: E402

from pricechat_worker import editorial, gpu, render, tts, voice2  # noqa: E402

LINE = "Bitcoin just jumped four percent in an hour. Here's what moved it, and what to watch next."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parent / "frontend" / "public" / "voices"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if not gpu.python() or not all(f.exists() for f in tts.kokoro_files()):
        sys.exit("Kokoro isn't installed on this worker (the GPU environment + worker/models/kokoro).")
    # key -> (voice, speed): every catalog voice at the brisk default speed, each preset with its own voice and speed
    want = {k: (k, tts.KOKORO_SPEED) for k in voices.ALL}
    want.update({k: (n["voice"], n["speed"]) for k, n in editorial.NARRATORS.items()})
    todo = {k: v for k, v in want.items() if a.force or not (out / f"{voices.preview_slug(k)}.mp3").exists()}
    if not todo:
        print(f"all {len(want)} previews exist in {out}")
        return
    ff = render.ffmpeg_exe()
    model, bank = tts.kokoro_files()
    with tempfile.TemporaryDirectory() as tmp:
        items = [{"text": tts.spoken(LINE), "voice": v, "speed": s, "out": str(Path(tmp) / f"{voices.preview_slug(k)}.raw.wav")}
                 for k, (v, s) in todo.items()]
        p = subprocess.run([str(gpu.python()), str(HERE / "pricechat_worker" / "kokoro_tts.py"), str(model), str(bank), items[0]["voice"], "1.0"],
                           input=json.dumps(items).encode(), capture_output=True, timeout=1800)
        if p.returncode != 0:
            sys.exit("Kokoro failed: " + p.stderr.decode(errors="replace")[-600:])
        for k, item in zip(todo, items):
            raw = Path(item["out"])
            wav = raw.with_suffix("").with_suffix(".wav")
            voice2._master(ff, raw, wav)
            mp3 = out / f"{voices.preview_slug(k)}.mp3"
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1", "-b:a", "64k", str(mp3)], check=True, timeout=120)
            print(f"{k:32} {mp3.stat().st_size // 1024} KB")
    print(f"{len(todo)} previews written to {out}")


if __name__ == "__main__":
    main()
