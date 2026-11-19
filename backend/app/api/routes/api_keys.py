"""Settings › API keys: the Pokémon data providers' keys in one place.

Values are stored encrypted (app.security.secrets) and take priority over .env; they are never returned. The list
shows names, where each key comes from ("dashboard" / "env") and when it was saved.
Locally, PUT /env also writes NAME=value into the project's .env (applied when the backend next starts).
"""

import os
import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from app.api.deps import Runtime
from app.api.schemas import StrictInput
from app.config.settings import PROJECT_ROOT
from app.security.credentials import KNOWN_SECRETS, env_name, info, secret_store

router = APIRouter(prefix="/settings/api-keys", tags=["settings"])

# name (KNOWN_SECRETS) -> (provider, label, hint). Only these can be set from Settings.
POKEMON_KEYS: dict[str, tuple[str, str, str]] = {
    "pokemontcg_api_key": ("Pokémon TCG API", "API key", "Optional: free key from dev.pokemontcg.io raises the rate limit."),
    "pricecharting_api_key": ("PriceCharting", "API key", "Paid subscription with API access."),
    "ebay_client_id": ("eBay", "App ID (Client ID)", "eBay developer account › Application keys (Production)."),
    "ebay_client_secret": ("eBay", "Cert ID (Client Secret)", "Same eBay app as the Client ID (Production keyset). Server-side only: never sent to the browser."),
    "ebay_dev_id": ("eBay", "Dev ID", "eBay developer account › Application keys. Identifies the account (not needed for OAuth)."),
    "psa_api_token": ("PSA", "API access token", "PSA account › Public API (needs PSA's approval). Certs + population by spec."),
    "gemrate_api_key": ("GemRate", "API key", "From the GemRate team (population data)."),
    "scrydex_api_key": ("Scrydex", "API key", "scrydex.com › Account Hub › API key (paid plan). Graded prices + PSA population."),
    "scrydex_team_id": ("Scrydex", "Team ID", "scrydex.com › Account Hub › Team. Sent with every request."),
    "anthropic_api_key": ("AI condition assistant (Anthropic)", "API key", "console.anthropic.com › API keys. Reviews raw-card photos for the "
                          "Pokémon Portfolio's ✨ Estimate Condition (vision model, daily limits). Server-side only."),
    "newsdesk_vision_api_key": ("News Desk image check (Anthropic vision)", "API key", "console.anthropic.com › API keys. Used ONLY to check that a "
                                "news photo shows the story's subject; captions never use it. Also needs NEWSDESK_VISION_ENABLED=true. Server-side only."),
    "resend_api_key": ("Email sign-in (Resend)", "API key", "resend.com › API Keys › Create (Full access, so Price Chat can add and verify the "
                       "pricechat.live domain). Sends the 6-digit sign-in codes. Server-side only."),
    "tiktok_client_key": ("TikTok (Brand Manager)", "Client key", "developers.tiktok.com › your app › App details. Login Kit + Content Posting API. "
                          "Redirect URI: https://pricechat.live/api/brand-manager/tiktok/callback"),
    "tiktok_client_secret": ("TikTok (Brand Manager)", "Client secret", "Same TikTok app as the Client key. Server-side only: never sent to the browser."),
}
assert set(POKEMON_KEYS) <= set(KNOWN_SECRETS)
# provider name -> the Pokémon data source key its "Test connection" button checks (POST /pokemon/sources/{key}/test)
TEST_KEYS = {"Pokémon TCG API": "pokemontcg", "PriceCharting": "pricecharting", "eBay": "ebay", "PSA": "psa", "GemRate": "gemrate", "Scrydex": "scrydex"}


ENV_FILE = PROJECT_ROOT / ".env"
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def _env_line(name: str) -> re.Pattern[str]:
    """`NAME=...`, also commented out (`# NAME=...`), so an example line in .env is filled in place."""
    return re.compile(rf"^\s*(#\s*)?{name}\s*=")


def _env_names() -> set[str]:
    """Names set (uncommented, non-empty) in the .env file right now. Values are never read out."""
    if not ENV_FILE.exists():
        return set()
    names = set()
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\S.*)?$", line)
        if m and m.group(2) and not m.group(2).startswith("#"):
            names.add(m.group(1))
    return names


def _keys(runtime: Runtime) -> dict[str, Any]:
    in_file = _env_names()
    out = []
    for name, (provider, label, hint) in POKEMON_KEYS.items():
        i = info(name, runtime.settings)
        out.append({
            "name": name, "env_name": env_name(name), "provider": provider, "label": label, "hint": hint, "test_key": TEST_KEYS.get(provider),
            "configured": i.configured, "source": i.source, "saved_at": i.saved_at, "in_env_file": env_name(name) in in_file or name.upper() in in_file,
        })
    return {"keys": out, "env_file": str(ENV_FILE), "env_writable": not runtime.settings.is_production}


class KeyValue(StrictInput):
    value: str = Field(min_length=1, max_length=4096)


def _check_value(name: str, value: str, runtime: Runtime) -> None:
    """Catch the common eBay mix-up before it is saved: the Cert ID is not the App ID (it starts with PRD- or SBX-)."""
    from app.security.credentials import resolve

    if name == "ebay_client_secret":
        v = value.strip()
        if v == (resolve("ebay_client_id", runtime.settings) or "").strip() or "-PRD-" in v or "-SBX-" in v:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "That is the eBay App ID (Client ID), not the Cert ID. Copy the Cert ID (Client Secret) from the same "
                                "Production keyset: it starts with PRD-.")


def _known(name: str) -> str:
    if name not in POKEMON_KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown API key")
    return name


@router.get("")
def list_keys(runtime: Runtime) -> dict[str, Any]:
    return _keys(runtime)


class EnvEntry(StrictInput):
    name: str = Field(min_length=2, max_length=64)
    value: str = Field(min_length=1, max_length=4096)


@router.put("/env")
def write_env(payload: EnvEntry, runtime: Runtime) -> dict[str, Any]:
    """Add or replace NAME=value in the project's .env (local only). Read by the backend on its next start."""
    if runtime.settings.is_production:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "In production .env isn't used: set variables on the host (Railway).")
    name, value = payload.name.strip().upper(), payload.value.strip()
    if not _ENV_NAME.match(name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name: capital letters, digits and _ only (e.g. PRICECHARTING_API_KEY)")
    if not value or any(c in value for c in "\r\n\0"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The value must be one line and not empty")
    if any(c in value for c in " #'\"\\"):
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    line = f"{name}={value}"

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pattern = _env_line(name)
    hits = [i for i, existing in enumerate(lines) if pattern.match(existing)]
    active = [i for i in hits if not lines[i].lstrip().startswith("#")]
    if active or hits:
        lines[(active or hits)[0]] = line  # replace the set value, else fill in the commented example
        for i in reversed(active[1:]):  # a later duplicate would win: drop it
            del lines[i]
    else:
        lines.append(line)
    tmp = ENV_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_FILE)
    return {**_keys(runtime), "written": name, "restart_needed": True}


@router.put("/{name}")
def save_key(name: str, payload: KeyValue, runtime: Runtime) -> dict[str, Any]:
    _check_value(_known(name), payload.value, runtime)
    value = payload.value.strip()
    if not value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The key is empty")
    secret_store(runtime.settings).set(_known(name), value)
    return _keys(runtime)


@router.delete("/{name}")
def remove_key(name: str, runtime: Runtime) -> dict[str, Any]:
    """Forget the dashboard-saved value (a .env value, if any, applies again)."""
    secret_store(runtime.settings).delete(_known(name))
    return _keys(runtime)
