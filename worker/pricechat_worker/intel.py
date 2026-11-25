"""Terminal Intelligence Engine: the owner-PC side of the intel channel (backend: app/intel/channel.py, app/api/routes/intel_worker.py).

Runs on its own, beside the other workers, with any Python that has httpx (it needs no GPU libraries; Ollama does the GPU work):

    <python> -m pricechat_worker.intel            (reads worker.env: INTEL_WORKER_TOKEN, PRICECHAT_SERVER)

It only ever calls OUT: HTTPS to Railway (/api/intel-worker/*) with its own pci_ token, and HTTP to the local Ollama. It opens no port
(the launcher holds a 127.0.0.1-only lock port so a second copy exits at once; nothing listens on it).

Loop
- heartbeat every INTEL_HEARTBEAT_S (30 s) from a background thread: the installed Ollama model names (/api/tags), version, busy, GPU.
  Railway offers jobs only while that heartbeat is under 60 s old. Ollama down: models=[] and busy=true, and nothing is claimed.
- while idle and the analysis or embedding model is installed: claim up to INTEL_MAX_CLAIM jobs (1..4) every INTEL_POLL_S (5 s).
- intel.analyze: Ollama /api/chat with format=<the job's JSON schema>, temperature 0, num_ctx 4096, a short keep_alive (so the News Desk
  picture models get the GPU back), think off for qwen3*/deepseek and "low" for gpt-oss (as ops/intel/bench.py does).
- intel.embed: Ollama /api/embed with nomic-embed-text, "search_document: " / "search_query: " prefix by purpose, Matryoshka-truncated to
  the job's dim (layer norm, cut, then L2 normalise, as the nomic v1.5 model card does).
- every result and failure is HMAC-signed with the token and the job's single-use nonce (canonical()/sign() are byte-for-byte the same as
  the server's). Any error on a job posts a signed /fail, so Railway answers it at once instead of waiting out the 120 s lease.
- Railway unreachable: exponential back-off (5 s .. 5 min), the loop never exits on a network error. 401: the token is refused (not yet
  configured on Railway, or rotated out): retried every 5 minutes.

The token is never logged. The worker can't publish anything: it only proposes labels, which Railway re-validates against the schema.

GPU courtesy: the News Desk picture worker (newsdesk_images.py) shares this GPU but exposes no busy signal to other processes (it keeps
its model loaded up to 10 min after a job), so the two are not coordinated beyond Ollama's short keep_alive here. If they collide, Ollama
or diffusers runs slower; nothing fails.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

log = logging.getLogger("intel")
ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.0.0"
STOP = False

PREFIX = "/api/intel-worker"
DEFAULT_SERVER = "https://api.pricechat.live"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.8:27b"  # primary (owner 2026-11-25, Phase 0 benchmark); qwen3.6:27b is the fallback
DEFAULT_EMBED = "nomic-embed-text"
MAX_CLAIM = 4  # the server's cap (channel.MAX_CLAIM)
LEASE_MARGIN_S = 8.0  # don't start a job with less lease than this left: hand it straight back
MAX_ERROR = 480  # FailIn.error max_length is 500
EMBED_PREFIX = {"document": "search_document: ", "query": "search_query: "}


class JobError(Exception):
    """A job that can't be done as asked: reported to the server (signed) with this message."""


class TokenRefused(Exception):
    """401 from Railway: the token isn't (or is no longer) the configured one."""


# --- signatures: byte-for-byte the same as backend/app/intel/channel.py ----------------------------------------------------------


def canonical(obj: Any) -> bytes:
    """The exact bytes both sides sign (backend/app/intel/channel.py has the same function)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sign(token: str, job_id: int, nonce: str, obj: Any) -> str:
    return hmac.new(token.encode(), f"{job_id}\n{nonce}\n".encode() + canonical(obj), hashlib.sha256).hexdigest()


# --- config ----------------------------------------------------------------------------------------------------------------------


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return default


class Config:
    def __init__(self, *, server: str | None = None, token: str | None = None, model: str | None = None, embed_model: str | None = None,
                 ollama: str | None = None, max_claim: int | None = None, poll_s: float | None = None, heartbeat_s: float | None = None,
                 keep_alive: str | None = None, gpu: str | None = None) -> None:
        self.server = (server or _env("PRICECHAT_SERVER", "PRICECHAT_API_BASE", default=DEFAULT_SERVER)).rstrip("/")
        self.token = token if token is not None else _env("INTEL_WORKER_TOKEN")
        self.model = model or _env("INTEL_MODEL", default=DEFAULT_MODEL)
        self.embed_model = embed_model or _env("INTEL_EMBED_MODEL", default=DEFAULT_EMBED)
        self.ollama = (ollama or _env("INTEL_OLLAMA_URL", "OLLAMA_URL", default=DEFAULT_OLLAMA)).rstrip("/")
        self.max_claim = max(1, min(MAX_CLAIM, int(max_claim if max_claim is not None else _env("INTEL_MAX_CLAIM", default="1"))))
        self.poll_s = float(poll_s if poll_s is not None else _env("INTEL_POLL_S", default="5"))
        self.heartbeat_s = float(heartbeat_s if heartbeat_s is not None else _env("INTEL_HEARTBEAT_S", default="30"))
        self.keep_alive = keep_alive or _env("INTEL_KEEP_ALIVE", default="2m")
        self.gpu = gpu if gpu is not None else _env("INTEL_GPU_NAME")

    def __repr__(self) -> str:  # never the token
        return (f"Config(server={self.server!r}, model={self.model!r}, embed_model={self.embed_model!r}, ollama={self.ollama!r}, "
                f"max_claim={self.max_claim}, token={'set' if self.token else 'MISSING'})")


# --- Railway ---------------------------------------------------------------------------------------------------------------------


class Server:
    """Outbound calls to /api/intel-worker/*. `http` is injectable (tests pass FastAPI's TestClient, which is an httpx.Client)."""

    def __init__(self, cfg: Config, http: httpx.Client | None = None) -> None:
        self.base = cfg.server + PREFIX
        self._token = cfg.token
        self.h = {"Authorization": f"Bearer {cfg.token}", "User-Agent": f"PriceChatWorker-intel/{VERSION}"}
        self.c = http or httpx.Client(timeout=30.0)

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        r = self.c.post(self.base + path, json=body, headers=self.h)
        if r.status_code == 401:
            raise TokenRefused("Railway refused the intel token (INTEL_WORKER_TOKEN_SHA256 not set to this token's hash, or rotated)")
        return r

    def heartbeat(self, models: list[str], info: dict[str, Any]) -> dict[str, Any]:
        r = self._post("/heartbeat", {"models": [m[:80] for m in models[:50]], "info": info})
        r.raise_for_status()
        return r.json()

    def claim(self, n: int) -> list[dict[str, Any]] | None:
        """The claimed jobs ([] when none). None = the heartbeat is stale (409): send one and try again."""
        r = self._post("/claim", {"max": max(1, min(MAX_CLAIM, n))})
        if r.status_code == 204:
            return []
        if r.status_code == 409:
            return None
        r.raise_for_status()
        return list(r.json().get("jobs") or [])

    def result(self, job: dict[str, Any], result: dict[str, Any]) -> httpx.Response:
        body = {"nonce": job["nonce"], "signature": sign(self._token, int(job["id"]), job["nonce"], result), "result": result}
        return self._post(f"/jobs/{int(job['id'])}/result", body)

    def fail(self, job: dict[str, Any], error: str) -> httpx.Response:
        error = (error or "error")[:MAX_ERROR]
        body = {"nonce": job["nonce"], "signature": sign(self._token, int(job["id"]), job["nonce"], {"error": error}), "error": error}
        return self._post(f"/jobs/{int(job['id'])}/fail", body)


# --- Ollama ----------------------------------------------------------------------------------------------------------------------


class Ollama:
    """The local Ollama. `http` is injectable (tests use httpx.MockTransport)."""

    def __init__(self, cfg: Config, http: httpx.Client | None = None) -> None:
        self.base = cfg.ollama
        self.keep_alive = cfg.keep_alive
        self.c = http or httpx.Client(timeout=120.0)

    def models(self) -> list[str]:
        """Installed model names, [] when Ollama is down."""
        try:
            r = self.c.get(f"{self.base}/api/tags", timeout=5.0)
            r.raise_for_status()
            return sorted({str(m.get("name") or m.get("model") or "") for m in (r.json().get("models") or [])} - {""})
        except (httpx.HTTPError, ValueError, AttributeError):
            return []

    def chat(self, model: str, system: str, user: str, schema: dict[str, Any], timeout: float) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "stream": False, "format": schema, "keep_alive": self.keep_alive,
                                "options": {"temperature": 0, "num_ctx": 4096},
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        think = think_for(model)
        if think is not None:
            body["think"] = think
        r = self.c.post(f"{self.base}/api/chat", json=body, timeout=timeout)
        if r.status_code >= 400:
            raise JobError(f"ollama chat HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def embed(self, model: str, text: str, timeout: float) -> list[float]:
        body = {"model": model, "input": text, "truncate": True, "keep_alive": self.keep_alive}
        r = self.c.post(f"{self.base}/api/embed", json=body, timeout=timeout)
        if r.status_code >= 400:
            raise JobError(f"ollama embed HTTP {r.status_code}: {r.text[:200]}")
        vecs = r.json().get("embeddings") or []
        if not vecs or not isinstance(vecs[0], list):
            raise JobError("ollama returned no embedding")
        return vecs[0]


def think_for(model: str) -> Any:
    m = model.lower()
    if m.startswith(("qwen3", "deepseek")):
        return False
    if m.startswith("gpt-oss"):
        return "low"
    return None


def has_model(installed: list[str], name: str) -> bool:
    """Ollama reports "nomic-embed-text:latest" for a model pulled as "nomic-embed-text"."""
    want = name if ":" in name else f"{name}:latest"
    return name in installed or want in installed


# --- the work --------------------------------------------------------------------------------------------------------------------


def matryoshka(vec: list[float], dim: int) -> list[float]:
    """Truncate to dim (layer norm first, as nomic-embed-text v1.5 prescribes) and L2-normalise."""
    v = [float(x) for x in vec]
    if not v or any(not math.isfinite(x) for x in v):
        raise JobError("embedding has non-finite values")
    if dim > len(v):
        raise JobError(f"embedding has {len(v)} dims, job wants {dim}")
    if dim < len(v):
        mean = sum(v) / len(v)
        std = math.sqrt(sum((x - mean) ** 2 for x in v) / len(v) + 1e-5)
        v = [(x - mean) / std for x in v][:dim]
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        raise JobError("embedding is all zeros")
    return [x / norm for x in v]


def lease_left(job: dict[str, Any], now: float | None = None) -> float:
    """Seconds of lease left (120 when the job carries no lease time)."""
    raw = job.get("lease_until")
    if not raw:
        return 120.0
    try:
        t = datetime.fromisoformat(str(raw))
    except ValueError:
        return 120.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp() - (time.time() if now is None else now)


def do_job(job: dict[str, Any], ollama: Ollama, cfg: Config, timeout: float) -> dict[str, Any]:
    """The signed result body {output, model, seconds} for one claimed job, or JobError."""
    t0 = time.perf_counter()
    kind = job.get("kind")
    if kind == "intel.analyze":
        if not isinstance(job.get("schema"), dict) or not job.get("user"):
            raise JobError("job is missing its prompt or schema")
        data = ollama.chat(cfg.model, str(job.get("system") or ""), str(job["user"]), job["schema"], timeout)
        raw = ((data or {}).get("message") or {}).get("content") or ""
        try:
            out = json.loads(raw)
        except ValueError as e:
            raise JobError(f"model reply is not JSON ({e})") from e
        if not isinstance(out, dict):
            raise JobError("model reply is not a JSON object")
        model = cfg.model
    elif kind == "intel.embed":
        text = str(job.get("text") or "")
        if not text.strip():
            raise JobError("embed job has no text")
        purpose = str(job.get("purpose") or "document")
        dim = int(job.get("dim") or 768)
        out = matryoshka(ollama.embed(cfg.embed_model, EMBED_PREFIX.get(purpose, EMBED_PREFIX["document"]) + text, timeout), dim)
        model = cfg.embed_model
    else:
        raise JobError(f"unknown job kind {kind!r}")
    return {"output": out, "model": model, "seconds": round(time.perf_counter() - t0, 3)}


def gpu_name() -> str:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=5,
                             creationflags=flags)
        return (out.stdout or "").strip().splitlines()[0][:80] if out.returncode == 0 and out.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


# --- the worker ------------------------------------------------------------------------------------------------------------------


class Worker:
    def __init__(self, cfg: Config, server_http: httpx.Client | None = None, ollama_http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = cfg
        self.server = Server(cfg, server_http)
        self.ollama = Ollama(cfg, ollama_http)
        self.sleep = sleep
        self.busy = False
        self.models: list[str] = []
        self.done = self.failed = 0
        self.last_beat = float("-inf")
        self.backoff = cfg.poll_s
        self._lock = threading.Lock()

    # heartbeat
    def info(self) -> dict[str, Any]:
        return {"version": VERSION, "busy": self.busy or not self.models, "gpu": self.cfg.gpu}

    def beat(self) -> dict[str, Any]:
        """One heartbeat (refreshes the installed-model list first). Raises httpx.HTTPError / TokenRefused."""
        with self._lock:
            self.models = self.ollama.models()
            got = self.server.heartbeat(self.models, self.info())
            self.last_beat = time.monotonic()
            return got

    def can_claim(self) -> bool:
        return has_model(self.models, self.cfg.model) or has_model(self.models, self.cfg.embed_model)

    # jobs
    def handle(self, job: dict[str, Any]) -> str:
        """Do one claimed job and report it. Returns 'done', 'rejected' (Railway refused the result) or 'failed'."""
        jid = job.get("id")
        left = lease_left(job)
        try:
            if left < LEASE_MARGIN_S:
                raise JobError(f"lease nearly over ({left:.0f}s left) before starting")
            installed = self.models
            want = self.cfg.model if job.get("kind") == "intel.analyze" else self.cfg.embed_model
            if not has_model(installed, want):
                raise JobError(f"model {want} is not installed in Ollama" if installed else "ollama is not reachable")
            result = do_job(job, self.ollama, self.cfg, timeout=max(5.0, left - 5.0))
        except (JobError, httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            msg = str(exc) if isinstance(exc, JobError) else f"{type(exc).__name__}: {exc}"
            log.warning("job %s (%s) failed: %s", jid, job.get("kind"), msg)
            self.failed += 1
            self._report_fail(job, msg)
            return "failed"
        for attempt in range(2):
            try:
                r = self.server.result(job, result)
                break
            except httpx.HTTPError as exc:
                if attempt:
                    log.warning("job %s: result upload failed (%s); the lease expires and Railway answers it", jid, exc)
                    self.failed += 1
                    return "failed"
                self.sleep(2.0)
        if r.status_code == 200:
            self.done += 1
            log.info("job %s (%s, %s): accepted in %.1fs", jid, job.get("kind"), job.get("priority"), result["seconds"])
            return "done"
        self.failed += 1
        log.warning("job %s: result refused, HTTP %s %s", jid, r.status_code, r.text[:200])
        return "rejected"

    def _report_fail(self, job: dict[str, Any], msg: str) -> None:
        try:
            r = self.server.fail(job, msg)
            if r.status_code != 200:
                log.info("job %s: fail report answered HTTP %s (already handled by Railway)", job.get("id"), r.status_code)
        except httpx.HTTPError as exc:
            log.warning("job %s: couldn't report the failure (%s); its lease will expire", job.get("id"), exc)

    def step(self) -> int:
        """One claim round: claim (when idle and a model is installed), do the jobs. Returns the number of jobs handled.
        Raises httpx.HTTPError / TokenRefused for the caller's back-off."""
        if time.monotonic() - self.last_beat > self.cfg.heartbeat_s:
            self.beat()
        if not self.can_claim():
            return 0
        jobs = self.server.claim(self.cfg.max_claim)
        if jobs is None:  # stale heartbeat (the beat thread fell behind): beat now and try once more
            self.beat()
            jobs = self.server.claim(self.cfg.max_claim) or []
        if not jobs:
            return 0
        self.busy = True
        try:
            for job in jobs:
                self.handle(job)
        finally:
            self.busy = False
        return len(jobs)

    def _beat_loop(self) -> None:
        while not STOP:
            try:
                self.beat()
            except TokenRefused:
                pass  # the main loop logs it and backs off
            except httpx.HTTPError as exc:
                log.debug("heartbeat: %s", exc)
            for _ in range(int(max(1.0, self.cfg.heartbeat_s))):
                if STOP:
                    return
                time.sleep(1.0)

    def run(self, once: bool = False) -> int:
        threading.Thread(target=self._beat_loop, name="intel-heartbeat", daemon=True).start()
        refused_logged = False
        while not STOP:
            try:
                n = self.step()
                self.backoff = self.cfg.poll_s
                refused_logged = False
            except TokenRefused as exc:
                if not refused_logged:
                    log.warning("%s; retrying every 5 minutes", exc)
                    refused_logged = True
                self._wait(300.0)
                continue
            except httpx.HTTPError as exc:
                log.warning("railway: %s (retrying in %.0fs)", type(exc).__name__, self.backoff)
                self._wait(self.backoff)
                self.backoff = min(self.backoff * 2, 300.0)
                continue
            if once:
                return 0
            if n == 0:
                self._wait(self.cfg.poll_s)
        return 0

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not STOP and time.monotonic() < end:
            self.sleep(min(1.0, end - time.monotonic()))


def main(argv: list[str] | None = None) -> int:
    load_env(ROOT / "worker.env")
    ap = argparse.ArgumentParser(prog="intel")
    ap.add_argument("--server", default=None, help="default PRICECHAT_SERVER (or PRICECHAT_API_BASE), else " + DEFAULT_SERVER)
    ap.add_argument("--model", default=None, help="analysis model, default INTEL_MODEL or " + DEFAULT_MODEL)
    ap.add_argument("--once", action="store_true", help="one claim round, then exit")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "intel.log", encoding="utf-8")])
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs URLs only, never headers; still, only problems are worth a line
    cfg = Config(server=a.server, model=a.model)
    if not cfg.token:
        print("No intel token (INTEL_WORKER_TOKEN in worker.env; make one with `python -m app.intel.tokens`)", file=sys.stderr)
        return 2
    if not cfg.token.startswith("pci_"):
        print("INTEL_WORKER_TOKEN must be a pci_ token (not a Brand Manager pcw_ token)", file=sys.stderr)
        return 2
    if not cfg.gpu:
        cfg.gpu = gpu_name()

    def _stop(*_: Any) -> None:
        global STOP
        STOP = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    log.info("intel worker %s ready: %r", VERSION, cfg)
    return Worker(cfg).run(once=a.once)


if __name__ == "__main__":
    sys.exit(main())
