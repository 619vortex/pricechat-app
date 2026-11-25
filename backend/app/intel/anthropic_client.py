"""The intel engine's ONE Anthropic Messages client (used by app.intel only; the older call sites are not rewired yet, see ledger TODO).

Raw httpx, like the rest of the backend (the `anthropic` SDK isn't a backend dependency). Every call:
1. resolves the key (Admin store, then ANTHROPIC_API_KEY): none -> CloudUnavailable;
2. reserves its worst-case cost in the shared ledger: over the daily cap -> CloudUnavailable (caller falls back to rules);
3. asks for structured JSON (output_config.format json_schema from app.intel.prompt), no temperature / top_p;
4. checks stop_reason (refusal, max_tokens) BEFORE reading content, then validates against app.intel.schema.ItemAnalysis;
5. settles the ledger with the usage the API reports (billed even when the answer is unusable).
The key and response bodies are never logged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.intel import ledger, prompt
from app.intel.schema import ItemAnalysis

logger = logging.getLogger(__name__)

URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"  # the version every other call site in this backend sends
MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
MAX_TOKENS = 2000  # the analysis is ~200 tokens of JSON; the rest is room for adaptive thinking
TIMEOUT = 45.0
PURPOSE = "intel.analyze"


class CloudUnavailable(Exception):
    """No key, over the cap, an API error, a refusal, a cut-off or an invalid answer: the caller falls back. `reason` is safe to log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CloudResult:
    analysis: ItemAnalysis
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


def _key(settings: Any) -> str | None:
    from app.security.credentials import resolve

    try:
        return resolve("anthropic_api_key", settings)
    except Exception:  # noqa: BLE001 - an unreadable store is "no key"
        return None


def request_body(model: str, title: str, summary: str = "", outlets: list[str] | None = None) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": prompt.SYSTEM,
        "messages": [{"role": "user", "content": prompt.user_message(title, summary, outlets)}],
        # extraction, not reasoning: low effort keeps thinking (billed as output) short
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": prompt.json_schema()}},
    }


def parse(data: dict[str, Any]) -> ItemAnalysis:
    """The validated analysis from a Messages response, or CloudUnavailable. stop_reason first: a refused or cut-off answer is never read."""
    stop = data.get("stop_reason")
    if stop == "refusal":
        raise CloudUnavailable("refusal")
    if stop == "max_tokens":
        raise CloudUnavailable("max_tokens")
    if stop not in ("end_turn", "stop_sequence"):
        raise CloudUnavailable(f"stop_reason {stop}")
    text = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text").strip()
    try:
        return ItemAnalysis.model_validate(json.loads(text))
    except (ValueError, ValidationError) as e:
        raise CloudUnavailable("invalid answer") from e


async def _call(db: Session, body: dict[str, Any], purpose: str, settings: Any, transport: httpx.AsyncBaseTransport | None,
                parse: Any) -> tuple[Any, int, int, float, int]:
    """The shared paid call: reserve in the ledger, POST, settle with the reported usage, parse. (parsed, tokens in, tokens out, cost, ms)."""
    model = body["model"]
    key = _key(settings)
    if not key:
        raise CloudUnavailable("no key")
    est = ledger.estimate(model, ledger.tokens_for(json.dumps(body)), int(body.get("max_tokens") or MAX_TOKENS))
    hold = ledger.reserve(db, model, est, purpose, settings)
    if hold is None:
        raise CloudUnavailable("over cap")
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            r = await client.post(URL, headers={"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
                                  json=body)
    except httpx.HTTPError as e:
        ledger.settle(db, hold, 0, 0, ok=False)
        logger.info("intel: cloud %s", type(e).__name__)
        raise CloudUnavailable(type(e).__name__) from e
    latency = int((time.perf_counter() - t0) * 1000)
    if r.status_code != 200:
        ledger.settle(db, hold, 0, 0, ok=False)  # an error response isn't billed
        logger.info("intel: cloud HTTP %s", r.status_code)  # the body is never logged
        raise CloudUnavailable(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as e:
        ledger.settle(db, hold, 0, 0, ok=False)
        raise CloudUnavailable("unreadable response") from e
    usage = data.get("usage") or {}
    tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    cread = int(usage.get("cache_read_input_tokens") or 0)
    try:
        parsed = parse(data)
    except CloudUnavailable:
        ledger.settle(db, hold, tin, tout, cread, ok=False)  # billed anyway
        raise
    row = ledger.settle(db, hold, tin, tout, cread)
    return parsed, tin, tout, row.cost_usd, latency


def _model(model: str | None, settings: Any) -> str:
    model = (model or settings.intel_cloud_model or MODELS[0]).strip()
    if model not in MODELS:
        raise CloudUnavailable(f"model {model} not allowed")
    return model


async def analyze(db: Session, title: str, summary: str = "", outlets: list[str] | None = None, *, model: str | None = None,
                  settings: Any = None, transport: httpx.AsyncBaseTransport | None = None) -> CloudResult:
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    model = _model(model, settings)
    body = request_body(model, title, summary, outlets)
    analysis, tin, tout, cost, latency = await _call(db, body, PURPOSE, settings, transport, parse)
    return CloudResult(analysis, model, tin, tout, cost, latency)


def parse_json(data: dict[str, Any]) -> dict[str, Any]:
    """A JSON object answer (stop_reason checked first, as parse() does)."""
    stop = data.get("stop_reason")
    if stop in ("refusal", "max_tokens"):
        raise CloudUnavailable(stop)
    if stop not in ("end_turn", "stop_sequence"):
        raise CloudUnavailable(f"stop_reason {stop}")
    text = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text").strip()
    try:
        out = json.loads(text)
    except ValueError as e:
        raise CloudUnavailable("invalid answer") from e
    if not isinstance(out, dict):
        raise CloudUnavailable("invalid answer")
    return out


async def write_json(db: Session, system: str, user: str, schema: dict[str, Any], *, purpose: str, max_tokens: int = 1000,
                     model: str | None = None, settings: Any = None, transport: httpx.AsyncBaseTransport | None = None) -> tuple[dict[str, Any], str, float]:
    """A short structured writing call (Growth Engine "growth.write"), under the same key, allow-list and daily cap. (object, model, cost)."""
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    model = _model(model, settings)
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}}}
    out, _, _, cost, _ = await _call(db, body, purpose, settings, transport, parse_json)
    return out, model, cost
