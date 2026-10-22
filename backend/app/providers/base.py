"""Model-layer contracts. Nothing outside app.providers may import a concrete provider."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]
EmbeddingPurpose = Literal["query", "document"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ChatOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    model: str | None = None  # per-call override of the provider's default model
    timeout_seconds: float | None = None
    # JSON Schema the reply must follow. Providers map this to their structured-output feature.
    response_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    model: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float | None = None
    tokens_per_second: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    model: str
    available: bool
    model_loaded: bool = False
    detail: str | None = None
    models: list[str] = field(default_factory=list)
    endpoint: str | None = None
    latency_ms: float | None = None


class LLMProvider(ABC):
    """Chat-capable model backend (Ollama today; Vortex/DGX Spark/OpenAI/Anthropic later)."""

    name: str

    def __init__(self, model: str, temperature: float = 0.7) -> None:
        self.model = model
        self.temperature = temperature

    @property
    def endpoint(self) -> str | None:
        """Human-readable location of the backend. Must never contain credentials."""
        return None

    @abstractmethod
    async def chat(
        self, messages: list[ChatMessage], options: ChatOptions | None = None
    ) -> ChatResult:
        """Return a single non-streaming completion. Raises ProviderError subclasses."""

    @abstractmethod
    async def health(self, model: str | None = None) -> ProviderHealth:
        """Report reachability and whether `model` (default: self.model) is available. Never raises."""

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Models the backend can serve."""

    async def aclose(self) -> None:
        """Release network resources. Override if the provider holds any."""


class EmbeddingProvider(ABC):
    """Turns text into vectors for semantic memory retrieval."""

    name: str

    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    async def embed(self, texts: list[str], purpose: EmbeddingPurpose = "document") -> list[list[float]]:
        """One vector per input text. `purpose` lets models that use query/document prefixes apply them."""

    async def aclose(self) -> None:
        """Release network resources. Override if the provider holds any."""
