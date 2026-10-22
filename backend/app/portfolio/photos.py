"""Portfolio photos: validated, re-encoded (no EXIF / GPS metadata), resized, thumbnailed, and stored in a PRIVATE
S3-compatible bucket (Railway bucket in production). Never on the app server's disk in production, never public URLs:
the API streams a photo only after checking that the viewer may see it (owner, or a public card in a public portfolio).

Development without a bucket: files under data/uploads (refused in production, where a bucket is required).
"""

import hashlib
import io
import secrets
from pathlib import Path
from typing import Any

from app.config.settings import Settings

MAX_UPLOAD = 15 * 1024 * 1024  # bytes accepted from the browser (phones produce 2-8 MB photos)
MAX_PIXELS = 50_000_000  # refuses decompression bombs
FULL_SIDE = 1600  # longest side kept
THUMB_SIDE = 420
FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}  # MPO = multi-picture JPEG some phones produce


class PhotoError(ValueError):
    pass


def process(data: bytes) -> tuple[bytes, bytes, int, int, str]:
    """(full jpeg, thumb jpeg, width, height, sha256 of the original). Raises PhotoError for anything that isn't a sane image."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not data or len(data) > MAX_UPLOAD:
        raise PhotoError(f"Photos up to {MAX_UPLOAD // (1024 * 1024)} MB.")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        pw, ph = probe.size
        # PIL itself only REFUSES above 2 x MAX_IMAGE_PIXELS (it merely warns in between): a small, highly compressed PNG
        # of ~100 MP would still be decoded (~400 MB, and more for the RGB copy). JPEGs are decoded reduced (draft below).
        if fmt not in ("JPEG", "MPO") and pw * ph > MAX_PIXELS:
            raise Image.DecompressionBombError(f"{pw}x{ph}")
        probe.verify()  # structural check before decoding
        img = Image.open(io.BytesIO(data))
        if img.format in ("JPEG", "MPO"):
            img.draft("RGB", (FULL_SIDE * 2, FULL_SIDE * 2))  # decode at >= 2x the kept size, not the full 12-50 MP
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError) as exc:
        raise PhotoError("That file isn't a photo we can read (JPEG, PNG or WebP).") from exc
    if fmt not in FORMATS:
        raise PhotoError("Photos must be JPEG, PNG or WebP.")
    img = ImageOps.exif_transpose(img)  # phone orientation applied, then all metadata dropped by re-encoding
    img = img.convert("RGB")
    w, h = img.size
    if min(w, h) < 200:
        raise PhotoError("That photo is too small to see the card. Use at least 800 px.")

    def jpeg(im: Any, side: int, quality: int) -> bytes:
        im = im.copy()
        im.thumbnail((side, side), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True, progressive=True)  # no exif / icc / comments written
        return out.getvalue()

    full = jpeg(img, FULL_SIDE, 86)
    thumb = jpeg(img, THUMB_SIDE, 80)
    fw, fh = (Image.open(io.BytesIO(full)).size)
    return full, thumb, fw, fh, hashlib.sha256(data).hexdigest()


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.portfolio_bucket
        self.local = Path(settings.portfolio_upload_dir)

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
                            config=Config(signature_version="s3v4", retries={"max_attempts": 3}, connect_timeout=10, read_timeout=30,
                                          s3={"addressing_style": "virtual"}))  # Railway buckets: virtual-host style

    def put(self, key: str, data: bytes) -> None:
        if self.bucket:
            self._s3().put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="image/jpeg", CacheControl="private, max-age=31536000")
            return
        if self.settings.is_production:
            raise PhotoError("Photo storage isn't set up yet.")
        path = self.local / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        if self.bucket:
            return self._s3().get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return (self.local / key).read_bytes()

    def delete(self, key: str) -> None:
        if self.bucket:
            self._s3().delete_object(Bucket=self.bucket, Key=key)
            return
        (self.local / key).unlink(missing_ok=True)


def new_keys(portfolio_id: int) -> tuple[str, str]:
    """Unguessable object keys (the bucket is private anyway)."""
    token = secrets.token_urlsafe(18)
    return f"portfolio/{portfolio_id}/{token}.jpg", f"portfolio/{portfolio_id}/{token}_t.jpg"
