"""Starts the News Desk GPU picture worker (pricechat_worker.newsdesk_images) in the background, no console window, and keeps it running:
if it exits with an error it is started again after a pause. Run with the GPU Python (D:\pc-gpu\venv\Scripts\pythonw.exe), by the
Windows Task Scheduler task "Price Chat News Desk GPU" (install_newsdesk_images_task.ps1): at logon, and every 15 minutes as a watchdog
(a second copy exits at once). Settings from worker.env; models from PCW_HF_HOME (default D:\pc-gpu\hf). Log: newsdesk_images.log."""
import os
import socket
import sys
import time
import traceback
from pathlib import Path

_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", 47562))  # one per machine (the Brand Manager worker holds 47561)
except OSError:
    sys.exit(0)

HERE = Path(__file__).resolve().parent
for line in (HERE / "worker.env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
os.environ.setdefault("HF_HOME", os.environ.get("PCW_HF_HOME", r"D:\pc-gpu\hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the models are on disk; never download at 3 a.m.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
sys.path.insert(0, str(HERE))
out = open(HERE / "newsdesk_images.out.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = out

from pricechat_worker import newsdesk_images  # noqa: E402

pause = 30.0
while True:
    started = time.time()
    try:
        code = newsdesk_images.main([])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    if newsdesk_images.STOP or code == 2:  # stopped on purpose, or no token: don't spin
        sys.exit(code or 0)
    pause = 30.0 if time.time() - started > 600 else min(pause * 2, 900)
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} newsdesk_images exited ({code}); restarting in {pause:.0f}s", flush=True)
    time.sleep(pause)
