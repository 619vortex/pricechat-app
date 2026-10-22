import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

# Must run before any app module reads settings / builds the engine.
_TEST_DB = Path(tempfile.mkdtemp(prefix="pricechat-tests-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["LLM_MODEL"] = "llama3.1:8b"
os.environ["EMBEDDING_PROVIDER"] = "ollama"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["TELEGRAM_BOT_TOKEN"] = ""  # never use a real token in tests
os.environ["TELEGRAM_LISTEN_SECONDS"] = "0"  # tests run one batch per pass (the listening loop has its own test)
os.environ["MODEL_TIER_FAST"] = "fast-model"
os.environ["MODEL_TIER_STRONG"] = "strong-model"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SCHEDULER_DISABLED_JOBS"] = ""  # this machine's .env may switch jobs off (production runs them): tests see every job
os.environ["SCHEDULER_ONLY_JOBS"] = ""
os.environ["GOOGLE_MEMBER_CLIENT_ID"] = ""  # tests configure the sign-in client themselves
os.environ["GOOGLE_MEMBER_CLIENT_SECRET"] = ""
os.environ["RESEND_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["PORTFOLIO_BUCKET"] = ""
os.environ["PORTFOLIO_UPLOAD_DIR"] = tempfile.mkdtemp(prefix="pricechat-uploads-")
os.environ["TIMEZONE"] = "UTC"
os.environ["SECRETS_FILE"] = str(Path(tempfile.mkdtemp(prefix="pricechat-secrets-")) / "secrets.dpapi")
from app.providers.pokemon.pricecharting import PriceChartingProvider  # noqa: E402

PriceChartingProvider.min_interval = 0.0  # no real API in tests: no pacing
os.environ["SCRYDEX_CACHE_DIR"] = tempfile.mkdtemp(prefix="pricechat-scrydex-")
os.environ["TCGDEX_CACHE_DIR"] = tempfile.mkdtemp(prefix="pricechat-tcgdex-")  # tests never touch the real cache
for _k in ("X_BEARER_TOKEN", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "PSA_API_KEY", "PSA_API_TOKEN",
           "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "PRICECHARTING_API_KEY", "POKEMONTCG_API_KEY", "GEMRATE_API_KEY",
           "SCRYDEX_API_KEY", "SCRYDEX_TEAM_ID", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"):  # never real keys in tests
    os.environ[_k] = ""

import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.seed import seed_defaults  # noqa: E402
from app.db.session import SessionLocal, engine, init_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.agent import AgentConfig  # noqa: E402
from app.providers.base import (  # noqa: E402
    ChatMessage,
    ChatOptions,
    ChatResult,
    EmbeddingProvider,
    EmbeddingPurpose,
    LLMProvider,
    ProviderHealth,
)
from app.providers.errors import ModelNotFoundError, ProviderError, ProviderUnavailableError  # noqa: E402
from tests.fakes import structured_reply  # noqa: E402


class FakeProvider(LLMProvider):
    """Deterministic chat provider. Records calls; can be told to raise."""

    name = "fake"

    def __init__(self, model: str = "fake-model") -> None:
        super().__init__(model=model)
        self.error: ProviderError | None = None
        self.installed: list[str] = [model]
        self.missing: set[str] = set()  # models that raise ModelNotFoundError
        self.calls: list[tuple[list[ChatMessage], ChatOptions | None]] = []
        # Optional (schema_title, json_text) -> text hook, e.g. to simulate cut-off or malformed model output.
        self.mangle: Callable[[str, str], str] | None = None

    def models_used(self) -> list[str | None]:
        return [o.model if o else None for _, o in self.calls]

    @property
    def fail(self) -> bool:
        return self.error is not None

    @fail.setter
    def fail(self, value: bool) -> None:
        self.error = ProviderUnavailableError("fake provider offline") if value else None

    @property
    def endpoint(self) -> str:
        return "fake://local"

    async def chat(self, messages: list[ChatMessage], options: ChatOptions | None = None) -> ChatResult:
        self.calls.append((messages, options))
        if self.error is not None:
            raise self.error
        if options and options.model in self.missing:
            raise ModelNotFoundError(options.model)
        if options and options.response_schema is not None:
            user = "\n".join(m.content for m in messages if m.role == "user")
            title = options.response_schema.get("title", "")
            content = json.dumps(structured_reply(title, user))
            if self.mangle is not None:
                content = self.mangle(title, content)
        else:
            content = f"echo: {messages[-1].content}"
        return ChatResult(
            content=content,
            model=(options.model if options and options.model else self.model),
            provider=self.name,
            prompt_tokens=42,
            completion_tokens=7,
            latency_ms=12.5,
            tokens_per_second=100.0,
        )

    async def health(self, model: str | None = None) -> ProviderHealth:
        model = model or self.model
        if self.error is not None:
            return ProviderHealth(self.name, model, available=False, detail=str(self.error), endpoint=self.endpoint)
        return ProviderHealth(
            self.name, model, available=True, model_loaded=model in self.installed,
            models=list(self.installed), endpoint=self.endpoint, latency_ms=1.0,
        )

    async def list_models(self) -> list[str]:
        return list(self.installed)


_WORDS = re.compile(r"[a-z]+")


class FakeEmbedder(EmbeddingProvider):
    """Bag-of-words hashing embedder, with optional synonym groups so 'meaning' can differ from keywords."""

    name = "fake"

    def __init__(self, synonyms: dict[str, str] | None = None, dims: int = 64) -> None:
        super().__init__(model="fake-embed")
        self.synonyms = synonyms or {}
        self.dims = dims
        self.error: ProviderError | None = None
        self.calls = 0

    async def embed(self, texts: list[str], purpose: EmbeddingPurpose = "document") -> list[list[float]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        vectors = []
        for t in texts:
            v = [0.0] * self.dims
            for w in _WORDS.findall(t.lower()):
                w = self.synonyms.get(w, w)
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dims] += 1.0
            vectors.append(v)
        return vectors


def reset_database() -> None:
    # Drop every table directly (FKs off): metadata.drop_all can't order self-referencing tables.
    engine.dispose()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        names = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
        conn.commit()
    engine.dispose()
    init_db()


@pytest.fixture(autouse=True)
def _fresh_database() -> Iterator[None]:
    from app.services import usage

    usage.reset_for_tests()
    from app.members import xp as member_xp

    member_xp.reset_for_tests()
    from app.api.routes import health
    from app.security import rate_limit

    rate_limit.reset_for_tests()
    health._cache["body"] = None
    from app.pokemon import history

    history.invalidate()  # computed market rows never outlive the database they were built from
    reset_database()
    Path(os.environ["SECRETS_FILE"]).unlink(missing_ok=True)  # no saved secrets leak between tests
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def agent_config(db: Session) -> AgentConfig:
    return seed_defaults(db)


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def client(fake_provider: FakeProvider, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(provider=fake_provider, embedder=fake_embedder, background_jobs=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_telegram() -> "FakeTelegram":
    from tests.fakes import FakeTelegram

    tg = FakeTelegram()
    tg.add_chat(-100111, "Price Chat Community")
    tg.add_chat(-100222, "Random Other Group")
    return tg


@pytest.fixture
def tg_client(fake_provider: FakeProvider, fake_telegram: "FakeTelegram") -> Iterator[TestClient]:
    """App configured with a (fake) bot token, talking to the in-memory Telegram."""
    from app.config.settings import Settings
    from tests.fakes import TOKEN

    community_embedder = FakeEmbedder(dims=512)
    settings = Settings(telegram_bot_token=TOKEN, telegram_api_base="https://api.telegram.test", telegram_long_poll_seconds=0)
    app = create_app(app_settings=settings, provider=fake_provider, embedder=community_embedder, background_jobs=False)
    with TestClient(app) as test_client:
        app.state.runtime.telegram.transport = fake_telegram.transport
        yield test_client


@pytest.fixture
def fake_x() -> "FakeX":
    from tests.fakes import FakeX

    return FakeX()


@pytest.fixture
def fake_youtube() -> "FakeYouTube":
    from tests.fakes import FakeYouTube

    return FakeYouTube()


@pytest.fixture
def yt_client(fake_provider: FakeProvider, fake_youtube: "FakeYouTube") -> Iterator[TestClient]:
    """App whose YouTube and Google OAuth calls go to the in-memory FakeYouTube (no quota is used)."""
    from app.config.settings import Settings

    settings = Settings(youtube_api_base="https://yt.test/youtube/v3", google_token_url="https://oauth.test/token",
                        google_auth_url="https://accounts.test/o/oauth2/v2/auth")
    app = create_app(app_settings=settings, provider=fake_provider, embedder=FakeEmbedder(dims=512), background_jobs=False)
    with TestClient(app) as test_client:
        app.state.runtime.youtube.transport = fake_youtube.transport
        yield test_client


@pytest.fixture
def x_client(fake_provider: FakeProvider, fake_x: "FakeX", fake_telegram: "FakeTelegram") -> Iterator[TestClient]:
    """App with Telegram (fake) and an X API base pointing at the in-memory FakeX. Credentials are saved via the API."""
    from app.config.settings import Settings
    from tests.fakes import TOKEN

    settings = Settings(telegram_bot_token=TOKEN, telegram_api_base="https://api.telegram.test", telegram_long_poll_seconds=0,
                        x_api_base="https://api.x.test", x_daily_budget_usd=1.0, x_monthly_budget_usd=20.0)
    app = create_app(app_settings=settings, provider=fake_provider, embedder=FakeEmbedder(dims=512), background_jobs=False)
    with TestClient(app) as test_client:
        app.state.runtime.telegram.transport = fake_telegram.transport
        app.state.runtime.x.transport = fake_x.transport
        yield test_client


def connect_x_user(db: Session) -> None:
    """Simulate a tested X connection with user-context (write-capable) credentials. No network."""
    from app.config.settings import get_settings
    from app.connectors.state import get_connector_state
    from app.models.enums import Platform
    from app.security.credentials import secret_store
    from tests.fakes import X_BEARER, X_USER_KEYS

    store = secret_store(get_settings())
    store.set("x_bearer_token", X_BEARER)
    for k, v in X_USER_KEYS.items():
        store.set(f"x_{k}", v)
    state = get_connector_state(db, Platform.X)
    state.status = "connected"
    state.account_external_id = "1600"
    state.account_username = "pricechat"
    db.commit()
