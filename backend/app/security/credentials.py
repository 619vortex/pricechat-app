"""Resolve connector credentials: dashboard-saved secrets first, then .env.

This is the only module that reads secret values. Callers receive plain strings
and must never log, return or forward them (in particular: never into a prompt).
"""

from dataclasses import dataclass
from functools import lru_cache

from app.config.settings import Settings, get_settings
from app.security.secrets import SecretInfo, SecretStore

# name in the store -> attribute on Settings (.env fallback)
KNOWN_SECRETS: dict[str, str] = {
    "telegram_bot_token": "telegram_bot_token",
    "telegram_api_hash": "telegram_api_hash",
    "telegram_session": "telegram_session",  # Telethon StringSession (encrypted at rest like every secret)
    "x_bearer_token": "x_bearer_token",
    "x_api_key": "x_api_key",
    "x_api_secret": "x_api_secret",
    "x_access_token": "x_access_token",
    "x_access_token_secret": "x_access_token_secret",
    # YouTube (read-only): an API key, or a Google OAuth client + the refresh token from the consent screen.
    "youtube_api_key": "youtube_api_key",
    "youtube_client_id": "youtube_client_id",
    "youtube_client_secret": "youtube_client_secret",
    "youtube_refresh_token": "youtube_refresh_token",
    # Member sign-in with X (OAuth 2.0), deliberately separate from the brand connector's keys above
    "x_member_client_id": "x_member_client_id",
    # Member sign-in with Google: a dedicated OAuth Web client (the YouTube connector keeps its own client + token)
    "google_member_client_id": "google_member_client_id",
    "google_member_client_secret": "google_member_client_secret",
    # Email sign-in codes (Resend): sending access; a full-access key also lets the owner set up the domain from the dashboard
    "resend_api_key": "resend_api_key",
    # Pokémon Portfolio: the AI condition assistant (Anthropic vision)
    "anthropic_api_key": "anthropic_api_key",
    "x_member_client_secret": "x_member_client_secret",
    # Combot official API key (group admin) for importing legacy Telegram reputation
    "combot_api_key": "combot_api_key",
    # Pokémon Vintage Market Cap data providers (app/providers/pokemon). Optional; each provider reports itself
    # "not connected" until its own credential is present.
    "psa_api_token": "psa_api_token",
    "ebay_client_id": "ebay_client_id",
    "ebay_client_secret": "ebay_client_secret",
    "ebay_dev_id": "ebay_dev_id",
    "pricecharting_api_key": "pricecharting_api_key",
    "pokemontcg_api_key": "pokemontcg_api_key",
    "gemrate_api_key": "gemrate_api_key",
    "scrydex_api_key": "scrydex_api_key",
    "scrydex_team_id": "scrydex_team_id",
    # Brand Manager: the TikTok app's client credentials (members' TikTok tokens are stored by app/brand_manager/tiktok.py)
    "tiktok_client_key": "tiktok_client_key",
    "tiktok_client_secret": "tiktok_client_secret",
    # Vortex trade checks: the bearer token of the owner's home AI on /api/vortex/* (app/tradingos/assist.py)
    "vortex_assist_token": "vortex_assist_token",
}


# .env names that differ from the store name (shown to the owner and written by Settings › API keys)
ENV_NAMES: dict[str, str] = {"psa_api_token": "PSA_API_KEY"}


def env_name(name: str) -> str:
    return ENV_NAMES.get(name, name.upper())


@lru_cache
def _store_for(path: str) -> SecretStore:
    from pathlib import Path

    return SecretStore(Path(path))


def secret_store(settings: Settings | None = None) -> SecretStore:
    settings = settings or get_settings()
    return _store_for(str(settings.secrets_file))


def resolve(name: str, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    value = secret_store(settings).get(name)
    if value:
        return value
    env = getattr(settings, KNOWN_SECRETS[name], None)
    if env is not None:
        raw = env.get_secret_value().strip()
        return raw or None
    return None


def info(name: str, settings: Settings | None = None) -> SecretInfo:
    settings = settings or get_settings()
    store = secret_store(settings)
    saved = store.saved_at(name)
    if saved is not None:
        return SecretInfo(name, True, "dashboard", saved)
    env = getattr(settings, KNOWN_SECRETS[name], None)
    if env is not None and env.get_secret_value().strip():
        return SecretInfo(name, True, "env", None)
    return SecretInfo(name, False, None, None)


def telegram_token(settings: Settings | None = None) -> str | None:
    return resolve("telegram_bot_token", settings)


@dataclass(frozen=True, slots=True)
class XCredentials:
    bearer_token: str | None
    api_key: str | None
    api_secret: str | None
    access_token: str | None
    access_token_secret: str | None

    @property
    def app_only(self) -> bool:
        return bool(self.bearer_token)

    @property
    def user_context(self) -> bool:
        return all((self.api_key, self.api_secret, self.access_token, self.access_token_secret))

    @property
    def configured(self) -> bool:
        return self.app_only or self.user_context

    def __repr__(self) -> str:  # never print values
        return f"XCredentials(app_only={self.app_only}, user_context={self.user_context})"


def x_credentials(settings: Settings | None = None) -> XCredentials:
    return XCredentials(
        resolve("x_bearer_token", settings),
        resolve("x_api_key", settings),
        resolve("x_api_secret", settings),
        resolve("x_access_token", settings),
        resolve("x_access_token_secret", settings),
    )
