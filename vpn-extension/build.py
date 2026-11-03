#!/usr/bin/env python3
"""The download for testers: only what the browser needs, with fixed timestamps so the same files give the same zip."""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "frontend", "public", "downloads", "kiwi-vpn-extension.zip")
FILES = ["manifest.json", "background.js", "lib.js", "popup.html", "popup.css", "popup.js", "icons/16.png", "icons/32.png", "icons/48.png", "icons/128.png"]

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for name in FILES:
        info = zipfile.ZipInfo(f"kiwi-vpn-extension/{name}", (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(os.path.join(HERE, name), "rb") as f:
            z.writestr(info, f.read().replace(b"\r\n", b"\n") if name.endswith((".js", ".json", ".css", ".html")) else f.read())
print(os.path.relpath(OUT, os.path.join(HERE, "..")), os.path.getsize(OUT), "bytes")
