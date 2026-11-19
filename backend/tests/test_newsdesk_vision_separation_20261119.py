"""The image check is separate from the caption writers (owner, 2026-11-19): "Create independent settings for image verification so
enabling vision NEVER changes how news captions are written ... Do not activate paid AI calls without my approval."

- editor.vision reads ONLY newsdesk_vision_api_key, and only with NEWSDESK_VISION_ENABLED; the writers read ONLY anthropic_api_key.
- Off by default: no HTTP call at all, not even the post-deploy self-test."""
from typing import Any

import httpx
import pytest
from PIL import Image

from app.newsdesk import editor
from app.security import credentials


class S:
    newsdesk_model = "claude-haiku-5-5"
    newsdesk_vision_model = "claude-haiku-5-5"
    newsdesk_vision_enabled = False


def keys(**k: str) -> Any:
    return lambda name, settings=None: k.get(name)


def never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no paid call may be made: {request.url}")


def pic() -> Image.Image:
    return Image.new("RGB", (800, 600), (200, 150, 40))


def test_the_default_is_off() -> None:
    from app.config.settings import Settings

    assert Settings.model_fields["newsdesk_vision_enabled"].default is False


async def test_the_writers_key_never_turns_the_image_check_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "resolve", keys(anthropic_api_key="writer-key"))
    s = S()
    assert await editor.vision(pic(), "x", "y", settings=s, transport=httpx.MockTransport(never)) is None
    s.newsdesk_vision_enabled = True  # switched on, but only the writers' key exists: still no call
    assert await editor.vision(pic(), "x", "y", settings=s, transport=httpx.MockTransport(never)) is None
    assert editor.vision_config(s)[2].startswith("the image check is on but has no newsdesk_vision_api_key")


async def test_without_the_switch_even_its_own_key_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "resolve", keys(newsdesk_vision_api_key="vision-key"))
    assert await editor.vision(pic(), "x", "y", settings=S(), transport=httpx.MockTransport(never)) is None
    out = await editor.self_test(S(), transport=httpx.MockTransport(never))  # the post-deploy self-test too
    assert out["state"] == "unavailable" and "NEWSDESK_VISION_ENABLED" in out["why"]


async def test_switched_on_with_its_own_key_it_uses_that_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "resolve", keys(anthropic_api_key="writer-key", newsdesk_vision_api_key="vision-key"))
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["key"], seen["model"] = request.headers["x-api-key"], json.loads(request.content)["model"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"shows_subject": true, "recognisable": true, "what": "gold"}'}]})

    s = S()
    s.newsdesk_vision_enabled, s.newsdesk_vision_model, s.newsdesk_model = True, "claude-vision-test", "claude-writer-test"
    v = await editor.vision(pic(), "Gold falls", "gold", settings=s, transport=httpx.MockTransport(handler))
    assert v is not None and seen == {"key": "vision-key", "model": "claude-vision-test"}  # never the writers' key or model


async def test_turning_the_image_check_on_never_changes_the_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.newsdesk import writer

    monkeypatch.setattr(credentials, "resolve", keys(newsdesk_vision_api_key="vision-key"))  # only the image check's key
    s = S()
    s.newsdesk_vision_enabled = True
    # the rules-based writer stays: the model writer needs anthropic_api_key, which the image check never supplies
    assert await writer.rewrite(s, "Gold falls 3%", "", "Reuters", transport=httpx.MockTransport(never)) is None
