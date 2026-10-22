"""Settings from environment variables, validated once at startup (fail fast on anything malformed).

Nothing secret has a default. Read-only operation (Phases 1-3) needs no secret at all; venue credentials only unlock
account data (fees, positions, balances). Execution additionally needs LIVE_TRADING=true AND LIVE_CONFIRM_TOKEN, both
absent by default; even then Phase 1 adapters refuse to place orders."""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FUNDARB_", extra="ignore", frozen=True)

    # endpoints (public base URLs; override for testnets)
    hyperliquid_url: str = "https://api.hyperliquid.xyz"
    hyperliquid_ws_url: str = "wss://api.hyperliquid.xyz/ws"
    lighter_url: str = "https://mainnet.zklighter.elliot.ai"
    orderly_url: str = "https://api.orderly.org"
    backpack_url: str = "https://api.backpack.exchange"

    # account identifiers (public, but not hard-coded): needed to read YOUR fee tier / positions
    hl_account_address: str | None = None
    lighter_account_index: int | None = Field(None, ge=0)
    orderly_account_id: str | None = None

    # credentials (Phase 4 / authenticated reads)
    orderly_key: SecretStr | None = None
    orderly_secret: SecretStr | None = None
    backpack_api_key: SecretStr | None = None
    backpack_api_secret: SecretStr | None = None

    # execution gate: BOTH must be set, and neither has a default. Read without the FUNDARB_ prefix.
    live_trading: bool = Field(False, validation_alias="LIVE_TRADING")
    live_confirm_token: SecretStr | None = Field(None, validation_alias="LIVE_CONFIRM_TOKEN")

    # data
    sqlite_path: Path = Path("fundarb_quotes.sqlite3")
    history_days: float = Field(7.0, gt=0)
    book_depth: int = Field(100, ge=5, le=1000)
    http_timeout: float = Field(15.0, gt=0)
    fee_refresh_seconds: int = Field(3600, ge=60)
    interval_refresh_seconds: int = Field(6 * 3600, ge=60)

    @field_validator("hl_account_address")
    @classmethod
    def _evm(cls, v: str | None) -> str | None:
        if v is not None and not EVM_ADDRESS.match(v):
            raise ValueError("must be a 0x-prefixed 40-hex-character address")
        return v.lower() if v else v

    @field_validator("orderly_account_id")
    @classmethod
    def _hex_id(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"0x[0-9a-fA-F]{64}", v):
            raise ValueError("must be a 0x-prefixed 64-hex-character Orderly account id")
        return v

    @model_validator(mode="after")
    def _pairs(self) -> "Settings":
        for a, b in (("orderly_key", "orderly_secret"), ("backpack_api_key", "backpack_api_secret")):
            if (getattr(self, a) is None) != (getattr(self, b) is None):
                raise ValueError(f"FUNDARB_{a.upper()} and FUNDARB_{b.upper()} must be set together")
        if self.live_trading and not (self.live_confirm_token and self.live_confirm_token.get_secret_value().strip()):
            raise ValueError("LIVE_TRADING=true requires LIVE_CONFIRM_TOKEN as well")
        return self

    @property
    def live_armed(self) -> bool:
        """Both execution switches are set. (Phase 1-3 code still never places an order.)"""
        return bool(self.live_trading and self.live_confirm_token and self.live_confirm_token.get_secret_value().strip())


@lru_cache
def load() -> Settings:
    """The process's settings; raises pydantic.ValidationError at startup if anything is malformed."""
    return Settings()
