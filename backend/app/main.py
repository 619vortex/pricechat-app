import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agents.price_chat import AgentPausedError
from app.security.secrets import SecretStoreError
from app.api.routes import (
    activity,
    arbitrage,
    burner,
    admin,
    home,
    agent,
    api_keys,
    analytics,
    brand,
    community,
    connectors,
    content,
    growth,
    health,
    jobs,
    knowledge,
    model,
    opportunities,
    overview,
    pokemon,
    portfolio,
    market,
    invites,
    settings,
    social,
    system,
    telegram,
    telegram_account,
    website,
    seo,
    members,
    members_admin,
    x,
    youtube,
    exchange,
    vault,
    brand_manager,
    committee,
    kalshi,
    short_market,
    tradingbot,
    tradingos,
    trading,
    kiwibrain,
    rewards,
    vortex,
    webhooks,
    vpn,
    newsdesk,
    newsdesk_worker,
    terminal,
    trending,
    desktop,
    workers,
)
from app.config.logging import configure_logging
from app.config.settings import Settings, get_settings
from app.db.seed import seed_defaults
from app.db.session import SessionLocal, init_db
from app.jobs import build_jobs
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX, desktop_token_may
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.errors import (
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnknownProviderError,
)
from app.scheduler.scheduler import Scheduler
from app.services.activity import ActivityLogger
from app.services.runtime import AppRuntime
from app.telegram.account import AccountError, FloodWait, InvalidApiCredentials, SessionRevoked
from app.telegram.errors import TelegramAuthError, TelegramError, TelegramNotConfiguredError, TelegramRequestError
from app.telegram.ingestion import get_bot_state
from app.connectors.x.errors import (
    XAuthError,
    XBadRequestError,
    XBudgetExceededError,
    XCreditsError,
    XError,
    XForbiddenError,
    XNotConfiguredError,
    XNotFoundError,
    XRateLimitError,
)
from app.social.x_service import sync_runtime_from_db

logger = logging.getLogger("pricechat")

# visitors' Pokémon Market reads running at once (each holds a database connection; the pool is 10 + 30, app/db/session.py)
MARKET_CONCURRENCY = 10
MARKET_SLOT_WAIT_S = 8.0


def _provider_status_code(exc: ProviderError) -> int:
    if isinstance(exc, ProviderTimeoutError):
        return 504
    if isinstance(exc, (ProviderUnavailableError, ModelNotFoundError)):
        return 503
    if isinstance(exc, UnknownProviderError):
        return 500
    return 502


def _telegram_status_code(exc: TelegramError) -> int:
    if isinstance(exc, (TelegramNotConfiguredError, TelegramRequestError)):
        return 400
    if isinstance(exc, TelegramAuthError):
        return 502
    return 503


def create_app(
    app_settings: Settings | None = None,
    provider: LLMProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    use_embedder: bool = True,
    initialize_database: bool = True,
    background_jobs: bool = True,
) -> FastAPI:
    app_settings = app_settings or get_settings()
    configure_logging(app_settings.log_level)
    from app.security import client_ip

    client_ip.startup_check(app_settings)
    from app.security import admin_auth as _admin_auth

    _admin_auth.bind_credentials(app_settings)  # a changed ADMIN_PASSWORD_HASH ends every earlier owner session (AR-10)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = AppRuntime.from_settings(app_settings, provider, embedder, use_embedder)
        app.state.runtime = runtime
        if initialize_database:
            init_db()
            with SessionLocal() as db:
                agent_config = seed_defaults(db)
                from app.members.catalog import ensure_catalog

                ensure_catalog(db)  # Kiwi ranks, XP rules, badges, entitlements (never overwrites edits)
                state = get_bot_state(db)
                db.commit()
                sync_runtime_from_db(db, app_settings, runtime.x)
                if runtime.telegram.configured and state.status in ("connected", "error"):
                    runtime.telegram.set_status(state.status, state.last_error or "", state.bot_username)
                ActivityLogger(db, agent_config.id).log(
                    "system_started",
                    f"Price Chat backend started (AI provider: {runtime.provider.name}, "
                    f"model: {agent_config.model_name or runtime.provider.model}; "
                    f"Telegram: {'configured' if runtime.telegram.configured else 'not configured'})",
                )

        prev = app_settings.app_encryption_key_previous
        if initialize_database and prev and prev.get_secret_value().strip():
            # key rotation: re-seal everything under the new key, in the background (boot isn't held up; errors only logged)
            import threading

            from app.security import rekey

            threading.Thread(target=rekey.run_at_startup, args=(SessionLocal,), name="rekey", daemon=True).start()

        from app.tradingos import autodelete

        autodelete.install()  # notes the bot's main-chat messages for tradingos.autodelete
        from app.tradingos import committeecmd

        committeecmd.RUNTIME = runtime  # /committee in Telegram: the model provider, gate and Telegram transport
        # One scheduler for every background job; tests can turn it off.
        specs = build_jobs(runtime)
        off = app_settings.jobs_off(s.name for s in specs)
        async def owner_alert(spec, status, error, previous, failures):  # type: ignore[no-untyped-def]
            """A job failing 3 runs in a row (the backup: once) / working again → the owner's Telegram (app/alerts.py)."""
            from app import alerts

            with SessionLocal() as db:
                await alerts.job_outcome(db, spec.name, spec.label, status, error, previous, failures,
                                         settings=app_settings, transport=runtime.telegram.transport)

        runtime.scheduler = Scheduler(SessionLocal, specs, disabled=off, alert=owner_alert)
        if off:
            logger.warning("Background jobs turned off on this server: %s", ", ".join(sorted(off)))
        if initialize_database:
            runtime.scheduler.sync_jobs()
        if background_jobs and app_settings.scheduler_enabled and initialize_database:
            runtime.scheduler.start()
        from app import observability

        # error rate / slow + stalled jobs -> one owner alert each (and one "recovered"); runs where the scheduler does
        watchdog = observability.Watchdog(SessionLocal, app.state.http_errors, settings=app_settings,
                                          transport=runtime.telegram.transport, scheduler=runtime.scheduler)
        app.state.watchdog = watchdog
        if background_jobs and app_settings.scheduler_enabled and initialize_database:
            watchdog.start()
        if initialize_database:
            with SessionLocal() as db:
                observability.startup_self_check(app_settings, [s.name for s in specs], db)
                # the silence monitors' states (ND-2), in the log even while the owner alert chat isn't set (AR-2)
                observability.log_monitors(observability.silence_monitors(db, runtime.scheduler))
        else:
            observability.startup_self_check(app_settings, [s.name for s in specs])
        if app_settings.is_production and initialize_database:
            # the Visual Editor's production self-test (2026-11-19): is visual verification working on this server? (logs one line)
            import asyncio as _asyncio

            from app.newsdesk import editor as _editor

            app.state.vision_self_test = _asyncio.get_running_loop().create_task(_editor.self_test(app_settings))
            from app.newsdesk import photography as _photography  # the silver bullion library, checked on this server (2026-11-20)

            app.state.photo_self_test = _asyncio.get_running_loop().create_task(_photography.library_self_test("silver"))
        if app_settings.is_production and initialize_database:
            # build the market homepage's rows once now, in the background, so the first visitor after a deploy doesn't
            # wait for the ~50k-card build (the scheduled warm-up only runs where background jobs are on)
            import threading

            def _warm_market() -> None:
                try:
                    from app.pokemon import market

                    with SessionLocal() as db:
                        market.warm_homepage(db.get_bind())
                    logger.info("Market homepage warmed")
                except Exception:  # noqa: BLE001 - a failed warm-up only means the first request builds it
                    logger.warning("Market warm-up failed", exc_info=True)

            threading.Thread(target=_warm_market, name="market-warm", daemon=True).start()

        logger.info(
            "Price Chat API ready: provider=%s model=%s embeddings=%s telegram=%s scheduler=%s",
            runtime.provider.name,
            runtime.provider.model,
            runtime.embedder.model if runtime.embedder else "off",
            "configured" if runtime.telegram.configured else "not configured",
            "on" if runtime.scheduler._loop_task else "off",
        )
        try:
            yield
        finally:
            await watchdog.stop()
            await runtime.aclose()

    # API docs exist only outside production (in production they'd advertise every internal endpoint)
    docs = not app_settings.is_production
    app = FastAPI(title=app_settings.app_name, version="0.3.0", lifespan=lifespan,
                  docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None, openapi_url="/openapi.json" if docs else None)

    from starlette.concurrency import run_in_threadpool

    from app.api import edge_cache
    from app.security import admin_auth

    def _admin_ok(token: str | None) -> bool:
        if not token:  # no owner cookie: nothing to look up (spares a database connection on every visitor request)
            return False
        with SessionLocal() as db:
            return admin_auth.current(db, token) is not None

    allowed_origins = {o.rstrip("/") for o in app_settings.cors_origins} | {app_settings.members_public_url.rstrip("/")}
    market_slots = app.state.market_slots = asyncio.Semaphore(MARKET_CONCURRENCY)

    from app.security.body_limit import BodyLimit

    # public routes: bounded request bodies (413). Added before admin_guard so it sits INSIDE it: an over-limit body raises
    # straight into the route's body read (through admin_guard's task group it would arrive as an ExceptionGroup -> a 400)
    app.add_middleware(BodyLimit, video_mb=app_settings.bm_max_video_mb)

    @app.middleware("http")
    async def admin_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Every /api route is the owner's unless it's on the public list (admin_auth.PUBLIC_*): the website, the member
        APIs (with their own member sign-in) and the login itself. Enforced on the server, not by hiding links."""
        path, method = request.url.path, request.method
        if method != "OPTIONS" and path.startswith("/api/"):
            if request.cookies.get("pc_session", "").startswith(DESKTOP_TOKEN_PREFIX) and not desktop_token_may(path):
                return JSONResponse(status_code=403, content={"detail": "The Kiwi VPN app can't do that.", "code": "desktop_scope"})
            if app_settings.is_production and method in ("POST", "PUT", "PATCH", "DELETE"):
                origin = request.headers.get("origin")
                extension = origin in app_settings.vpn_extension_origins and path.startswith("/api/members/vpn/browser")  # our own extension, this one route
                if origin and origin.rstrip("/") not in allowed_origins and not extension:  # a form or script on another site
                    return JSONResponse(status_code=403, content={"detail": "Cross-site request refused.", "code": "bad_origin"})
            if app_settings.admin_auth_required and not admin_auth.is_public(method, path):
                from app.pokemon import public as market_public

                admin = await run_in_threadpool(_admin_ok, request.cookies.get(admin_auth.COOKIE))
                if not admin and market_public.is_public_get(method, path):
                    # the public Pokémon Market tab: read-only, and cleaned of what visitors may not see
                    refused = market_public.grade_refused(path, request.query_params)
                    if refused:
                        return JSONResponse(status_code=400, content={"detail": refused, "code": "grade_not_listed"})
                    from app.security import rate_limit

                    wait = rate_limit.retry_after("market", request)
                    if wait is not None:
                        return JSONResponse(status_code=429, content={"detail": "Too many requests. Please slow down.", "code": "rate_limited"},
                                            headers={"Retry-After": str(wait)})
                    # at most MARKET_CONCURRENCY visitor reads at once, so a crawler can never take every database connection
                    # from the bot, the scheduler and members (a full house answers 503 after a short wait, not a 30 s pool timeout)
                    try:
                        await asyncio.wait_for(market_slots.acquire(), MARKET_SLOT_WAIT_S)
                    except TimeoutError:
                        return JSONResponse(status_code=503, content={"detail": "The market is busy. Please retry in a moment.", "code": "busy"},
                                            headers={"Retry-After": "5"})
                    try:
                        response = await call_next(request)
                    finally:
                        market_slots.release()
                    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("application/json"):
                        return response
                    import json

                    body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
                    clean = market_public.sanitize(json.loads(body), path)
                    # the public website marks its market reads (?pub=1), so only those addresses are kept at the edge: the
                    # owner's dashboard asks without it and never gets a visitor's cleaned copy from the CDN
                    cache = edge_cache.header(60) if request.query_params.get("pub") == "1" else "public, max-age=60"
                    return JSONResponse(status_code=200, content=clean, headers={"Cache-Control": cache})
                if not admin:
                    return JSONResponse(status_code=401, content={"detail": "Admin login required.", "code": "admin_auth"})
        return edge_cache.apply(request, await call_next(request))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(status_code=_provider_status_code(exc), content={"detail": str(exc), "code": exc.code})

    @app.exception_handler(AccountError)
    async def telegram_account_error(request: Request, exc: AccountError) -> JSONResponse:
        code = 429 if isinstance(exc, FloodWait) else 401 if isinstance(exc, (SessionRevoked, InvalidApiCredentials)) else 400
        return JSONResponse(status_code=code, content={"detail": str(exc), "code": exc.code})

    @app.exception_handler(TelegramError)
    async def telegram_error(request: Request, exc: TelegramError) -> JSONResponse:
        return JSONResponse(status_code=_telegram_status_code(exc), content={"detail": str(exc), "code": exc.code})

    @app.exception_handler(XError)
    async def x_error(request: Request, exc: XError) -> JSONResponse:
        code = 400 if isinstance(exc, (XNotConfiguredError, XBadRequestError)) else 402 if isinstance(exc, (XBudgetExceededError, XCreditsError))             else 429 if isinstance(exc, XRateLimitError) else 404 if isinstance(exc, XNotFoundError) else 502 if isinstance(exc, (XAuthError, XForbiddenError)) else 503
        return JSONResponse(status_code=code, content={"detail": str(exc), "code": exc.code})

    @app.exception_handler(SecretStoreError)
    async def secret_store_error(request: Request, exc: SecretStoreError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "code": "secret_store"})

    @app.exception_handler(AgentPausedError)
    async def agent_paused(request: Request, exc: AgentPausedError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "code": "agent_paused"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        try:
            return await request_validation_exception_handler(request, exc)
        except ValueError:  # the default 422 echoes each input: a NaN / Infinity in the body can't be, which made it a 500
            return JSONResponse(status_code=422, content={"detail": jsonable_encoder([{k: v for k, v in e.items() if k != "input"} for e in exc.errors()])})

    from sqlalchemy.exc import DataError

    @app.exception_handler(OverflowError)
    @app.exception_handler(DataError)
    async def out_of_range(request: Request, exc: Exception) -> JSONResponse:
        # an id / number too big for the database column (e.g. /api/pokemon/cards/99999999999999999999): the caller's input,
        # not a server fault. SQLite raises OverflowError while binding it, Postgres a DataError (numeric value out of range).
        logger.warning("Out-of-range value on %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return JSONResponse(status_code=400, content={"detail": "A value in the request is out of range.", "code": "out_of_range"})

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        from app.observability import route_of

        rid = getattr(request.state, "request_id", None) or "-"
        # the route template, not the raw path (ids / tokens in it); the request id matches the request log line
        logger.exception("Unhandled error on %s %s rid=%s", request.method, route_of(request.scope), rid)
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "code": "internal_error", "request_id": rid},
                            headers={"X-Request-ID": rid})

    for module in (
        health, overview, agent, brand, model, knowledge, content, connectors, activity, settings, api_keys,
        telegram, telegram_account, community, opportunities, jobs, x, social, growth, analytics, youtube, website,
        members, members_admin, admin, pokemon, portfolio, market, exchange, vault, brand_manager, newsdesk, newsdesk_worker, trending, terminal, desktop, short_market, invites, system, tradingbot, tradingos, trading, kiwibrain, arbitrage, burner, workers, rewards, home, seo, vpn, vortex, committee, webhooks, kalshi,
    ):
        app.include_router(module.router, prefix="/api")

    from app.observability import ErrorWindow, RequestObserver

    from app.security.headers import SecurityHeaders

    # nosniff / frame / referrer (+ HSTS in production) on every response, also the ones that reach api.pricechat.live directly,
    # never through Vercel's header rules; outside admin_guard and CORS, so their 401/403/429 answers carry them too
    app.add_middleware(SecurityHeaders, hsts=app_settings.is_production)

    # outermost (added last): one log line + X-Request-ID per request, and the 5xx window the watchdog alerts on
    app.state.http_errors = ErrorWindow()
    app.add_middleware(RequestObserver, window=app.state.http_errors)
    return app


app = create_app()
