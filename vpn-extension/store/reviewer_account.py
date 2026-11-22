#!/usr/bin/env python3
"""Makes the store-review password and saves only its hash on Railway. Run it yourself, in your own terminal:

    python reviewer_account.py your.reviewer.address@gmail.com

The password goes to your clipboard and is never printed or written to a file. Paste it straight into your password manager.
Lost it? Run this again for a new one, then redeploy the `api` service so the new hash takes effect.
"""
import base64
import datetime as dt
import hashlib
import secrets
import shutil
import subprocess
import sys

PROJECT, ENVIRONMENT, SERVICE = "c2a48c3b-60de-4eec-80a3-1e7cf5ff3e26", "7c06bad6-768e-4625-9a69-7251e6225a89", "api"
DAYS = 30


def make_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    raw = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(raw).decode()}"


def main() -> None:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        sys.exit(__doc__)
    email = sys.argv[1].strip().lower()
    password = secrets.token_urlsafe(18)
    until = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=DAYS)).isoformat()
    npx = shutil.which("npx") or sys.exit("npx wasn't found: install Node.js first.")
    subprocess.run([npx, "@railway/cli", "variables", "--project", PROJECT, "--environment", ENVIRONMENT, "--service", SERVICE, "--skip-deploys",
                    "--set", f"VPN_REVIEWER_EMAIL={email}", "--set", f"VPN_REVIEWER_PASSWORD_HASH={make_hash(password)}",
                    "--set", f"VPN_REVIEWER_UNTIL={until}"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["clip"], input=password.encode("utf-16-le"), check=True)
    print(f"Done. Reviewer email: {email}. Access ends: {until} (UTC).")
    print("The password is on your clipboard. Paste it into your password manager now.")


if __name__ == "__main__":
    main()
