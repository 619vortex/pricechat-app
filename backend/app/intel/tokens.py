"""Make the PC's intel worker token: `python -m app.intel.tokens` (run by the owner, at a terminal).

Prints a new pci_ token ONCE, for the PC's worker.env (INTEL_WORKER_TOKEN), and its SHA-256 for Railway (INTEL_WORKER_TOKEN_SHA256).
Railway never sees the token itself. Setting a new hash on Railway retires the previous token immediately."""

import sys

from app.intel.channel import new_token


def main() -> int:
    token, digest = new_token()
    print("PC worker.env (keep secret, never commit):")
    print(f"  INTEL_WORKER_TOKEN={token}")
    print("Railway variable (safe to share, it's only a hash):")
    print(f"  INTEL_WORKER_TOKEN_SHA256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
