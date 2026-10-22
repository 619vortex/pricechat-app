"""The only place that maps configuration to concrete providers.

To add a backend (e.g. VortexProvider): implement LLMProvider (and optionally
EmbeddingProvider) in its own module, write builders below, and register them.
Nothing outside app.providers changes.
"""

from collections.abc import Callable

from app.config.settings import Settings
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.errors import UnknownProviderError
from app.providers.ollama import OllamaEmbeddingProvider, OllamaProvider

ProviderBuilder = Callable[[Settings], LLMProvider]
EmbeddingBuilder = Callable[[Settings], EmbeddingProvider]


def _build_ollama(settings: Settings) -> LLMProvider:
    return OllamaProvider(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
        keep_alive=settings.ollama_keep_alive or None,
    )


def _build_ollama_embeddings(settings: Settings) -> EmbeddingProvider:
    return OllamaEmbeddingProvider(model=settings.embedding_model, base_url=settings.ollama_base_url)


_BUILDERS: dict[str, ProviderBuilder] = {
    "ollama": _build_ollama,
}

_EMBEDDING_BUILDERS: dict[str, EmbeddingBuilder] = {
    "ollama": _build_ollama_embeddings,
}


def available_providers() -> list[str]:
    return sorted(_BUILDERS)


def register_provider(name: str, builder: ProviderBuilder) -> None:
    _BUILDERS[name.lower()] = builder


def register_embedding_provider(name: str, builder: EmbeddingBuilder) -> None:
    _EMBEDDING_BUILDERS[name.lower()] = builder


def create_provider(settings: Settings) -> LLMProvider:
    key = settings.llm_provider.lower()
    try:
        builder = _BUILDERS[key]
    except KeyError:
        raise UnknownProviderError(
            f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Available: {available_providers()}"
        ) from None
    return builder(settings)


def create_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    """None disables semantic retrieval; memory falls back to keyword/tag search."""
    key = settings.embedding_provider.lower()
    if key in ("", "none", "off", "disabled"):
        return None
    try:
        builder = _EMBEDDING_BUILDERS[key]
    except KeyError:
        raise UnknownProviderError(
            f"Unknown EMBEDDING_PROVIDER '{settings.embedding_provider}'. "
            f"Available: {sorted(_EMBEDDING_BUILDERS)} or 'none'"
        ) from None
    return builder(settings)
