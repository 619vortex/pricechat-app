#!/usr/bin/env python3
"""The packages: only what the browser needs, with fixed timestamps so the same files give the same zip.

The testers' download keeps the manifest's `key`, which fixes the id of a Load unpacked install. The stores refuse a new item with a `key` and give it
their own id, so their packages leave it out and sit at the top of the zip (no folder), as the stores expect."""
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "frontend", "public", "downloads", "kiwi-vpn-extension.zip")
FILES = ["manifest.json", "background.js", "lib.js", "popup.html", "popup.css", "popup.js", "icons/16.png", "icons/32.png", "icons/48.png", "icons/128.png"]

DIST = os.path.join(HERE, "store", "dist")


def read(name):
    with open(os.path.join(HERE, name), "rb") as f:
        data = f.read()
    return data.replace(b"\r\n", b"\n") if name.endswith((".js", ".json", ".css", ".html")) else data


def write(path, prefix, manifest):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            info = zipfile.ZipInfo(prefix + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, manifest if name == "manifest.json" else read(name))
    print(os.path.relpath(path, os.path.join(HERE, "..")), os.path.getsize(path), "bytes")


plain = json.loads(read("manifest.json"))
write(OUT, "kiwi-vpn-extension/", read("manifest.json"))
store = json.dumps({k: v for k, v in plain.items() if k != "key"}, indent=2).encode() + b"\n"
for browser in ("chrome", "edge"):
    path = os.path.join(DIST, f"kiwi-vpn-{browser}-{plain['version']}.zip")
    write(path, "", store)
    with zipfile.ZipFile(path) as z:  # read back what was really written: the stores refuse a `key`, and want the manifest at the top
        built = json.loads(z.read("manifest.json"))
        assert "key" not in built, f"{path} has a key"
        assert sorted(z.namelist()) == sorted(FILES), f"{path} has the wrong files"
        assert z.testzip() is None, f"{path} is damaged"
