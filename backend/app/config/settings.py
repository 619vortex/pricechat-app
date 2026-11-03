"""Application settings. All configuration and secrets come from environment / .env."""

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
SEED_DIR = Path(__file__).resolve().parents[2] / "seeds"  # backend/seeds, wherever the backend folder is deployed

_LOCAL = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Price Chat"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = f"sqlite:///{(DATA_DIR / 'price_chat.db').as_posix()}"

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # Model layer. Only app.providers.factory interprets provider names and provider-specific keys.
    llm_provider: str = "ollama"
    llm_model: str = "llama3.1:8b"
    llm_temperature: float = 0.7
    llm_timeout_seconds: float = 120.0
    # Strong-tier (e.g. gemma3:27b) background analysis: loading the model can take a while.
    llm_strong_timeout_seconds: float = Field(300.0, ge=30)

    embedding_provider: str = "ollama"  # "none" disables semantic retrieval
    embedding_model: str = "nomic-embed-text"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_keep_alive: str = "30m"

    # IANA zone for "today"/"yesterday" (e.g. Asia/Dubai). Empty = this computer's zone.
    timezone: str = ""

    # Agent runtime
    conversation_window: int = Field(20, ge=2, le=200)  # recent messages sent with each request
    memory_recall_limit: int = Field(6, ge=0, le=50)
    memory_prompt_char_budget: int = Field(4000, ge=500, le=50000)
    # Cosine similarity a memory needs to count as a meaning match. Calibrated for nomic-embed-text;
    # re-check it if you change EMBEDDING_MODEL.
    retrieval_min_similarity: float = Field(0.60, ge=0.0, le=1.0)

    seed_file: Path = SEED_DIR / "price_chat.json"

    # Task → model routing tiers (see app/ai/routing.py; per-task overrides are set in the dashboard)
    model_tier_fast: str = "llama3.1:8b"
    model_tier_strong: str = "gemma3:27b"

    # Telegram (official Bot API). The token is a secret: .env only, never logged or sent to the browser.
    telegram_bot_token: SecretStr | None = None
    telegram_api_base: str = "https://api.telegram.org"
    telegram_long_poll_seconds: int = Field(10, ge=0, le=50)
    telegram_listen_seconds: int = Field(90, ge=0, le=600)  # each ingest run keeps long-polling this long (0 = one batch, the old way)

    # Background jobs
    scheduler_enabled: bool = True
    job_telegram_ingest_seconds: int = Field(20, ge=5)
    job_community_process_seconds: int = Field(60, ge=10)
    job_community_topics_seconds: int = Field(300, ge=30)
    job_summary_recent_seconds: int = Field(10800, ge=300)
    job_summary_daily_seconds: int = Field(3600, ge=300)
    job_memory_extraction_seconds: int = Field(1800, ge=300)
    job_opportunities_seconds: int = Field(3600, ge=300)
    job_knowledge_reindex_seconds: int = Field(1800, ge=300)

    # Community intelligence tuning (similarities calibrated for nomic-embed-text)
    community_topic_similarity: float = Field(0.75, ge=0, le=1)
    community_question_similarity: float = Field(0.80, ge=0, le=1)
    community_topic_min_messages: int = Field(3, ge=2)
    community_topic_min_participants: int = Field(2, ge=1)
    community_recurring_min_asks: int = Field(2, ge=2)
    community_summary_min_messages: int = Field(8, ge=1)

    # Secrets saved from the dashboard (Windows DPAPI-encrypted). .env values above/below are the fallback.
    secrets_file: Path = DATA_DIR / "secrets.dpapi"

    # X (official X API v2). Credentials can be saved from the dashboard instead of .env.
    x_bearer_token: SecretStr | None = None
    # YouTube (read-only, official Data API v3). Usually entered on the dashboard (YouTube page) instead.
    youtube_api_key: SecretStr | None = None
    youtube_client_id: SecretStr | None = None
    youtube_client_secret: SecretStr | None = None
    # "Sign in with Google" for members: its own OAuth Web client (falls back to the YouTube client when unset).
    # Authorized redirect URI: {members_public_url}/api/members/auth/google/callback
    google_member_client_id: SecretStr | None = None
    google_member_client_secret: SecretStr | None = None
    youtube_refresh_token: SecretStr | None = None
    youtube_api_base: str = "https://www.googleapis.com/youtube/v3"
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    # Must match the redirect URI registered on the Google OAuth client (only for the OAuth option).
    youtube_redirect_uri: str = "http://127.0.0.1:8000/api/youtube/oauth/callback"
    dashboard_url: str = "http://127.0.0.1:5173/admin"  # where the internal dashboard lives (behind the admin login)
    # Community members (website sign-in). ONE canonical public address (scheme + host + port, no trailing slash):
    # the website redirects other hosts (e.g. localhost) to it, and the member OAuth callbacks are derived from it:
    #   {members_public_url}/api/members/auth/google/callback  and  {members_public_url}/api/members/auth/x/callback
    # Register exactly those on the Google OAuth client and the X app. Use the real https address once deployed.
    members_public_url: str = "http://127.0.0.1:5173"
    # Vortex Realm online: the WebSocket URL the game connects to (the API host itself: Vercel's proxy carries no WebSockets).
    # Empty = derived from the ticket request's own host.
    realm_ws_url: str = ""
    # Only if a provider must use a different callback than the derived one (normally leave empty).
    member_google_redirect_uri: str | None = None
    member_x_redirect_uri: str | None = None
    member_session_days: int = Field(30, ge=1, le=365)
    # Passwordless email sign-in (members/email_login.py). Codes are sent through Resend (resend.com); the API key is
    # saved encrypted on Settings › API keys (or RESEND_API_KEY). The sender's domain must be verified at Resend.
    resend_api_key: SecretStr | None = None
    # Kiwi VPN payments (app/vpn/billing.py). Without both keys of a provider, that way of paying is simply off.
    nowpayments_api_key: SecretStr | None = None
    nowpayments_ipn_secret: SecretStr | None = None
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    # The website's chat bubble: a read-only preview of the PUBLIC Telegram group (first names only). Off = no bubble.
    website_chat_enabled: bool = True
    # Pokémon Portfolio. Photos: a PRIVATE S3-compatible bucket (Railway bucket: endpoint, region, key id, secret, name).
    # Without a bucket, development stores photos under portfolio_upload_dir; production refuses uploads.
    portfolio_bucket: str | None = None
    portfolio_s3_endpoint: str | None = None
    portfolio_s3_region: str | None = None
    portfolio_s3_key_id: SecretStr | None = None
    portfolio_s3_secret: SecretStr | None = None
    portfolio_upload_dir: Path = DATA_DIR / "uploads"
    # Card scanner (Scrydex Vision, 5 credits a scan) and the AI condition assistant (Anthropic vision): daily limits
    portfolio_scans_per_day: int = Field(40, ge=0, le=1000)  # per member
    portfolio_scans_total_per_day: int = Field(1500, ge=0)  # whole site (credit protection)
    anthropic_api_key: SecretStr | None = None
    condition_model: str = "claude-sonnet-5-5"
    # Kiwi AI Investment Committee (app/committee). Backend: auto | vortex | router | anthropic | openai | rules. Model names
    # for the paid backends come from here only (none hardcoded); keys from ANTHROPIC_API_KEY / OPENAI_API_KEY or the store.
    committee_backend: str = "auto"
    committee_model_fast: str = ""
    committee_model_deep: str = ""
    committee_model_synth: str = ""
    committee_costs: str = ""  # JSON {"model": [usd per 1M input tokens, per 1M output]} over the built-in table
    committee_daily_limit: int = Field(3, ge=0, le=1000)  # runs per Telegram user / member a day
    committee_daily_limit_ranked: int = Field(10, ge=0, le=1000)  # Passport members at / above the boost Kiwi Rank
    committee_max_concurrent: int = Field(2, ge=1, le=20)
    committee_daily_calls: int = Field(300, ge=0)
    committee_daily_usd: float = Field(2.0, ge=0)
    committee_call_timeout_seconds: float = Field(90.0, ge=5)
    openai_api_key: SecretStr | None = None
    portfolio_conditions_per_day: int = Field(5, ge=0, le=100)  # per member
    portfolio_conditions_total_per_day: int = Field(300, ge=0)
    # Card Marketplace (docs/MARKETPLACE.md). "simulation": sandbox listings + simulated USDT payments only. "live" is refused
    # by app.market.compliance unless every launch gate is met (explicit approval, payment/vault/KYC partners, PostgreSQL).
    market_mode: Literal["off", "simulation", "live"] = "simulation"
    market_live_approved: bool = False
    market_payment_partner: str | None = None  # a regulated USDT payment / escrow provider (none contracted yet)
    market_vault_partners: str = ""  # comma-separated approved vault partners (none contracted yet)
    market_kyc_provider: str | None = None
    market_seller_fee_bps: int = Field(500, ge=0, le=2000)  # platform fee taken from the seller's proceeds
    market_buyer_fee_bps: int = Field(0, ge=0, le=1000)
    market_high_value_cents: int = Field(100_000, ge=0)  # listings at/above this need a level-2 verified seller
    market_blocked_countries: str = "CU,IR,KP,SY,RU,BY,MM"  # sanctions block-list (ISO-2); legal review must confirm
    market_order_minutes: int = Field(30, ge=5, le=240)  # time to pay before a reservation expires
    market_offer_hours: int = Field(48, ge=1, le=336)
    # TESTNET payments (no value): the owner's receiving TEST wallet (an address only, never a key). Unset = testnet off.
    market_testnet_receive: str | None = None
    market_testnet_rpc: str = "https://ethereum-sepolia-rpc.publicnode.com"  # read-only Sepolia JSON-RPC
    email_from: str = "Price Chat <login@pricechat.live>"
    email_code_minutes: int = Field(10, ge=3, le=30)
    # Telegram's Login Widget needs a public domain (BotFather /setdomain); locally members link Telegram by
    # pressing Start on a one-time bot link instead. Turn this off once the widget is set up on the real domain.
    members_telegram_deeplink: bool = True
    x_api_key: SecretStr | None = None
    x_api_secret: SecretStr | None = None
    # MEMBER sign-in with X (OAuth 2.0 + PKCE). Separate from the @pricechat brand connector above: this only proves
    # "this X account belongs to me" for a community member. Client ID/secret from the X app's "OAuth 2.0 Client ID".
    x_member_client_id: SecretStr | None = None
    x_member_client_secret: SecretStr | None = None  # empty for a public (native) client
    # Connect X asks for read-only access unless this is on. Turn it on only once the X app's "App permissions" is "Read and
    # write": asking a Read-only app for tweet.write makes X refuse the sign-in ("You weren't able to give access to the App").
    # Off, members still reply / repost on X itself (the buttons open the post) and the XP is still verified by reading.
    x_member_write: bool = False
    x_oauth2_authorize_url: str = "https://x.com/i/oauth2/authorize"
    x_oauth2_token_url: str = "https://api.x.com/2/oauth2/token"
    x_oauth2_revoke_url: str = "https://api.x.com/2/oauth2/revoke"
    # Combot (official API v5, group admin's API key) for legacy Telegram reputation
    combot_api_key: SecretStr | None = None
    # --- Pokémon Vintage Market Cap providers (all optional) ---
    # PSA Public API bearer token (cert verification + spec population). .env name PSA_API_KEY (PSA_API_TOKEN also read).
    psa_api_token: SecretStr | None = Field(None, validation_alias=AliasChoices("psa_api_key", "psa_api_token"))
    ebay_client_id: SecretStr | None = None
    ebay_client_secret: SecretStr | None = None
    ebay_dev_id: SecretStr | None = None  # identifies the developer account; OAuth client credentials don't need it
    pricecharting_api_key: SecretStr | None = None  # PRICECHARTING_API_KEY; internal use only unless PriceCharting grants a display licence
    pricecharting_api_base: str = "https://www.pricecharting.com"
    ebay_api_base: str = "https://api.ebay.com"
    ebay_marketplace: str = "EBAY_US"
    # --- Brand Manager (docs/BRAND_MANAGER.md): the AI social media manager, TikTok first ---
    # TikTok app (developers.tiktok.com): Client key + Client secret saved on Settings › API keys (or TIKTOK_CLIENT_KEY /
    # TIKTOK_CLIENT_SECRET). Redirect URI to register: {members_public_url}/api/brand-manager/tiktok/callback
    tiktok_client_key: SecretStr | None = None
    tiktok_client_secret: SecretStr | None = None
    # Vortex trade checks (/help <trade>): the bearer Vortex (the owner's home AI) uses on /api/vortex/*. Set in Admin › Trading OS.
    vortex_assist_token: SecretStr | None = None
    # Earn › Arbitrage › Lunch Bot (paper only: reads pump.fun launches and Solana accounts, never signs anything)
    lunch_bot_enabled: bool = True
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    pumpportal_api_key: SecretStr | None = None  # optional: PumpPortal trade-by-trade feed (a key funded with 0.02 SOL)
    tiktok_auth_url: str = "https://www.tiktok.com/v2/auth/authorize/"
    tiktok_api_base: str = "https://open.tiktokapis.com"
    # video.publish only when the app's Content Posting API has Direct Post enabled (TikTok rejects the sign-in otherwise)
    tiktok_scopes: str = "user.info.basic,user.info.profile,user.info.stats,video.list,video.upload"
    # Where TikTok pulls finished videos from (PULL_FROM_URL): this URL prefix must be verified in the TikTok app
    bm_media_base_url: str = "https://api.pricechat.live"
    bm_lease_seconds: int = Field(900, ge=60, le=7200)  # a worker's claim on a job; progress reports extend it
    bm_tick_seconds: int = Field(60, ge=15, le=3600)
    bm_max_video_mb: int = Field(120, ge=5, le=1024)
    bm_worker_bootstrap_sha256: str | None = None  # sha256 of a worker token kept on the worker machine (see queue.worker_for)
    # --- Price Chat Vault (docs/VAULT.md): the curated storefront of vintage Japanese cards from approved sellers ---
    vault_ebay_sellers: str = "mayumicardshop|Mayumi Card Shop"  # username|display name, comma-separated; synced via the Browse API
    vault_sync_seconds: int = Field(10800, ge=900, le=21600)  # eBay's licence: displayed listings at most 6 h older than eBay's
    vault_max_age_hours: int = Field(6, ge=1, le=24)  # older than this: price shown as "last checked …", Request to Buy paused
    vault_ebay_max_pages: int = Field(10, ge=1, le=50)  # 200 listings a page
    vault_ebay_details_per_sync: int = Field(60, ge=0, le=500)  # getItem calls for new listings' item specifics + photos
    vault_ebay_daily_calls: int = Field(1500, ge=50, le=5000)  # the Vault's share of eBay's 5,000 calls a day
    vault_include_graded: bool = False  # the store shows ungraded cards; graded slabs are excluded by the classifier
    vault_requests_enabled: bool = True  # members can send a Request to Buy (no payment, no reservation)
    # Paper Exchange (simulated spot + perps trading): retired from the website. Off = member endpoints answer 410 and
    # its jobs only prune old rows. Data is kept.
    exchange_enabled: bool = False
    ebay_cards_per_sync: int = Field(150, ge=1, le=1000)  # scheduled sync: Top 100 + nearest candidates, PSA 10 (1-2 calls each)
    pokemon_top100_min_history: int = Field(8, ge=2, le=365)  # observations in the last year a Top 100 card needs for a real chart
    pokemon_top100_max_age_days: int = Field(10, ge=1, le=60)  # a provider valuation older than this is stale
    pokemon_top100_backfill_requests: int = Field(300, ge=0, le=3000)  # Scrydex credits a Top 100 assessment may spend on history
    fx_api_base: str = "https://api.frankfurter.dev/v1"  # ECB reference rates (free, no key) for non-USD sales
    pokemontcg_api_key: SecretStr | None = None  # optional: the Pokémon TCG API works without one at a lower rate limit
    pokemontcg_api_base: str = "https://api.pokemontcg.io/v2"
    # Scrydex (paid): graded market prices, PSA population per variant, price history. Headers X-Api-Key + X-Team-ID.
    scrydex_api_key: SecretStr | None = None
    scrydex_team_id: SecretStr | None = None
    scrydex_api_base: str = "https://api.scrydex.com"
    scrydex_cache_hours: int = Field(12, ge=0)  # responses are cached on disk: re-runs don't spend credits
    scrydex_cache_dir: Path = DATA_DIR / "cache" / "scrydex"
    scrydex_max_requests_per_sync: int = Field(150, ge=1, le=5000)  # hard cap per sync run (1 request = 1 credit)
    tcgdex_api_base: str = "https://api.tcgdex.net/v2"  # TCGdex REST (no key): card catalog + images, English + Japanese
    tcgdex_cache_dir: Path = DATA_DIR / "cache" / "tcgdex"  # cached API responses (pricing fields stripped)
    tcgdex_cache_hours: int = Field(7 * 24, ge=0)
    psa_api_base: str = "https://api.psacard.com/publicapi"
    gemrate_api_key: SecretStr | None = None  # GemRate API (population + population history), from the GemRate team
    gemrate_api_base: str = "https://api.gemrate.com/v1"
    job_pokemon_snapshot_seconds: int = Field(24 * 3600, ge=600)  # once a day: Price Chat's own history
    job_pokemon_sync_seconds: int = Field(6 * 3600, ge=600)
    job_pokemon_catalog_seconds: int = Field(3600, ge=600)  # hourly; each set is refreshed once a week
    pokemon_catalog_sets_per_run: int = Field(40, ge=1, le=500)
    pokemon_catalog_requests_per_run: int = Field(150, ge=1, le=1000)  # 1 credit each
    combot_api_base: str = "https://api.combot.org/v5"
    job_combot_sync_seconds: int = Field(6 * 3600, ge=900)
    x_access_token: SecretStr | None = None
    x_access_token_secret: SecretStr | None = None
    x_api_base: str = "https://api.x.com"
    x_account_username: str = ""
    # X bills per resource read (pay-per-use). Local guard rails on top of the console spending limit.
    # Local estimate per X write request (X does not report per-request charges through the API).
    x_write_cost_estimate_usd: float = Field(0.01, ge=0)
    job_pokemon_pulse_seconds: int = Field(900, ge=300)  # Market Pulse X search (only when enabled in the dashboard)
    x_daily_budget_usd: float = Field(1.0, ge=0)
    x_monthly_budget_usd: float = Field(20.0, ge=0)

    # More background jobs
    job_x_ingest_seconds: int = Field(900, ge=60)
    job_x_own_seconds: int = Field(900, ge=120)  # Read & Earn: Price Chat's own new X posts
    job_x_opportunities_seconds: int = Field(1800, ge=300)
    job_cross_signals_seconds: int = Field(900, ge=120)
    job_topic_merge_seconds: int = Field(3600, ge=300)
    job_opportunity_rank_seconds: int = Field(1800, ge=300)
    job_approvals_expire_seconds: int = Field(600, ge=60)
    job_members_verify_seconds: int = Field(600, ge=60)  # Kiwi XP from verified Telegram / X activity
    job_youtube_sync_seconds: int = Field(900, ge=120)  # new uploads appear on the website within this interval
    # Background jobs that must NOT run here (comma list or JSON list), e.g. "telegram.ingest,x.ingest" on a laptop
    # while production reads Telegram and X. Telegram allows ONE reader per bot token.
    scheduler_disabled_jobs: str = ""
    # The opposite: when set, ONLY these jobs run here (comma list; an entry ending in "." is a prefix, e.g. "pokemon." on
    # production, which refreshes the public market while the laptop keeps Telegram / AI work). Combined with the above.
    scheduler_only_jobs: str = ""

    @property
    def disabled_jobs(self) -> set[str]:
        return {j.strip() for j in self.scheduler_disabled_jobs.replace(";", ",").split(",") if j.strip()}

    def jobs_off(self, names: Iterable[str]) -> set[str]:
        """Every job that must not run on this server: the disabled list, plus everything outside the only-list."""
        only = [j.strip() for j in self.scheduler_only_jobs.replace(";", ",").split(",") if j.strip()]
        off = set(self.disabled_jobs)
        if only:
            off |= {n for n in names if not any(n == j or (j.endswith(".") and n.startswith(j)) for j in only)}
        return off

    # --- production safety ----------------------------------------------------------------------------------
    # Owner login for the internal dashboard and every admin API. Required in production; optional locally (when
    # ADMIN_PASSWORD_HASH is set locally, the local dashboard asks for it too). Make the hash with:
    #   python -m app.security.admin_auth hash
    admin_username: str | None = None
    admin_password_hash: SecretStr | None = None
    admin_session_hours: int = Field(12, ge=1, le=24 * 30)
    # Encrypts stored secrets and members' X tokens where Windows DPAPI isn't available (production on Linux).
    # A Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    app_encryption_key: SecretStr | None = None
    # Key rotation: the old key(s), comma-separated, still accepted for decrypting; new values always use the key above.
    # While set, startup re-encrypts everything under the new key (app/security/rekey.py). Remove once that's done.
    app_encryption_key_previous: SecretStr | None = None
    # Client addresses for rate limits (app/security/client_ip.py). PROXY_SHARED_SECRET: the same long random value in
    # Railway and in Vercel's project env; Vercel's /api rewrite sends it as x-pc-edge, and only then is Vercel's report of
    # the visitor believed; other callers are keyed on the address Railway's edge saw (right-most X-Forwarded-For).
    # Unset: the old, forgeable left-most entry is kept (and startup warns).
    # Comma-separate old,new while rotating. TRUSTED_PROXY_HOPS: how many right-most X-Forwarded-For entries our own
    # proxies append (Railway's edge: 1).
    proxy_shared_secret: SecretStr | None = None
    trusted_proxy_hops: int = Field(1, ge=1, le=5)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def admin_auth_required(self) -> bool:
        return self.is_production or bool(self.admin_password_hash and self.admin_password_hash.get_secret_value().strip())

    @model_validator(mode="after")
    def _production_requirements(self) -> "Settings":
        """In production nothing silently falls back to a local address or a throwaway location: the app refuses to
        start, naming what's missing (names only, never values)."""
        if not self.is_production:
            return self
        problems: list[str] = []
        explicit = self.model_fields_set
        for name in ("members_public_url", "dashboard_url", "cors_origins", "database_url"):
            if name not in explicit:
                problems.append(f"{name.upper()} must be set")
        for name, value in (("MEMBERS_PUBLIC_URL", self.members_public_url), ("DASHBOARD_URL", self.dashboard_url)):
            if any(h in value for h in _LOCAL) or not value.startswith("https://"):
                problems.append(f"{name} must be the public https address")
        if not self.cors_origins or any(any(h in o for h in _LOCAL) or not o.startswith("https://") for o in self.cors_origins):
            problems.append("CORS_ORIGINS must list only public https origins")
        if not self.admin_username or not (self.admin_password_hash and self.admin_password_hash.get_secret_value().strip()):
            problems.append("ADMIN_USERNAME and ADMIN_PASSWORD_HASH must be set")
        if not (self.app_encryption_key and self.app_encryption_key.get_secret_value().strip()):
            problems.append("APP_ENCRYPTION_KEY must be set")
        if problems:
            raise ValueError("Production configuration incomplete: " + "; ".join(problems))
        self.members_public_url = self.members_public_url.rstrip("/")
        if "youtube_redirect_uri" not in explicit:
            self.youtube_redirect_uri = f"{self.members_public_url}/api/youtube/oauth/callback"
        if "secrets_file" not in explicit and self.database_url.startswith("sqlite:///"):
            # next to the database, on the persistent volume
            self.secrets_file = Path(self.database_url.removeprefix("sqlite:///")).parent / "secrets.json"
        return self

    @property
    def telegram_configured(self) -> bool:
        from app.security.credentials import telegram_token

        return telegram_token(self) is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
