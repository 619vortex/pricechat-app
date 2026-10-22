"""First production start: restore the database and secret store that were shipped with the deploy.

`prod-bootstrap/` (next to `app/`) may hold a gzipped SQLite snapshot and secret store plus a manifest with their
SHA-256. On a PRODUCTION start (ENVIRONMENT=production), BEFORE any migration or connection:
  - a file is restored only when its target does NOT exist yet (an existing database is never overwritten);
  - the checksum must match, the decompressed copy is written next to the target and moved into place atomically;
  - nothing happens when there is no bundle (every deploy after the first one).
"""

import gzip
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUNDLE = Path(__file__).resolve().parents[2] / "prod-bootstrap"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _restore(src: Path, target: Path, expected_sha: str | None) -> str:
    if target.exists():
        return f"kept existing {target.name}"
    if not src.exists():
        return f"no bundled {src.name}"
    if expected_sha and _sha256(src) != expected_sha:
        raise RuntimeError(f"Bundled {src.name} is corrupt (checksum mismatch): refusing to restore it")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".restoring")
    with gzip.open(src, "rb") as fin, tmp.open("wb") as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return f"restored {target.name} ({target.stat().st_size:,} bytes)"


def restore_if_empty(settings: Any) -> list[str]:
    # production only: a development or test start must never pick up the shipped snapshot
    if not getattr(settings, "is_production", False) or not BUNDLE.exists() or not settings.database_url.startswith("sqlite:///"):
        return []
    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    sums = manifest.get("sha256", {})
    db = Path(settings.database_url.removeprefix("sqlite:///"))
    out = [_restore(BUNDLE / "price_chat.db.gz", db, sums.get("price_chat.db.gz")),
           _restore(BUNDLE / "secrets.json.gz", Path(settings.secrets_file), sums.get("secrets.json.gz"))]
    for line in out:
        logger.info("Bootstrap: %s", line)
    return out
