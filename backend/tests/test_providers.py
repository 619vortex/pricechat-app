import json

import httpx
import pytest

from app.config.settings import Settings
from app.providers import (
    ChatMessage,
    ChatOptions,
    ModelNotFoundError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnknownProviderError,
    create_embedding_provider,
    create_provider,
)
from app.providers.ollama import OllamaEmbeddingProvider, OllamaProvider


def _ollama(handler, **kw) -> OllamaProvider:  # type: ignore[no-untyped-def]
    return OllamaProvider(model="llama3.1:8b", transport=httpx.MockTransport(handler), **kw)


async def test_chat_sends_expected_payload_and_reports_tokens() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "llama3.1:8b",
                "message": {"role": "assistant", "content": " hello \n"},
                "prompt_eval_count": 12,
                "eval_count": 30,
                "eval_duration": 500_000_000,
            },
        )

    provider = _ollama(handler, keep_alive="30m")
    result = await provider.chat(
        [ChatMessage("system", "sys"), ChatMessage("user", "hi")],
        ChatOptions(temperature=0.1, max_tokens=50),
    )
    await provider.aclose()

    assert result.content == "hello"
    assert result.provider == "ollama"
    assert (result.prompt_tokens, result.completion_tokens) == (12, 30)
    assert result.tokens_per_second == 60.0
    assert seen["path"] == "/api/chat"
    assert seen["body"]["stream"] is False
    assert seen["body"]["keep_alive"] == "30m"
    assert seen["body"]["options"] == {"temperature": 0.1, "num_predict": 50}
    assert seen["body"]["messages"][1] == {"role": "user", "content": "hi"}


async def test_per_call_model_override() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"message": {"content": "ok"}})

    result = await _ollama(handler).chat([ChatMessage("user", "hi")], ChatOptions(model="gemma3:27b"))
    assert seen["model"] == "gemma3:27b"
    assert result.model == "gemma3:27b"


async def test_unreachable_raises_unavailable_and_health_is_offline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = _ollama(handler)
    with pytest.raises(ProviderUnavailableError) as exc:
        await provider.chat([ChatMessage("user", "hi")])
    assert exc.value.code == "provider_unavailable"

    health = await provider.health()
    assert health.available is False
    assert health.endpoint == "http://127.0.0.1:11434"
    assert "Cannot reach Ollama" in (health.detail or "")


async def test_read_timeout_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderTimeoutError) as exc:
        await _ollama(handler).chat([ChatMessage("user", "hi")])
    assert exc.value.code == "provider_timeout"
    assert isinstance(exc.value, ProviderUnavailableError)  # timeouts are a kind of unavailability


async def test_missing_model_raises_model_not_found() -> None:
    provider = _ollama(lambda r: httpx.Response(404, json={"error": "model 'nope:1b' not found"}))
    with pytest.raises(ModelNotFoundError) as exc:
        await provider.chat([ChatMessage("user", "hi")], ChatOptions(model="nope:1b"))
    assert exc.value.code == "model_unavailable"
    assert "ollama pull nope:1b" in str(exc.value)


async def test_other_errors_raise_response_error() -> None:
    provider = _ollama(lambda r: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(ProviderResponseError, match="boom"):
        await provider.chat([ChatMessage("user", "hi")])


async def test_health_checks_requested_model() -> None:
    provider = _ollama(lambda r: httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text:latest"}]}))
    assert (await provider.health()).model_loaded is True
    assert (await provider.health("nomic-embed-text")).model_loaded is True  # untagged == :latest
    missing = await provider.health("gemma3:27b")
    assert missing.available is True and missing.model_loaded is False
    assert "ollama pull gemma3:27b" in (missing.detail or "")
    assert missing.latency_ms is not None


async def test_embeddings_apply_model_prefixes() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    embedder = OllamaEmbeddingProvider("nomic-embed-text", transport=httpx.MockTransport(handler))
    vectors = await embedder.embed(["a", "b"], purpose="query")
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["body"]["input"] == ["search_query: a", "search_query: b"]


async def test_embeddings_bad_payload() -> None:
    embedder = OllamaEmbeddingProvider("x", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"embeddings": []})))
    with pytest.raises(ProviderResponseError):
        await embedder.embed(["a"])


def test_factory_builds_configured_provider() -> None:
    provider = create_provider(Settings(llm_provider="Ollama", llm_model="m:1"))
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "m:1"


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(UnknownProviderError):
        create_provider(Settings(llm_provider="vortex"))


def test_embedding_factory_can_be_disabled() -> None:
    assert create_embedding_provider(Settings(embedding_provider="none")) is None
    assert isinstance(create_embedding_provider(Settings()), OllamaEmbeddingProvider)
