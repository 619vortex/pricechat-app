"""The committee's language-model backends, budgets and per-run accounting (contract: Committee run).

COMMITTEE_BACKEND = auto | vortex | router | anthropic | openai | rules
  vortex     generic LLM jobs over the Vortex worker channel (remote.py); $0
  router     the existing AIRouter (app/ai/routing, tasks committee.*) + complete_structured; $0 (local models)
  anthropic  POST https://api.anthropic.com/v1/messages (x-api-key, anthropic-version 2023-06-01)
  openai     POST https://api.openai.com/v1/chat/completions
  rules      no model at all: every role has a deterministic writer (roles.py), also each role's fallback
  auto       vortex if it long-polled in the last 120 s → router if its provider answers a health check → rules

Keys only from env (ANTHROPIC_API_KEY / OPENAI_API_KEY) or the encrypted secret store, never logged; model names only from
COMMITTEE_MODEL_FAST / _DEEP / _SYNTH. A paid backend without its key or models is not selectable (fail fast at selection,
never at import). A ChatGPT subscription is not an API key and is not supported (NOTICE.md).

Budgets, persisted in cm_usage (so a restart never resets them): COMMITTEE_DAILY_CALLS calls a day on any model backend,
COMMITTEE_DAILY_USD for paid backends; over budget → rules. Per run: at most MAX_CALLS_PER_RUN calls, each at most
MAX_OUTPUT_TOKENS output tokens, at most PARALLEL at once. A provider error or timeout costs that ONE role its model text
(rules instead, recorded in degraded); the run continues.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.committee import CmUsage

logger = logging.getLogger(__name__)

BACKENDS = ("auto", "vortex", "router", "anthropic", "openai", "rules")
MAX_CALLS_PER_RUN = 12
MAX_OUTPUT_TOKENS = 900
PARALLEL = 4
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TASK_TIER = {"committee.analyst": "fast", "committee.debate": "deep", "committee.trader": "fast", "committee.risk": "fast",
             "committee.synthesis": "synthesis"}
# USD per 1M tokens (input, output). Override or extend with COMMITTEE_COSTS='{"model": [in, out]}'. A paid model missing
# here is charged at UNKNOWN_PAID (deliberately high, so an unknown price can only make the budget stricter).
COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-5": (3.0, 15.0), "claude-opus-4-1": (15.0, 75.0),
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.0), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.0, 8.0),
}
UNKNOWN_PAID = (15.0, 75.0)


class LLMError(Exception):
    """A provider error, timeout or unusable answer: that role falls back to rules."""


@dataclass
class Call:
    role: str
    task: str  # app/ai/routing TASKS key (committee.*)
    system: str
    prompt: str
    schema: type[BaseModel]
    max_tokens: int = MAX_OUTPUT_TOKENS
    temperature: float = 0.2

    @property
    def tier(self) -> str:
        return TASK_TIER.get(self.task, "fast")


@dataclass
class Reply:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


def settings() -> Any:
    from app.config.settings import get_settings

    return get_settings()


def cost_table(s: Any = None) -> dict[str, tuple[float, float]]:
    s = s or settings()
    table = dict(COSTS)
    raw = (getattr(s, "committee_costs", "") or "").strip()
    if raw:
        try:
            for k, v in json.loads(raw).items():
                table[str(k)] = (float(v[0]), float(v[1]))
        except (ValueError, TypeError, IndexError, AttributeError):
            logger.warning("COMMITTEE_COSTS is not valid JSON {model: [in, out]}; using the built-in table")
    return table


def cost_of(backend: str, model: str, tokens_in: int, tokens_out: int, s: Any = None) -> float:
    if backend not in ("anthropic", "openai"):
        return 0.0  # local models (Vortex, the router's Ollama) cost nothing per call
    pin, pout = cost_table(s).get(model, UNKNOWN_PAID)
    return round(tokens_in * pin / 1e6 + tokens_out * pout / 1e6, 6)


def _schema_note(call: Call) -> str:
    return "\n\nJSON schema of the reply:\n" + json.dumps(call.schema.model_json_schema(), separators=(",", ":"))


# ---------------------------------------------------------------- backends
class Backend:
    name = "rules"
    paid = False

    def models(self) -> dict[str, str]:
        return {"fast": "rules", "deep": "rules", "synthesis": "rules"}

    async def complete(self, call: Call, run_id: int | None = None) -> Reply:  # pragma: no cover - rules never calls
        raise LLMError("the rules backend has no model")


class RouterBackend(Backend):
    """The existing AIRouter (Ollama today): tasks committee.* resolve to tiers / models from the dashboard."""

    name = "router"

    def __init__(self, db: Session, provider: Any, gate: Any = None, s: Any = None) -> None:
        from app.ai.routing import ModelRouter

        self.router = ModelRouter(db, s or settings(), provider, gate=gate)

    def models(self) -> dict[str, str]:
        r = self.router.resolve
        return {"fast": r("committee.analyst").model, "deep": r("committee.debate").model, "synthesis": r("committee.synthesis").model}

    async def complete(self, call: Call, run_id: int | None = None) -> Reply:
        from app.ai.structured import complete_structured

        try:
            obj, res, route = await complete_structured(self.router, call.task, call.system, call.prompt, call.schema, retries=0)
        except Exception as e:  # noqa: BLE001 - provider down, timeout, bad JSON: rules for this role
            raise LLMError(f"router: {type(e).__name__}") from e
        return Reply(obj.model_dump_json(), route.model, int(res.prompt_tokens or 0), int(res.completion_tokens or 0))


class VortexBackend(Backend):
    name = "vortex"

    def __init__(self, timeout: float = 90.0, poll: float = 0.5) -> None:
        self.timeout, self.poll = timeout, poll

    def models(self) -> dict[str, str]:
        return {"fast": "vortex:fast", "deep": "vortex:deep", "synthesis": "vortex:synthesis"}

    async def complete(self, call: Call, run_id: int | None = None) -> Reply:
        from app.committee import remote
        from app.db import session as dbs

        db = dbs.SessionLocal()
        try:
            job = remote.create(db, role=call.role, tier=call.tier, system=call.system, prompt=call.prompt,
                                schema=call.schema.model_json_schema(), max_tokens=call.max_tokens,
                                temperature=call.temperature, run_id=run_id)
            jid = job.public_id
        finally:
            db.close()
        try:
            a = await remote.wait(jid, self.timeout, self.poll)
        except remote.RemoteError as e:
            raise LLMError(str(e)) from e
        return Reply(a["text"], a["model"], int(a["tokens_in"]), int(a["tokens_out"]))


class _HttpBackend(Backend):
    paid = True
    url = ""

    def __init__(self, key: str, model_names: dict[str, str], timeout: float = 90.0, http: httpx.AsyncClient | None = None) -> None:
        self._key, self._models, self.timeout, self.http = key, model_names, timeout, http

    def models(self) -> dict[str, str]:
        return dict(self._models)

    async def _post(self, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        client = self.http or httpx.AsyncClient(timeout=self.timeout)
        try:
            r = await asyncio.wait_for(client.post(self.url, headers=headers, json=body), self.timeout)
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            raise LLMError(f"{self.name}: {type(e).__name__}") from e
        finally:
            if self.http is None:
                await client.aclose()
        if r.status_code != 200:
            raise LLMError(f"{self.name}: HTTP {r.status_code}")  # the body is never logged (it may echo the prompt)
        try:
            return r.json()
        except ValueError as e:
            raise LLMError(f"{self.name}: not JSON") from e


class AnthropicBackend(_HttpBackend):
    name = "anthropic"
    url = ANTHROPIC_URL

    async def complete(self, call: Call, run_id: int | None = None) -> Reply:
        model = self._models[call.tier]
        data = await self._post({"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                                {"model": model, "max_tokens": min(call.max_tokens, MAX_OUTPUT_TOKENS), "system": call.system,
                                 "temperature": call.temperature,
                                 "messages": [{"role": "user", "content": call.prompt + _schema_note(call)}]})
        text = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")
        u = data.get("usage") or {}
        return Reply(text, str(data.get("model") or model), int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0))


class OpenAIBackend(_HttpBackend):
    name = "openai"
    url = OPENAI_URL

    async def complete(self, call: Call, run_id: int | None = None) -> Reply:
        model = self._models[call.tier]
        data = await self._post({"Authorization": f"Bearer {self._key}", "content-type": "application/json"},
                                {"model": model, "max_completion_tokens": min(call.max_tokens, MAX_OUTPUT_TOKENS),
                                 "response_format": {"type": "json_object"},
                                 "messages": [{"role": "system", "content": call.system},
                                              {"role": "user", "content": call.prompt + _schema_note(call)}]})
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("openai: no message in the answer") from e
        u = data.get("usage") or {}
        return Reply(text, str(data.get("model") or model), int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))


# ---------------------------------------------------------------- budgets (cm_usage "global" row per UTC day)
def day_of(now: datetime | None = None) -> str:
    return (now or utcnow()).strftime("%Y-%m-%d")


def usage_row(db: Session, key: str, now: datetime | None = None, tg_user_id: int | None = None, member_id: int | None = None) -> CmUsage:
    day = day_of(now)
    row = db.scalar(select(CmUsage).where(CmUsage.day == day, CmUsage.key == key))
    if row is None:
        row = CmUsage(day=day, key=key, tg_user_id=tg_user_id, member_id=member_id, count=0, calls=0, tokens_in=0, tokens_out=0, cost_usd=0.0)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:  # another run created it first
            db.rollback()
            row = db.scalar(select(CmUsage).where(CmUsage.day == day, CmUsage.key == key))
            assert row is not None
    return row


def budget(db: Session, s: Any = None, now: datetime | None = None) -> dict[str, Any]:
    s = s or settings()
    row = db.scalar(select(CmUsage).where(CmUsage.day == day_of(now), CmUsage.key == "global").execution_options(populate_existing=True))
    return {"calls_today": row.calls if row else 0, "calls_limit": s.committee_daily_calls,
            "usd_today": round(row.cost_usd, 4) if row else 0.0, "usd_limit": s.committee_daily_usd,
            "tokens_in_today": row.tokens_in if row else 0, "tokens_out_today": row.tokens_out if row else 0}


def over_budget(db: Session, paid: bool, s: Any = None) -> str | None:
    b = budget(db, s)
    if b["calls_today"] >= b["calls_limit"]:
        return f"daily LLM call budget reached ({b['calls_today']}/{b['calls_limit']})"
    if paid and b["usd_today"] >= b["usd_limit"]:
        return f"daily USD budget reached (${b['usd_today']:.2f}/${b['usd_limit']:.2f})"
    return None


def record(db: Session, tokens_in: int, tokens_out: int, cost: float) -> None:
    """One LLM call, added atomically to today's global row (concurrent runs never lose an update)."""
    row = usage_row(db, "global")
    db.execute(update(CmUsage).where(CmUsage.id == row.id).values(calls=CmUsage.calls + 1, tokens_in=CmUsage.tokens_in + tokens_in,
                                                                  tokens_out=CmUsage.tokens_out + tokens_out,
                                                                  cost_usd=CmUsage.cost_usd + cost)
               .execution_options(synchronize_session=False))
    db.commit()


# ---------------------------------------------------------------- selection
def _key(name: str) -> str | None:
    from app.security.credentials import resolve

    try:
        return resolve(name)
    except Exception:  # noqa: BLE001 - an unreadable store is "no key"
        return None


def _paid_models(s: Any) -> dict[str, str] | None:
    m = {"fast": s.committee_model_fast.strip(), "deep": s.committee_model_deep.strip(), "synthesis": s.committee_model_synth.strip()}
    return m if all(m.values()) else None


async def backends_status(db: Session, provider: Any = None, s: Any = None) -> dict[str, dict[str, Any]]:
    """Which backends could serve right now, and why not (never shows a key)."""
    from app.tradingos import assist

    s = s or settings()
    out: dict[str, dict[str, Any]] = {"rules": {"ok": True, "detail": "deterministic writers (always available)"}}
    seen = assist.online(db)
    out["vortex"] = {"ok": seen, "detail": "long-polled in the last 120 s" if seen else "not seen in the last 120 s"}
    if provider is None:
        out["router"] = {"ok": False, "detail": "no model provider in this process"}
    else:
        try:
            h = await asyncio.wait_for(provider.health(), 5)
            out["router"] = {"ok": bool(h.available), "detail": f"{h.provider} {'answers' if h.available else 'unavailable'}"}
        except Exception as e:  # noqa: BLE001
            out["router"] = {"ok": False, "detail": f"health check failed ({type(e).__name__})"}
    models = _paid_models(s)
    for name, secret in (("anthropic", "anthropic_api_key"), ("openai", "openai_api_key")):
        if not _key(secret):
            out[name] = {"ok": False, "detail": f"{secret.upper()} not set"}
        elif not models:
            out[name] = {"ok": False, "detail": "COMMITTEE_MODEL_FAST / _DEEP / _SYNTH not set"}
        else:
            out[name] = {"ok": True, "detail": "key and models configured"}
    return out


async def choose(db: Session, provider: Any = None, gate: Any = None, http: httpx.AsyncClient | None = None,
                 s: Any = None) -> tuple[Backend, str | None]:
    """(backend, why it is rules instead of the configured one | None)."""
    s = s or settings()
    want = (s.committee_backend or "auto").strip().lower()
    if want not in BACKENDS:
        return Backend(), f"unknown COMMITTEE_BACKEND {want!r}"
    if want == "rules":
        return Backend(), None
    st = await backends_status(db, provider, s)
    order = ["vortex", "router"] if want == "auto" else [want]
    for name in order:
        if not st[name]["ok"]:
            continue
        paid = name in ("anthropic", "openai")
        why = over_budget(db, paid, s)
        if why:
            return Backend(), why
        timeout = float(s.committee_call_timeout_seconds)
        if name == "vortex":
            return VortexBackend(timeout=timeout), None
        if name == "router":
            return RouterBackend(db, provider, gate, s), None
        models = _paid_models(s) or {}
        key = _key(f"{name}_api_key") or ""
        cls = AnthropicBackend if name == "anthropic" else OpenAIBackend
        return cls(key, models, timeout=timeout, http=http), None
    return Backend(), ("no model backend available" if want == "auto" else f"{want}: {st[want]['detail']}")


# ---------------------------------------------------------------- one run's ledger
@dataclass
class Ledger:
    """Counts one run's calls, tokens and cost, enforces the per-run cap and the daily budgets before every call."""

    db: Session
    backend: Backend
    run_id: int | None = None
    max_calls: int = MAX_CALLS_PER_RUN
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    models_used: dict[str, str] = field(default_factory=dict)
    _sem: asyncio.Semaphore | None = None

    @property
    def live(self) -> bool:
        return self.backend.name != "rules"

    async def ask(self, call: Call) -> tuple[BaseModel | None, str | None, str | None]:
        """(parsed output, author "llm:<model>", None) or (None, None, degraded note)."""
        from app.ai.structured import parse_model_output

        if not self.live:
            return None, None, None
        if self.calls >= self.max_calls:
            return None, None, f"{call.role}: per-run call cap ({self.max_calls}) reached"
        why = over_budget(self.db, self.backend.paid)
        if why:
            return None, None, f"{call.role}: {why}"
        if self._sem is None:
            self._sem = asyncio.Semaphore(PARALLEL)
        self.calls += 1  # reserved before awaiting, so parallel roles can't overshoot the cap
        call.max_tokens = min(call.max_tokens, MAX_OUTPUT_TOKENS)
        started = time.perf_counter()
        async with self._sem:
            try:
                reply = await self.backend.complete(call, self.run_id)
            except LLMError as e:
                self._account(Reply("", "", 0, 0))
                return None, None, f"{call.role}: {e} after {time.perf_counter() - started:.0f} s"
            except Exception as e:  # noqa: BLE001 - never let one role's backend crash the run
                logger.warning("committee %s: backend error %s", call.role, type(e).__name__)
                self._account(Reply("", "", 0, 0))
                return None, None, f"{call.role}: backend error ({type(e).__name__})"
        self._account(reply)
        self.models_used[call.tier] = reply.model
        try:
            return parse_model_output(reply.text, call.schema), f"llm:{reply.model}", None
        except (ValueError, ValidationError):
            return None, None, f"{call.role}: unreadable model answer"

    def _account(self, r: Reply) -> None:
        cost = cost_of(self.backend.name, r.model, r.tokens_in, r.tokens_out) if r.model else 0.0
        self.tokens_in += r.tokens_in
        self.tokens_out += r.tokens_out
        self.cost_usd = round(self.cost_usd + cost, 6)
        record(self.db, r.tokens_in, r.tokens_out, cost)
