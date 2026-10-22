"""TEST FIXTURES: real public responses recorded from each venue on 2026-10-21 (trimmed), served through httpx.MockTransport.
No test touches the network."""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2] / "backend"  # the package lives in backend/fundarb: one copy, deployed with the API
sys.path.insert(0, str(ROOT))

from fundarb.config import Settings  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Any:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("FUNDARB_") or k in ("LIVE_TRADING", "LIVE_CONFIRM_TOKEN"):
            monkeypatch.delenv(k, raising=False)


def settings(**kw: Any) -> Settings:
    return Settings(**kw)


class Router:
    """Routes by (method, path[, a body/param key]) to fixture payloads; records every request."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.calls: list[httpx.Request] = []
        self._handler = handler

    def transport(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            self.calls.append(r)
            return self._handler(r)
        return httpx.MockTransport(handle)


def body(r: httpx.Request) -> dict[str, Any]:
    return json.loads(r.content) if r.content else {}
