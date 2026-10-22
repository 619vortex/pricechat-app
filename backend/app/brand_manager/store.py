"""Object storage for Brand Manager media (videos, thumbnails): the same private S3-compatible bucket as the Portfolio
(PORTFOLIO_BUCKET…), under the brand-manager/ prefix. Never the database, never the server's small volume.
Without a bucket (local development) files go under data/uploads/brand-manager/."""

import hashlib
import hmac
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from app.config.settings import Settings
from app.security.credentials import secret_store

PREFIX = "brand-manager/"


class StorageError(Exception):
    pass


class MediaStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.portfolio_bucket
        self.local = Path(settings.portfolio_upload_dir) / "brand-manager"

    @property
    def available(self) -> bool:
        return bool(self.bucket) or not self.settings.is_production

    def _s3(self) -> Any:
        import boto3
        from botocore.config import Config

        s = self.settings
        return boto3.client("s3", endpoint_url=s.portfolio_s3_endpoint or None, region_name=s.portfolio_s3_region or "auto",
                            aws_access_key_id=s.portfolio_s3_key_id.get_secret_value() if s.portfolio_s3_key_id else None,
                            aws_secret_access_key=s.portfolio_s3_secret.get_secret_value() if s.portfolio_s3_secret else None,
                            config=Config(signature_version="s3v4", retries={"max_attempts": 3}, connect_timeout=10, read_timeout=60,
                                          s3={"addressing_style": "virtual"}))

    def put_file(self, key: str, fileobj: IO[bytes], content_type: str) -> None:
        """Streams the file (never loads a whole video into memory)."""
        key = PREFIX + key
        if self.bucket:
            self._s3().upload_fileobj(fileobj, self.bucket, key, ExtraArgs={"ContentType": content_type, "CacheControl": "private, max-age=86400"})
            return
        if self.settings.is_production:
            raise StorageError("Object storage isn't set up (PORTFOLIO_BUCKET).")
        path = self.local / key.removeprefix(PREFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as out:
            while chunk := fileobj.read(1 << 20):
                out.write(chunk)

    def size(self, key: str) -> int | None:
        try:
            if self.bucket:
                return int(self._s3().head_object(Bucket=self.bucket, Key=PREFIX + key)["ContentLength"])
            return (self.local / key).stat().st_size
        except Exception:  # noqa: BLE001 - missing object
            return None

    def stream(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        if self.bucket:
            rng = f"bytes={start}-{'' if end is None else end}"
            body = self._s3().get_object(Bucket=self.bucket, Key=PREFIX + key, Range=rng)["Body"]
            while chunk := body.read(1 << 18):
                yield chunk
            return
        with (self.local / key).open("rb") as f:
            f.seek(start)
            left = None if end is None else end - start + 1
            while True:
                chunk = f.read(1 << 18 if left is None else min(1 << 18, left))
                if not chunk:
                    return
                yield chunk
                if left is not None:
                    left -= len(chunk)
                    if left <= 0:
                        return

    def delete(self, key: str) -> None:
        if self.bucket:
            self._s3().delete_object(Bucket=self.bucket, Key=PREFIX + key)
            return
        (self.local / key).unlink(missing_ok=True)


# --- signed media URLs (TikTok's PULL_FROM_URL fetches the video from our verified domain) ---------------------------


def _media_secret(settings: Settings) -> bytes:
    store = secret_store(settings)
    value = store.get("bm_media_secret")
    if not value:
        value = secrets.token_urlsafe(32)
        store.set("bm_media_secret", value)
    return value.encode()


def sign(settings: Settings, video_id: int, expires: int) -> str:
    return hmac.new(_media_secret(settings), f"{video_id}:{expires}".encode(), hashlib.sha256).hexdigest()[:40]


def media_url(settings: Settings, video_id: int, kind: str = "video", ttl: int = 6 * 3600) -> str:
    expires = int(time.time()) + ttl
    ext = "mp4" if kind == "video" else "jpg"
    return f"{settings.bm_media_base_url.rstrip('/')}/api/brand-manager/media/{video_id}/{expires}/{sign(settings, video_id, expires)}/{kind}.{ext}"


def verify(settings: Settings, video_id: int, expires: int, signature: str) -> bool:
    return expires >= time.time() and hmac.compare_digest(sign(settings, video_id, expires), signature)
