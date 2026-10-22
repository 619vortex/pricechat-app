"""Ask a routed model for JSON that matches a Pydantic model, robustly."""

import json
import logging
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.routing import ModelRouter, RouteDecision
from app.providers.base import ChatMessage, ChatResult
from app.providers.errors import ProviderResponseError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class StructuredOutputError(ProviderResponseError):
    code = "structured_output_error"

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw  # the last unparseable model output (for salvaging complete items)


def salvage_items(text: str, key: str, item_cls: type[T]) -> list[T]:
    """Recover the complete objects of a JSON list from output that was cut off or malformed later on.

    E.g. '{"items": [{...}, {...}, {"id": "p9", "rel' → the first two items. Invalid items are skipped.
    """
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*\[', text or "")
    if not m:
        return []
    decoder = json.JSONDecoder()
    pos, out = m.end(), []
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            break
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        try:
            out.append(item_cls.model_validate(obj))
        except ValidationError:
            continue
    return out


def unit_score(value: object) -> float:
    """Coerce a model-supplied score into 0..1. Models sometimes answer on a 0-10 or 0-100 scale,
    and JSON-schema output enforces types but not ranges."""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    if v > 1:
        v = v / 10 if v <= 10 else v / 100
    return max(0.0, min(1.0, v))


def extract_json(text: str) -> str:
    cleaned = _FENCE.sub("", text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return cleaned[start : end + 1]


def parse_model_output(text: str, model_cls: type[T]) -> T:
    return model_cls.model_validate(json.loads(extract_json(text)))


async def complete_structured(
    router: ModelRouter,
    task: str,
    system: str,
    user: str,
    model_cls: type[T],
    retries: int = 1,
) -> tuple[T, ChatResult, RouteDecision]:
    """Call the task's model with a JSON schema; retry once with the validation error on bad output."""
    schema = model_cls.model_json_schema()
    messages = [ChatMessage("system", system), ChatMessage("user", user)]
    last_error: Exception | None = None
    raw = ""
    for attempt in range(retries + 1):
        result, route = await router.complete(task, messages, response_schema=schema)
        raw = result.content or ""
        try:
            return parse_model_output(result.content, model_cls), result, route
        except (ValueError, ValidationError) as exc:
            last_error = exc
            logger.warning("Task %s returned invalid JSON (attempt %d): %s", task, attempt + 1, str(exc)[:200])
            messages = [
                *messages,
                ChatMessage("assistant", result.content[:2000]),
                ChatMessage("user", f"That was not valid. Error: {str(exc)[:300]}. Reply with JSON matching the schema only."),
            ]
    raise StructuredOutputError(f"Model output for '{task}' could not be parsed: {last_error}", raw=raw)
