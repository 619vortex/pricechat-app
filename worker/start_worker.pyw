"""Starts the Price Chat AI worker in the background with the settings in worker.env (no console window).
Logs go to worker.log next to this file. Used by the Windows Startup shortcut."""
import os
import socket
import sys
from pathlib import Path

# One worker per machine: a second copy (e.g. Startup + a manual start) exits quietly.
_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", 47561))
except OSError:
    sys.exit(0)

HERE = Path(__file__).resolve().parent
for line in (HERE / "worker.env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, str(HERE))
log = open(HERE / "worker.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log
from pricechat_worker.__main__ import main  # noqa: E402

sys.exit(main(["--keep", str(HERE / "out")]))
