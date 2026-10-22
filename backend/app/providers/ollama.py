"""Ollama implementations of LLMProvider and EmbeddingProvider.

All Ollama-specific behaviour (endpoints, payload shape, error strings,
model-name conventions, embedding prefixes) lives in this module.
API reference: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

import logging
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.providers.base import (
    ChatMessage,
    ChatOptions,
    ChatResult,
    EmbeddingProvider,
    EmbeddingPurpose,
    LLMProvider,
    ProviderHealth,
)
from app.providers.errors import (
    ModelNotFoundError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

# Embedding models trained with task prefixes. Matched against the model name without tag.
_EMBED_PREFIXES: dict[str, dict[str, str]] = {
    "nomic-embed-text": {"query": "search_query: ", "document": "search_document: "},
}


def _without_credentials(url: str) -> str:
    """The URL as shown in errors, logs and the dashboard: user:password@ (remote Ollama behind basic auth) removed."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


def _pull_hint(model: str) -> str:
    return f"Run: ollama pull {model}"


class _OllamaHttp:
    """Shared HTTP client with Ollama error mapping."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _without_credentials(base_url.rstrip("/"))
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            transport=transport,
        )

    async def request(
        self, method: str, path: str, *, model: str | None = None, timeout: float | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout, connect=5.0)
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            if isinstance(exc, httpx.ConnectTimeout):
                raise ProviderUnavailableError(f"Cannot reach Ollama at {self.base_url}") from exc
            raise ProviderTimeoutError(
                "Ollama did not answer in time. The model may still be loading; try again."
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Is Ollama running?"
            ) from exc

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = None
            message = str(body.get("error", response.text)) if isinstance(body, dict) else response.text
            logger.warning("Ollama %s %s -> %s: %s", method, path, response.status_code, message)
            if model and response.status_code == 404 and "not found" in message.lower():
                raise ModelNotFoundError(model, _pull_hint(model))
            raise ProviderResponseError(f"Ollama error ({response.status_code}): {message}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Ollama returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError(f"Unexpected Ollama response: {data!r:.200}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()


def _model_present(model: str, installed: list[str]) -> bool:
    # Ollama reports "name:tag"; an untagged name means ":latest".
    wanted = model if ":" in model else f"{model}:latest"
    return wanted in installed or model in installed


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        temperature: float = 0.7,
        timeout_seconds: float = 120.0,
        keep_alive: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(model=model, temperature=temperature)
        self.keep_alive = keep_alive
        self._http = _OllamaHttp(base_url, timeout_seconds, transport)

    @property
    def endpoint(self) -> str:
        return self._http.base_url

    async def chat(
        self, messages: list[ChatMessage], options: ChatOptions | None = None
    ) -> ChatResult:
        options = options or ChatOptions()
        model = options.model or self.model
        model_options: dict[str, Any] = {
            "temperature": options.temperature if options.temperature is not None else self.temperature
        }
        if options.max_tokens is not None:
            model_options["num_predict"] = options.max_tokens

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": model_options,
        }
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        if options.response_schema is not None:
            payload["format"] = options.response_schema

        started = time.perf_counter()
        data = await self._http.request(
            "POST", "/api/chat", model=model, timeout=options.timeout_seconds, json=payload
        )
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderResponseError(f"Unexpected Ollama response: {data!r:.200}") from exc
        if not isinstance(content, str):  # e.g. "content": null
            raise ProviderResponseError(f"Unexpected Ollama response: {data!r:.200}")

        completion_tokens = data.get("eval_count")
        eval_ns = data.get("eval_duration")
        tps = round(completion_tokens / (eval_ns / 1e9), 1) if completion_tokens and eval_ns else None

        return ChatResult(
            content=content.strip(),
            model=data.get("model", model),
            provider=self.name,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=completion_tokens,
            latency_ms=round(latency_ms, 1),
            tokens_per_second=tps,
        )

    async def list_models(self) -> list[str]:
        data = await self._http.request("GET", "/api/tags", timeout=10.0)
        return [m["name"] for m in data.get("models", []) if "name" in m]

    async def health(self, model: str | None = None) -> ProviderHealth:
        model = model or self.model
        started = time.perf_counter()
        try:
            models = await self.list_models()
        except (ProviderUnavailableError, ProviderResponseError) as exc:
            return ProviderHealth(self.name, model, available=False, detail=str(exc), endpoint=self.endpoint)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        loaded = _model_present(model, models)
        return ProviderHealth(
            self.name,
            model,
            available=True,
            model_loaded=loaded,
            detail=None if loaded else f"Model '{model}' is not installed. {_pull_hint(model)}",
            models=sorted(models),
            endpoint=self.endpoint,
            latency_ms=latency_ms,
        )

    async def aclose(self) -> None:
        await self._http.aclose()


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(model=model)
        self._http = _OllamaHttp(base_url, timeout_seconds, transport)
        self._prefixes = _EMBED_PREFIXES.get(model.split(":")[0], {})

    async def embed(self, texts: list[str], purpose: EmbeddingPurpose = "document") -> list[list[float]]:
        if not texts:
            return []
        prefix = self._prefixes.get(purpose, "")
        data = await self._http.request(
            "POST",
            "/api/embed",
            model=self.model,
            json={"model": self.model, "input": [prefix + t for t in texts]},
        )
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ProviderResponseError("Ollama returned an unexpected embedding payload")
        return vectors

    async def aclose(self) -> None:
        await self._http.aclose()
