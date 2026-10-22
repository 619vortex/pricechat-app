import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.ai.gate import AIGate
from app.ai.routing import ModelRouter
from app.connectors.x.connector import XRuntime
from app.security.credentials import x_credentials
from app.config.settings import Settings
from app.connectors.registry import ConnectorRegistry
from app.providers.base import EmbeddingProvider, LLMProvider, ProviderHealth
from app.providers.factory import create_embedding_provider, create_provider
from app.services.provider_monitor import ProviderMonitor
from app.telegram.state import TelegramRuntime

if TYPE_CHECKING:
    from app.scheduler.scheduler import Scheduler
    from app.telegram.account_service import TelegramAccountRuntime
    from app.youtube.service import YouTubeRuntime

# The dashboard polls status; an offline backend can take seconds to refuse a connection.
HEALTH_CACHE_SECONDS = 5.0


def _youtube_runtime() -> "YouTubeRuntime":
    from app.youtube.service import YouTubeRuntime

    return YouTubeRuntime()


def _account_runtime() -> "TelegramAccountRuntime":
    from app.telegram.account_service import TelegramAccountRuntime

    return TelegramAccountRuntime()


@dataclass(slots=True)
class AppRuntime:
    """Process-wide singletons, created once at startup and stored on app.state."""

    settings: Settings
    provider: LLMProvider
    embedder: EmbeddingProvider | None
    connectors: ConnectorRegistry
    telegram: TelegramRuntime = field(default_factory=TelegramRuntime)
    monitor: ProviderMonitor = field(default_factory=ProviderMonitor)
    scheduler: "Scheduler | None" = None
    gate: AIGate = field(default_factory=AIGate)
    x: XRuntime = field(default_factory=XRuntime)
    telegram_account: "TelegramAccountRuntime" = field(default_factory=lambda: _account_runtime())
    youtube: "YouTubeRuntime" = field(default_factory=lambda: _youtube_runtime())
    members_transport: Any = None  # httpx transport for member sign-in calls (tests inject a fake; None = real network)
    pokemon_transport: Any = None  # httpx transport for Pokémon data providers (tests inject a fake)
    bm_transport: Any = None  # httpx transport for TikTok / topic sources in the Brand Manager (tests inject a fake)
    _health: dict[str, tuple[float, ProviderHealth]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        provider: LLMProvider | None = None,
        embedder: EmbeddingProvider | None = None,
        use_embedder: bool = True,
    ) -> "AppRuntime":
        if embedder is None and use_embedder:
            embedder = create_embedding_provider(settings)
        telegram = TelegramRuntime(configured=settings.telegram_configured)
        if telegram.configured:
            telegram.status = "disconnected"
        x = XRuntime(configured=x_credentials(settings).configured)
        if x.configured:
            x.status = "disconnected"
        return cls(
            settings=settings,
            provider=provider or create_provider(settings),
            embedder=embedder,
            connectors=ConnectorRegistry(telegram=telegram, x=x),
            telegram=telegram,
            x=x,
        )

    def router(self, db: Session, background: bool = True) -> ModelRouter:
        """Model router; the 'default' tier follows the agent's chat model.

        background=True: calls queue behind interactive chat (scheduled jobs).
        background=False: for user-initiated actions; wrap them in `gate.interactive()`.
        """
        from app.services.agent_service import get_agent_config

        config = get_agent_config(db)
        return ModelRouter(db, self.settings, self.provider, default_model=config.model_name or self.provider.model,
                           gate=self.gate if background else None)

    async def llm_health(self, model: str | None = None, max_age: float = HEALTH_CACHE_SECONDS) -> ProviderHealth:
        key = model or self.provider.model
        now = time.monotonic()
        cached = self._health.get(key)
        if cached is not None and now - cached[0] < max_age:
            return cached[1]
        health = await self.provider.health(key)
        self._health[key] = (now, health)
        return health

    def invalidate_health(self) -> None:
        self._health.clear()

    async def aclose(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        await self.provider.aclose()
        if self.embedder is not None:
            await self.embedder.aclose()
