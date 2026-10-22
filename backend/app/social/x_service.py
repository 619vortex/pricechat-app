"""X connection management: credentials, status, capability detection."""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.connectors.x.client import XClient
from app.connectors.x.connector import XRuntime
from app.connectors.x.errors import (
    XAuthError,
    XBudgetExceededError,
    XCreditsError,
    XError,
    XForbiddenError,
    XNotConfiguredError,
    XRateLimitError,
)
from app.connectors.x.normalize import profile as to_profile
from app.db.base import utcnow
from app.models.enums import ActivityLevel, ConnectionStatus, Platform
from app.models.social import SocialAccount
from app.security import credentials
from app.services.activity import ActivityLogger
from app.social.budget import PlatformBudget
from app.social.store import upsert_profile

logger = logging.getLogger(__name__)

X_SECRET_NAMES = ("x_bearer_token", "x_api_key", "x_api_secret", "x_access_token", "x_access_token_secret")

CAPABILITIES = {
    "app_auth": "App authentication (X accepted the credentials)",
    "public_read": "Public X read (look up a public post)",
    "search_recent": "Recent search: public posts from the last 7 days (monitoring topics)",
    "account_lookup": "Public account lookup (needed for the watchlist)",
    "own_account": "Own account: needs user-context credentials (Access Token + Secret)",
    "own_posts": "Read your own posts and their public metrics",
    "mentions": "Read posts that mention your account",
    "private_metrics": "Impressions/clicks of your own posts (user-context credentials, last ~30 days)",
    "write_post": "Write: post",
    "write_reply": "Write: reply",
    "write_like": "Write: like",
    "write_repost": "Write: repost",
    "write_follow": "Write: follow",
    "write_actions": "Write actions (every one needs your approval)",
}

# A long-standing public post (X's own first post, id 20) used only to check that public reads work.
PUBLIC_PROBE_POST_ID = "20"
PUBLIC_PROBE_ACCOUNT = "XDevelopers"


def make_x_client(db: Session, settings: Settings, runtime: XRuntime) -> XClient:
    budget = PlatformBudget(db, settings)
    return XClient(
        credentials.x_credentials(settings), settings.x_api_base, transport=runtime.transport,  # type: ignore[arg-type]
        on_usage=budget.record, budget_check=budget.check, write_cost_estimate=settings.x_write_cost_estimate_usd,
    )


def save_credentials(db: Session, settings: Settings, runtime: XRuntime, values: dict[str, str | None]) -> list[str]:
    store = credentials.secret_store(settings)
    saved = []
    for name in ("bearer_token", "api_key", "api_secret", "access_token", "access_token_secret"):
        value = values.get(name)
        if value:
            store.set(f"x_{name}", value)
            saved.append(name)
    if values.get("username"):
        state = get_connector_state(db, Platform.X)
        state.account_username = str(values["username"]).lstrip("@")
    runtime.configured = credentials.x_credentials(settings).configured
    if runtime.configured and runtime.status == "not_configured":
        runtime.status = "disconnected"
    db.commit()
    if saved:
        ActivityLogger(db).log(
            "x_credentials_saved", f"X credentials saved (encrypted on this computer): {', '.join(n.replace('_', ' ') for n in saved)}",
            level=ActivityLevel.AUDIT, actor="dashboard",
        )
    return saved


def remove_credentials(db: Session, settings: Settings, runtime: XRuntime) -> None:
    store = credentials.secret_store(settings)
    for name in X_SECRET_NAMES:
        store.delete(name)
    runtime.configured = credentials.x_credentials(settings).configured  # .env values may remain
    state = get_connector_state(db, Platform.X)
    state.status = "not_configured" if not runtime.configured else "disconnected"
    runtime.status = state.status
    account = db.scalar(select(SocialAccount).where(SocialAccount.platform == Platform.X))
    if account is not None:
        account.status = ConnectionStatus.NOT_CONNECTED
    db.commit()
    ActivityLogger(db).log("x_disconnected", "X disconnected: saved credentials removed", level=ActivityLevel.AUDIT, actor="dashboard")


@dataclass(slots=True)
class ProbeResult:
    status: str  # available | unavailable | not_configured | rate_limited | budget | error | disabled
    detail: str


async def _probe(fn) -> ProbeResult:  # type: ignore[no-untyped-def]
    try:
        detail = await fn()
        return ProbeResult("available", detail or "OK")
    except XNotConfiguredError as exc:
        return ProbeResult("not_configured", str(exc))
    except XForbiddenError as exc:
        return ProbeResult("unavailable", str(exc))
    except XBudgetExceededError as exc:
        return ProbeResult("budget", str(exc))
    except XCreditsError as exc:
        return ProbeResult("no_credit", str(exc))
    except XRateLimitError as exc:
        return ProbeResult("rate_limited", str(exc))
    except XAuthError:
        raise
    except XError as exc:
        return ProbeResult("error", str(exc))


WRITE_ACTIONS = ("post", "reply", "like", "repost", "follow")
READ_ONLY_FIX = ("The Access Token is read-only. In the X Developer Console set the app permissions to "
                 "'Read and write', then regenerate the Access Token and Secret and paste them again.")


def write_capabilities(user_context: bool, own_account: str, access_level: str | None) -> dict[str, ProbeResult]:
    """Write capability per action, from what X actually reported. Nothing is written to find out."""
    if not user_context:
        r = ProbeResult("not_configured", "Requires user-context credentials (Access Token and Access Token Secret)")
    elif own_account != "available":
        r = ProbeResult("error", "Your account could not be read with these credentials, so writing will not work either")
    elif access_level == "read":
        r = ProbeResult("unavailable", READ_ONLY_FIX)
    elif access_level and "write" in access_level:
        r = ProbeResult("available", f"X reports access level '{access_level}'. Confirmed for real on the first approved action.")
    else:
        r = ProbeResult("unknown", "X did not report the access level; it is confirmed on the first approved action (try it in dry run first)")
    out = {f"write_{a}": r for a in WRITE_ACTIONS}
    summary = ProbeResult("approval", "Every write action needs your approval; automatic mode is locked") if r.status in ("available", "unknown") else r
    out["write_actions"] = summary
    return out


async def test_connection(db: Session, settings: Settings, runtime: XRuntime) -> dict[str, Any]:
    """Probe what the configured access can actually do. Costs a few cents of X credit."""
    creds = credentials.x_credentials(settings)
    if not creds.configured:
        raise XNotConfiguredError("X is not configured: add a Bearer token (and optionally account keys) first.")
    state = get_connector_state(db, Platform.X)
    client = make_x_client(db, settings, runtime)
    account: dict[str, Any] = {}
    results: dict[str, ProbeResult] = {}
    try:
        async def own_account() -> str:
            me = await client.me()
            account.update(me)
            return f"@{me.get('username')}"

        async def public_read() -> str:
            body = await client.public_posts([PUBLIC_PROBE_POST_ID])
            if not body.get("data"):
                raise XError("X returned no data for a public post")
            return "public posts readable"

        async def lookup() -> str:
            name = account.get("username") or state.account_username or (settings.x_account_username or "").lstrip("@") or None
            user = await client.user_by_username(name or PUBLIC_PROBE_ACCOUNT)
            if name and not account:
                account.update(user)
            return f"@{user.get('username')}" if name else f"public lookup works (@{user.get('username')}); add your username to read your mentions"

        results["public_read"] = await _probe(public_read)
        results["own_account"] = await _probe(own_account) if creds.user_context else ProbeResult(
            "not_configured", "Requires user-context credentials (Consumer Key/Secret + Access Token/Secret)")
        results["account_lookup"] = await _probe(lookup) if creds.app_only or account else ProbeResult("not_configured", "Needs a Bearer token")

        account_id = str(account["id"]) if account.get("id") else None
        if account_id:
            async def own_posts() -> str:
                body = await client.user_posts(account_id, 5, own=True)
                return f"{(body.get('meta') or {}).get('result_count', 0)} recent posts readable"

            async def mentions() -> str:
                body = await client.mentions(account_id, 5)
                return f"{(body.get('meta') or {}).get('result_count', 0)} recent mentions readable"

            results["own_posts"] = await _probe(own_posts)
            results["mentions"] = await _probe(mentions)
        else:
            results["own_posts"] = results["mentions"] = ProbeResult("not_configured", "Needs your account (username or account keys)")

        async def search() -> str:
            body = await client.search_recent(f"from:{account.get('username')}" if account.get("username") else "price chat", 10)
            return f"search works ({(body.get('meta') or {}).get('result_count', 0)} results)"

        results["search_recent"] = await _probe(search)

        if creds.user_context and account_id and results["own_posts"].status == "available":
            async def private_metrics() -> str:
                body = await client.user_posts(account_id, 5, own=True)
                ids = [t["id"] for t in body.get("data") or []][:1]
                if not ids:
                    return "no recent posts to check yet"
                await client.own_post_metrics(ids)
                return "impressions and clicks available"

            results["private_metrics"] = await _probe(private_metrics)
        else:
            results["private_metrics"] = ProbeResult("not_configured", "Needs account keys and your own recent posts")
        for k, r in write_capabilities(creds.user_context, results["own_account"].status, client.access_level).items():
            results[k] = r
        # A 401 anywhere aborts the test (XAuthError), so reaching here means X accepted the credentials,
        # unless no request could be made at all (credit / budget / rate limit before any answer).
        answered = any(r.status in ("available", "unavailable") for r in results.values())
        results["app_auth"] = ProbeResult("available", "X accepted the credentials") if answered else ProbeResult(
            "unknown", "No request was answered (see the other rows), so authentication could not be confirmed")
    except XAuthError as exc:
        state.status, state.last_error, state.last_error_at = "error", str(exc), utcnow()
        runtime.status, runtime.detail = "error", str(exc)
        db.commit()
        ActivityLogger(db).log("x_error", f"X connection problem: {exc}", level=ActivityLevel.ERROR)
        raise
    finally:
        state.rate_limits = {**(state.rate_limits or {}), **client.rate_limits}
        await client.aclose()

    caps = {k: {"status": results[k].status, "detail": results[k].detail, "checked_at": utcnow().isoformat(), "label": label}
            for k, label in CAPABILITIES.items() if k in results}
    state.capabilities = caps
    state.auth_modes = [m for m, on in (("app_only", creds.app_only), ("user_context", creds.user_context)) if on]
    working = any(r.status == "available" for k, r in results.items() if not k.startswith("write_"))
    if account.get("id"):
        state.account_external_id = str(account["id"])
        state.account_username = account.get("username")
        state.account_name = account.get("name")
        me = upsert_profile(db, to_profile(account), is_self=True)
        from app.analytics.snapshots import record_x_account

        record_x_account(db, account, "x_response")  # follower history for the growth dashboard (no extra request)
        acct = db.scalar(select(SocialAccount).where(SocialAccount.platform == Platform.X))
        if acct is None:
            acct = SocialAccount(platform=Platform.X, handle=me.username, display_name=me.display_name)
            db.add(acct)
        acct.handle, acct.status = me.username, ConnectionStatus.CONNECTED if working else ConnectionStatus.ERROR
        acct.extra = {**(acct.extra or {}), "external_id": me.external_id}
    state.status = "connected" if working else "error"
    if working:
        state.last_success_at, state.last_error = utcnow(), None
    else:
        statuses = {r.status for k, r in results.items() if not k.startswith("write_")}
        if "no_credit" in statuses:
            reason = next(r.detail for r in results.values() if r.status == "no_credit")
        elif statuses <= {"unavailable", "not_configured"}:
            reason = "Your X access level doesn't include any read feature Price Chat uses (or your username is missing)"
        else:
            reason = "No read capability is available with these credentials"
        state.last_error, state.last_error_at = reason, utcnow()
    runtime.status, runtime.username = state.status, state.account_username
    runtime.capabilities = {k: v["status"] for k, v in caps.items()}
    db.commit()
    ActivityLogger(db).log(
        "x_connection_tested",
        f"X connection test: {sum(r.status == 'available' for r in results.values())} capabilities available"
        + (f" as @{state.account_username}" if state.account_username else ""),
        actor="dashboard", details={k: v["status"] for k, v in caps.items()},
    )
    return caps


def sync_runtime_from_db(db: Session, settings: Settings, runtime: XRuntime) -> None:
    runtime.configured = credentials.x_credentials(settings).configured
    state = get_connector_state(db, Platform.X)
    if not runtime.configured:
        runtime.status = "not_configured"
    elif state.paused:
        runtime.status = "paused"
    else:
        runtime.status = state.status if state.status != "not_configured" else "disconnected"
    runtime.username = state.account_username
    runtime.detail = state.last_error or ""
    runtime.capabilities = {k: v.get("status", "") for k, v in (state.capabilities or {}).items()}
