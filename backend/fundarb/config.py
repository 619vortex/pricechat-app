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
    # universe discovery (Module 1): full Hyperliquid perp universe, native + every HIP-3 builder dex
    universe_refresh_seconds: int = Field(300, ge=30)
    universe_schema_path: Path = Path("fundarb_universe_schema.json")  # the field-name hash a human last confirmed
    universe_overrides_path: Path = Path(__file__).with_name("classification_overrides.json")  # operator classification decisions
    universe_db_path: Path = Path("fundarb_universe.sqlite3")  # markets, quotes, gate runs, pairs, scan results

    # Module 2 gates (every one overridable: FUNDARB_MIN_VOLUME_24H_USD, ...)
    min_volume_24h_usd: float = Field(5_000_000, ge=0)
    min_oi_usd: float = Field(2_000_000, ge=0)
    max_spread_bps: float = Field(10.0, gt=0)
    min_depth_usd: float = Field(250_000, ge=0)
    max_oi_cap_share: float = Field(0.8, gt=0, le=1)
    oracle_min_samples: int = Field(30, ge=10)  # basis samples before the gate ENFORCES each market's own 99th percentile
    oracle_backstop_bps: float = Field(150.0, gt=0)  # |mark - oracle| above this is rejected at all times
    oracle_persistent_bps: float = Field(50.0, gt=0)  # |basis| above this on every recent sample = a thin or lagging oracle
    oracle_stable_std_bps: float = Field(10.0, gt=0)  # ...and this steady (std of those samples)
    oracle_recent_samples: int = Field(20, ge=3)
    book_concurrency: int = Field(4, ge=1, le=16)

    # fees a venue only shows a signed-in account: unknown (net APR not computed) unless the operator states an assumption,
    # e.g. FUNDARB_ASSUMED_TAKER_BPS='{"orderly": 5, "backpack": 5}'. Never defaulted.
    assumed_taker_bps: dict[str, float] = Field(default_factory=dict)

    # capital model (fundarb.capital)
    capital_buffer: float = Field(0.25, ge=0)  # idle collateral on top of initial margin, share of that margin

    # Module 4 equity bucket (fundarb.sessions / events / oracle_study / equity)
    equity_tz: str = "America/New_York"
    equity_rth_open: str = Field("09:30", pattern=r"^\d{2}:\d{2}$")
    equity_rth_close: str = Field("16:00", pattern=r"^\d{2}:\d{2}$")
    equity_ext_open: str = Field("04:00", pattern=r"^\d{2}:\d{2}$")
    equity_ext_close: str = Field("20:00", pattern=r"^\d{2}:\d{2}$")
    equity_operator_path: Path = Path(__file__).with_name("equity_operator.json")  # event calendar + published index weights
    oracle_policy_path: Path = Path(__file__).with_name("equity_oracle_policy.json")  # written by `fundarb oracle-study`
    oracle_tracks_beta: float = Field(0.05, ge=0)  # |slope of hourly premium change on the perp's return| at or below: oracle tracks
    oracle_frozen_beta: float = Field(0.8, gt=0)  # at or above: the oracle is not moving (premium = the perp's own move)
    oracle_stale_stop_mult: float = Field(1.5, ge=1)  # spread-stop widening in a session the study found stale
    # Hyperliquid caps funding at 4%/hour for every asset (Module 4 rule 6; Hyperliquid docs "Funding"). Not served by the
    # API as a field, so it is configuration, not a constant buried in code.
    funding_cap_hourly: float = Field(0.04, gt=0)
    funding_cap_share: float = Field(0.9, gt=0, le=1)  # |hourly funding| at or above this share of the cap = "at cap"
    equity_event_buffer_hours: float = Field(24.0, ge=0)  # block entries this long before an event starts
    equity_risk_budget_usd: float = Field(1000.0, gt=0)  # loss at the spread stop that sizes one index trade

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
