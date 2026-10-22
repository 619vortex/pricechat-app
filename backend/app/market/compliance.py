"""Launch gates and jurisdiction rules. Live settlement is impossible unless EVERY gate passes; otherwise the marketplace
runs as a simulation (sandbox inventory, simulated payments) whatever MARKET_MODE says."""

from typing import Any

from app.config.settings import Settings

SANDBOX_VAULT = "sandbox-vault"


def vault_partners(settings: Settings) -> list[str]:
    return [p.strip() for p in settings.market_vault_partners.split(",") if p.strip()]


def blocked_countries(settings: Settings) -> set[str]:
    return {c.strip().upper() for c in settings.market_blocked_countries.split(",") if c.strip()}


def gates(settings: Settings) -> list[dict[str, Any]]:
    """Every condition for real-money trading, with its current state (shown in the admin panel)."""
    return [
        {"gate": "owner_approval", "ok": settings.market_live_approved, "detail": "MARKET_LIVE_APPROVED (explicit owner approval)"},
        {"gate": "payment_partner", "ok": bool(settings.market_payment_partner), "detail": "Regulated USDT payment / escrow partner"},
        {"gate": "vault_partner", "ok": bool(vault_partners(settings)), "detail": "Contracted, insured vault / fulfilment partner"},
        {"gate": "kyc_provider", "ok": bool(settings.market_kyc_provider), "detail": "KYC / KYB and sanctions screening provider"},
        {"gate": "durable_database", "ok": settings.database_url.startswith(("postgresql", "postgres")),
         "detail": "PostgreSQL with point-in-time backups (SQLite is simulation-only)"},
        {"gate": "jurisdictions", "ok": bool(blocked_countries(settings)), "detail": "Jurisdiction block-list loaded (legal review required)"},
        {"gate": "legal_review", "ok": settings.market_live_approved, "detail": "UAE + destination-market legal/compliance sign-off (recorded with owner approval)"},
    ]


def mode(settings: Settings) -> str:
    """off | simulation | live. "live" only when every gate passes."""
    if settings.market_mode == "off":
        return "off"
    if settings.market_mode == "live" and all(g["ok"] for g in gates(settings)):
        return "live"
    return "simulation"


def country_allowed(settings: Settings, country: str | None) -> bool:
    return not country or country.upper() not in blocked_countries(settings)
