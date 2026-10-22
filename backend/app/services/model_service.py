"""Model status and the 'Test Model' action. Provider-neutral."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.enums import ActivityLevel
from app.providers.base import ChatMessage, ChatOptions, ChatResult, ProviderHealth
from app.providers.errors import ProviderError, ProviderUnavailableError
from app.services.activity import ActivityLogger
from app.services.agent_service import get_agent_config
from app.services.runtime import AppRuntime

TEST_PROMPT = "Reply with exactly these words and nothing else: model online"


@dataclass(frozen=True, slots=True)
class ModelStatus:
    provider: str
    model: str
    default_model: str
    model_overridden: bool
    temperature: float
    status: str  # online | model_missing | offline
    endpoint: str | None
    detail: str | None
    health_latency_ms: float | None
    installed_models: list[str]
    last_success_at: datetime | None
    last_success_model: str | None
    last_inference_latency_ms: float | None
    last_error: str | None
    embedding_provider: str | None
    embedding_model: str | None


def status_label(health: ProviderHealth) -> str:
    if not health.available:
        return "offline"
    return "online" if health.model_loaded else "model_missing"


async def model_status(db: Session, runtime: AppRuntime) -> ModelStatus:
    config = get_agent_config(db)
    model = config.model_name or runtime.provider.model
    health = await runtime.llm_health(model)
    runtime.monitor.load_last_success(db)
    runtime.monitor.record_health(db, config.id, health)
    last = runtime.monitor.last_success
    return ModelStatus(
        provider=runtime.provider.name,
        model=model,
        default_model=runtime.provider.model,
        model_overridden=bool(config.model_name),
        temperature=config.temperature if config.temperature is not None else runtime.provider.temperature,
        status=status_label(health),
        endpoint=runtime.provider.endpoint,
        detail=health.detail,
        health_latency_ms=health.latency_ms,
        installed_models=health.models,
        last_success_at=last.at if last else None,
        last_success_model=last.model if last else None,
        last_inference_latency_ms=last.latency_ms if last else None,
        last_error=runtime.monitor.last_error,
        embedding_provider=runtime.embedder.name if runtime.embedder else None,
        embedding_model=runtime.embedder.model if runtime.embedder else None,
    )


async def test_model(db: Session, runtime: AppRuntime) -> ChatResult:
    """Send a tiny fixed prompt to the configured model. Raises ProviderError subclasses."""
    config = get_agent_config(db)
    model = config.model_name or runtime.provider.model
    activity = ActivityLogger(db, config.id)
    runtime.monitor.load_last_success(db)
    try:
        result = await runtime.provider.chat(
            [ChatMessage("system", "You are a connectivity test."), ChatMessage("user", TEST_PROMPT)],
            ChatOptions(model=model, temperature=0.0, max_tokens=24),
        )
    except ProviderError as exc:
        runtime.monitor.record_failure(db, config.id, str(exc), isinstance(exc, ProviderUnavailableError))
        runtime.invalidate_health()
        activity.log(
            "model_test",
            f"Model test failed for {model}: {exc}",
            level=ActivityLevel.ERROR,
            actor="dashboard",
            details={"model": model, "error_type": exc.code},
        )
        raise

    runtime.monitor.record_success(db, config.id, result)
    runtime.invalidate_health()
    activity.log(
        "model_test",
        f"Model test passed: {result.model} answered in {(result.latency_ms or 0) / 1000:.1f}s",
        actor="dashboard",
        details={
            "model": result.model,
            "latency_ms": result.latency_ms,
            "completion_tokens": result.completion_tokens,
            "tokens_per_second": result.tokens_per_second,
        },
    )
    return result
