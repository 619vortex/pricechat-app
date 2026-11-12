"""Starts the World Monitor bridge (pricechat_worker.worldmonitor) in the background, no console window, and keeps it running: if it
exits with an error it is started again after a pause. Run by the Windows Task Scheduler task "Price Chat World Monitor"
(install_worldmonitor_task.ps1): at logon, and every 15 minutes as a watchdog (a second copy exits at once). Settings from worker.env.
Log: worldmonitor.out.log."""
import os
import socket
import sys
import time
import traceback
from pathlib import Path

_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", 47563))  # one per machine (47561 Brand Manager, 47562 News Desk GPU)
except OSError:
    sys.exit(0)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
out = open(HERE / "worldmonitor.out.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = out

from pricechat_worker import worldmonitor  # noqa: E402

pause = 30.0
while True:
    started = time.time()
    try:
        code = worldmonitor.main([])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    if worldmonitor.STOP or code == 2:  # stopped on purpose, or no token / app not installed: don't spin
        sys.exit(code or 0)
    pause = 30.0 if time.time() - started > 600 else min(pause * 2, 900)
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} worldmonitor exited ({code}); restarting in {pause:.0f}s", flush=True)
    time.sleep(pause)
