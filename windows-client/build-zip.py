"""Packs what the VM needs, from what's committed: sources, scripts and the WireGuard DLLs. It holds no secrets. Usage: python build-zip.py <output.zip>"""
import os
import subprocess
import sys
import zipfile

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
paths = ["app", "client", "service", "docs/VM-CHECKLIST.md", "BUILDING.md", "build-app-in-vm.ps1", "reconnect-test.ps1", "fake-service-test.ps1", "ipc-and-key-test.ps1", "scan-for-key.ps1"]
files = subprocess.check_output(["git", "-C", root, "ls-files", *[f"windows-client/{p}" for p in paths]], text=True).split("\n")
files = [f for f in files if f]
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    for f in files:
        z.write(os.path.join(root, f), f[len("windows-client/"):])
    commit = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
    z.writestr("BUILD-INFO.txt", f"commit {commit}\n")  # goes back with the installer, so the main PC can tell which source it came from
    for n in ["tunnel.dll", "wireguard.dll"]:
        z.write(os.path.join(root, "windows-client", "dist", n), "dist/" + n)
print(len(files) + 2, "files", os.path.getsize(sys.argv[1]) // 1024, "KB")
