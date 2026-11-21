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
os.environ["OPENAI_API_KEY"] = ""
for _k in ("COMMITTEE_MODEL_FAST", "COMMITTEE_MODEL_DEEP", "COMMITTEE_MODEL_SYNTH", "COMMITTEE_COSTS"):  # the committee: no paid models
    os.environ[_k] = ""
os.environ["COMMITTEE_BACKEND"] = "auto"
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
import sqlite3  # noqa: E402
from contextlib import closing  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402
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


@event.listens_for(engine, "connect")
def _no_fsync(dbapi_conn: Any, _record: Any) -> None:
    # A throwaway test database never needs to survive a power cut: skip the fsync on every commit.
    dbapi_conn.execute("PRAGMA synchronous=OFF")


_TEMPLATE_DB = _TEST_DB.with_name("template.db")


def _copy_sqlite(src: Path, dst: Path) -> None:
    """Page-for-page copy through SQLite's backup API (works while the destination file is in use)."""
    with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(dst)) as d:
        s.backup(d)


def reset_database() -> None:
    """Give the test a freshly migrated, empty database.

    Running all migrations takes ~1 s, so it happens once per session into a template file; every test then
    gets an exact copy of that template, so nothing a previous test wrote (rows, tables, sequences) survives."""
    engine.dispose()
    if not _TEMPLATE_DB.exists():
        _TEST_DB.unlink(missing_ok=True)
        init_db()
        engine.dispose()
        _copy_sqlite(_TEST_DB, _TEMPLATE_DB)
    _copy_sqlite(_TEMPLATE_DB, _TEST_DB)
    engine.dispose()


@pytest.fixture(autouse=True)
def _one_fake_market_for_both_networks(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: tests never read Hyperliquid's testnet. TESTNET accounts see the same (usually fake) market as mainnet,
    unless a test installs its own testnet market (monkeypatch market.testnet)."""
    from app.tradingos import market

    monkeypatch.setattr(market, "testnet", lambda: market.data(), raising=False)


@pytest.fixture(autouse=True)
def _no_live_photo_search(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: News Desk tests never search Wikimedia Commons or Openverse. A test that wants photos stubs photos.discover itself;
    tests/test_newsdesk_photos.py tests discover() with its own mocked transport."""
    if request.module.__name__.endswith("test_newsdesk_photos"):
        return
    from app.newsdesk import photos

    async def none(*_: object, **__: object) -> list[object]:
        return []

    monkeypatch.setattr(photos, "discover", none)
    from app.newsdesk import datapost

    async def no_prices(*_: object, **__: object) -> dict[str, object]:
        return {}

    from app.newsdesk import clean

    async def no_bars(*_: object, **__: object) -> list[object]:
        return []

    monkeypatch.setattr(clean, "fetch_bars", no_bars)  # the clean style's charts never read Yahoo in tests

    monkeypatch.setattr(datapost, "fetch_all", no_prices)  # nor Yahoo's daily closes: data-post tests pass their own
    from app.newsdesk import livedata

    async def no_quote(*_: object, **__: object) -> None:
        return None

    async def no_daily(*_: object, **__: object) -> tuple[list[object], None]:
        return [], None

    # the re-read before sending (app/newsdesk/livedata) never reaches Yahoo in tests: it uses what the test's desk pass read, else holds
    monkeypatch.setattr(livedata, "_fetch_quote", no_quote)
    monkeypatch.setattr(livedata, "_fetch_daily", no_daily)
    livedata.forget()


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


@pytest.fixture(autouse=True)
def _newsdesk_plan(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The News Desk's older tests were written for the 3-4 a day windows plan; production runs the six Dubai slots (2026-11-09), which
    tests/test_newsdesk_slots.py covers. Tests that want the slots set events.DEFAULT_SCHEDULE themselves."""
    from app.newsdesk import recap as _recap

    async def _no_xyz(name: str, transport: object = None) -> None:  # the trade.xyz second source is live data: never in tests
        return None

    monkeypatch.setattr(_recap, "xyz_change", _no_xyz)
    if request.module.__name__.split(".")[-1] in ("test_newsdesk", "test_newsdesk_events", "test_newsdesk_telegram", "test_newsdesk_collect", "test_x_social", "test_brand_publishing_safety"):
        from app.newsdesk import events

        from app.newsdesk import sources

        monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "windows")
        monkeypatch.setattr(sources, "EXTRA_FEEDS", [])  # their fake web answers only the feeds they set
        monkeypatch.setattr(sources, "WORLDMONITOR_FEEDS", [])
    if request.module.__name__.split(".")[-1] in ("test_newsdesk", "test_newsdesk_events", "test_newsdesk_telegram", "test_newsdesk_collect", "test_x_social",
                                                  "test_brand_publishing_safety", "test_newsdesk_slots", "test_tg_rules_hotfix"):
        from app.newsdesk import engine as nd_engine, recap as nd_recap, telegram as nd_telegram

        # written for the earlier text format (card + summary, written market brief); tests/test_newsdesk_visual.py covers the picture format
        monkeypatch.setattr(nd_telegram, "FORMAT", "classic")
        monkeypatch.setattr(nd_engine, "X_FORMAT", "classic")
        monkeypatch.setattr(nd_recap, "VISUAL", False)
    if request.module.__name__.split(".")[-1] in ("test_newsdesk", "test_newsdesk_events", "test_newsdesk_telegram", "test_newsdesk_slots", "test_newsdesk_visual",
                                                  "test_newsdesk_editorial", "test_x_follows_telegram", "test_newsdesk_x_live"):
        from app.newsdesk import events as nd_events

        # the market-check fallback is off in production (owner, 2026-11-10: skip, never fill); tests/test_newsdesk_hardblock.py covers that.
        # The older tests use it to make market posts, so they keep it on.
        monkeypatch.setattr(nd_events, "RECAP_FALLBACK", True)
    if request.module.__name__.split(".")[-1] != "test_x_follows_telegram":
        from app.newsdesk import events as nd_events

        # the older tests drive X on its own; tests/test_x_follows_telegram.py covers X mirroring Telegram (owner, 2026-11-09)
        monkeypatch.setattr(nd_events, "X_FOLLOWS_TELEGRAM", False)


@pytest.fixture(autouse=True)
def _vpn_payments_public(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: the Kiwi VPN tests written before payments' test mode (VPN_PAYMENTS_PUBLIC, default off in production) are about
    payments open to everyone. tests/test_vpn_payments_test_mode.py tests the default itself."""
    if request.module.__name__.endswith("test_vpn_payments_test_mode"):
        return
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "vpn_payments_public", True, raising=False)


@pytest.fixture(autouse=True)
def _classic_post_style(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: the News Desk's older tests are about the classic layout (still Admin's rollback); the clean style's own tests
    (test_newsdesk_clean*.py) set network.DEFAULT_STYLE = "clean"."""
    from app.newsdesk import network

    monkeypatch.setattr(network, "DEFAULT_STYLE", "classic")


@pytest.fixture(autouse=True)
def _market_posts_not_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: production starts with desk-measured market posts PAUSED until the owner resumes them (events.MARKET_PAUSE_DEFAULT,
    2026-11-21); the delivery tests are about what happens when they are allowed. tests/test_phase0_20261121.py tests the default."""
    from app.newsdesk import events

    monkeypatch.setattr(events, "MARKET_PAUSE_DEFAULT", False)


@pytest.fixture(autouse=True)
def _no_learned_casing(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: the casing vocabulary the desk learns from its feeds (app/newsdesk/wire.learn_vocabulary) starts empty in every test."""
    from app.newsdesk import wire

    monkeypatch.setattr(wire, "_VOCAB", {})


@pytest.fixture(autouse=True)
def _no_library_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: the subject library (app/newsdesk/photography) reads Wikimedia Commons; tests never touch the network, so the library
    is empty unless a test supplies its records (tests/test_newsdesk_photography_20261119.py)."""
    from app.newsdesk import photography

    async def none(client: object, subject: object) -> list:
        return []

    monkeypatch.setattr(photography, "_records", none)
    photography._cache.clear()
    from app.newsdesk import datapost

    datapost._last.clear()
