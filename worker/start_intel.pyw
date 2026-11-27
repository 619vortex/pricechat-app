"""Starts the Terminal Intelligence Engine worker (pricechat_worker.intel) in the background, no console window, and keeps it running:
if it exits with an error it is started again after a pause. Any Python with httpx will do (the worker needs no GPU libraries; Ollama
does the GPU work), e.g. D:\pc-gpu\venv\Scripts\pythonw.exe. Nothing is installed by this file: start it by hand, or register the
optional Task Scheduler task with install_intel_task.ps1 (the owner runs that, at a terminal). A second copy exits at once.
Settings from worker.env (INTEL_WORKER_TOKEN, PRICECHAT_SERVER, INTEL_MODEL, INTEL_EMBED_MODEL). Log: intel.log (the token is never logged).
The worker only calls out; the lock below binds 127.0.0.1 and never listens."""
import os
import socket
import sys
import time
import traceback
from pathlib import Path

_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", 47564))  # one per machine (47561 Brand Manager, 47562 News Desk GPU, 47563 World Monitor)
except OSError:
    sys.exit(0)

HERE = Path(__file__).resolve().parent
env = HERE / "worker.env"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, str(HERE))
out = open(HERE / "intel.out.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = out

from pricechat_worker import intel  # noqa: E402

pause = 30.0
while True:
    started = time.time()
    try:
        code = intel.main([])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    if intel.STOP or code == 2:  # stopped on purpose, or no / wrong token: don't spin
        sys.exit(code or 0)
    pause = 30.0 if time.time() - started > 600 else min(pause * 2, 900)
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} intel exited ({code}); restarting in {pause:.0f}s", flush=True)
    time.sleep(pause)
