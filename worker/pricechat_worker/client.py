"""The worker's side of the Brand Manager job protocol: outbound HTTPS to the Price Chat backend, bearer token."""

import json
from pathlib import Path
from typing import Any

import httpx


class ServerError(Exception):
    pass


class Client:
    def __init__(self, server: str, token: str, timeout: float = 60.0, product: str = "brand-manager") -> None:
        self.base = server.rstrip("/") + f"/api/{product}/worker"  # brand-manager | short-market: the same protocol
        self.http = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {token}", "User-Agent": "pricechat-worker/1.0"})

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        r = self.http.post(self.base + path, json=body)
        if r.status_code == 401:
            raise ServerError("The server refused this worker's token (deleted or disabled in Brand Manager › Settings › AI).")
        if r.status_code >= 400 and r.status_code != 409:
            raise ServerError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        return r

    def claim(self, info: dict[str, Any]) -> dict[str, Any] | None:
        r = self._post("/claim", {"info": info})
        return None if r.status_code == 204 else r.json()

    def progress(self, job_id: int, stage: str, status: str, note: str = "") -> bool:
        """True = keep going; False = the job was cancelled (emergency stop) or taken away."""
        r = self._post(f"/jobs/{job_id}/progress", {"stage": stage, "status": status, "note": note[:500]})
        if r.status_code == 409:
            return False
        return not r.json().get("cancel")

    def script(self, job_id: int, script: dict[str, Any]) -> dict[str, Any]:
        r = self._post(f"/jobs/{job_id}/script", {"script": script})
        if r.status_code == 409:
            raise ServerError(r.json().get("detail", "job lost"))
        return r.json()

    def media(self, job_id: int, assets: list[dict[str, Any]]) -> None:
        """Content Studio v4: the media research's findings (with rights records) for the video's media library."""
        try:
            self._post(f"/jobs/{job_id}/media", {"assets": assets[:200]})
        except (ServerError, httpx.HTTPError):
            pass  # the library also rides along with the finished video

    def result(self, job_id: int, video: Path, thumb: Path | None, meta: dict[str, Any]) -> dict[str, Any]:
        with video.open("rb") as vf:
            files: dict[str, Any] = {"video": ("video.mp4", vf, "video/mp4")}
            tf = thumb.open("rb") if thumb else None
            try:
                if tf:
                    files["thumb"] = ("thumb.jpg", tf, "image/jpeg")
                r = self.http.post(f"{self.base}/jobs/{job_id}/result", files=files, data={"meta": json.dumps(meta)}, timeout=600.0)
            finally:
                if tf:
                    tf.close()
        if r.status_code >= 400:
            raise ServerError(f"upload: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    def fail(self, job_id: int, error: str, retryable: bool = True) -> None:
        try:
            self._post(f"/jobs/{job_id}/fail", {"error": error[:1900], "retryable": retryable})
        except (ServerError, httpx.HTTPError):
            pass  # the lease will expire and the job returns to the queue
