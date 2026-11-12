"""Client addresses for rate limits (app/security/client_ip.py): a direct caller can't pick its own bucket; the website's
Vercel rewrite is believed only with the shared secret."""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from app.config.settings import Settings, get_settings
from app.security import client_ip

SECRET = "s3cret-0123456789abcdef-0123456789"


def _settings(secret: str | None = SECRET, hops: int = 1) -> Settings:
    return get_settings().model_copy(update={"proxy_shared_secret": SecretStr(secret) if secret is not None else None,
                                             "trusted_proxy_hops": hops})


def _req(headers: list[tuple[str, str]] | dict[str, str] | None = None, peer: str | None = "10.1.2.3") -> Request:
    items = list(headers.items()) if isinstance(headers, dict) else list(headers or [])
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(k.lower().encode(), v.encode()) for k, v in items],
                    "client": (peer, 1234) if peer else None, "query_string": b""})


def _vercel(visitor: str, edge: str = SECRET, vercel_egress: str = "76.76.21.9") -> dict[str, str]:
    """What arrives from the website: Vercel's visitor headers + our secret, then Railway's edge appends Vercel's address."""
    return {"x-forwarded-for": f"{visitor}, {vercel_egress}", "x-real-ip": visitor, "x-vercel-forwarded-for": visitor,
            "x-vercel-id": "fra1::abcd", "x-pc-edge": edge}


# --- direct callers ----------------------------------------------------------------------------------------------------


def test_forged_left_most_entries_do_not_change_the_key_for_direct_calls() -> None:
    s = _settings()
    keys = {client_ip.client_key(_req({"x-forwarded-for": f"198.51.100.{i}, 203.0.113.7"}), s) for i in range(20)}
    assert keys == {"203.0.113.7"}  # always the entry Railway's edge appended
    assert client_ip.client_key(_req({"x-forwarded-for": "203.0.113.7"}), s) == "203.0.113.7"
    # forged Vercel headers without the secret are ignored too
    forged = {"x-forwarded-for": "1.1.1.1, 203.0.113.7", "x-vercel-forwarded-for": "1.1.1.1", "x-real-ip": "1.1.1.1"}
    assert client_ip.client_ip(_req(forged), s) == "203.0.113.7"


def test_repeated_forwarded_for_lines_use_the_last_entry() -> None:
    r = _req([("x-forwarded-for", "9.9.9.9"), ("x-forwarded-for", "8.8.8.8, 203.0.113.8")])
    assert client_ip.client_ip(r, _settings()) == "203.0.113.8"


def test_an_unparsable_edge_entry_never_falls_back_to_the_callers_entries() -> None:
    assert client_ip.client_ip(_req({"x-forwarded-for": "1.2.3.4, garbage"}), _settings()) == client_ip.UNKNOWN
    assert client_ip.client_ip(_req({"x-forwarded-for": ""}), _settings()) == client_ip.UNKNOWN


def test_trusted_hops_counts_in_from_the_right() -> None:
    r = _req({"x-forwarded-for": "6.6.6.6, 203.0.113.9, 100.64.0.2"})
    assert client_ip.client_ip(r, _settings(hops=2)) == "203.0.113.9"
    assert client_ip.client_ip(_req({"x-forwarded-for": "203.0.113.9"}), _settings(hops=2)) == client_ip.UNKNOWN


def test_missing_headers_use_the_socket_peer() -> None:
    assert client_ip.client_ip(_req(), _settings()) == "10.1.2.3"
    assert client_ip.client_ip(_req(peer=None), _settings()) == client_ip.UNKNOWN
    assert client_ip.client_ip(_req(peer="testclient"), _settings()) == "testclient"
    # the secret alone (no address headers) still ends at the peer
    assert client_ip.client_ip(_req({"x-pc-edge": SECRET}), _settings()) == "10.1.2.3"


# --- through the website (Vercel) -----------------------------------------------------------------------------------------


def test_a_forged_x_vercel_forwarded_for_through_the_website_is_ignored() -> None:
    # Production 2026-11-12: Vercel passes a caller's own X-Vercel-Forwarded-For through (with the valid x-pc-edge
    # added), while it overwrites X-Forwarded-For. Only the left-most X-Forwarded-For entry may name the visitor.
    h = _vercel("192.0.2.44")
    h["x-vercel-forwarded-for"] = "1.2.3.4"
    assert client_ip.client_ip(_req(h), _settings()) == "192.0.2.44"


def test_vercel_path_with_the_right_secret_uses_the_reported_visitor() -> None:
    s = _settings()
    assert client_ip.client_ip(_req(_vercel("192.0.2.44")), s) == "192.0.2.44"
    # without x-vercel-forwarded-for: the left-most entry, which Vercel wrote
    h = _vercel("192.0.2.45")
    del h["x-vercel-forwarded-for"]
    assert client_ip.client_ip(_req(h), s) == "192.0.2.45"
    # Vercel's header unusable and no XFF: Railway-edge rules (here: the peer)
    assert client_ip.client_ip(_req({"x-pc-edge": SECRET, "x-vercel-forwarded-for": "nope"}), s) == "10.1.2.3"


def test_wrong_missing_or_unconfigured_secret_falls_back_to_the_edge_address() -> None:
    for edge in ("wrong-secret-0123456789abcdef", SECRET + "x", SECRET[:-1], ""):
        assert client_ip.client_ip(_req(_vercel("192.0.2.44", edge=edge)), _settings()) == "76.76.21.9", edge
    # Vercel sends the literal reference when its env var is missing: never a match
    assert client_ip.client_ip(_req(_vercel("192.0.2.44", edge="$PROXY_SHARED_SECRET")), _settings()) == "76.76.21.9"


def test_without_a_valid_secret_configured_the_legacy_left_most_rule_stays() -> None:
    """Unset (or unusable) PROXY_SHARED_SECRET: today's behaviour, so website visitors aren't all keyed on Vercel's few
    egress addresses on deploy day. Forgeable, which startup_check warns about."""
    for s in (_settings(secret=None), _settings(secret=""), _settings(secret="short"),
              _settings(secret="$PROXY_SHARED_SECRET")):
        assert not client_ip.secrets_of(s)
        assert client_ip.client_ip(_req(_vercel("192.0.2.44")), s) == "192.0.2.44"
        assert client_ip.client_ip(_req(_vercel("192.0.2.44", edge="$PROXY_SHARED_SECRET")), s) == "192.0.2.44"
        assert client_ip.client_ip(_req({"x-forwarded-for": "198.51.100.1, 203.0.113.7"}), s) == "198.51.100.1"
        assert client_ip.client_key(_req({"x-forwarded-for": "2001:db8:0:1::5, 203.0.113.7"}), s) == "2001:db8:0:1::/64"
        # an unparsable left-most entry: the edge's entry rather than "unknown"
        assert client_ip.client_ip(_req({"x-forwarded-for": "junk, 203.0.113.7"}), s) == "203.0.113.7"
        assert client_ip.client_ip(_req(), s) == "10.1.2.3"


def test_secret_rotation_accepts_old_and_new() -> None:
    old, new = "old-" + "a" * 30, "new-" + "b" * 30
    s = _settings(secret=f"{old}, {new}")
    assert client_ip.client_ip(_req(_vercel("192.0.2.1", edge=old)), s) == "192.0.2.1"
    assert client_ip.client_ip(_req(_vercel("192.0.2.1", edge=new)), s) == "192.0.2.1"
    assert client_ip.client_ip(_req(_vercel("192.0.2.1", edge=f"{old}, {new}")), s) == "76.76.21.9"


# --- IPv6 and formats -------------------------------------------------------------------------------------------------


def test_ipv6_is_canonical_and_bucketed_by_64() -> None:
    s = _settings()
    a = client_ip.client_ip(_req({"x-forwarded-for": "1.1.1.1, 2001:DB8:0:1::abcd"}), s)
    assert a == "2001:db8:0:1::abcd"
    k1 = client_ip.client_key(_req({"x-forwarded-for": "2001:db8:0:1::abcd"}), s)
    k2 = client_ip.client_key(_req({"x-forwarded-for": "2001:db8:0:1:ffff:1:2:3"}), s)
    k3 = client_ip.client_key(_req({"x-forwarded-for": "2001:db8:0:2::1"}), s)
    assert k1 == k2 == "2001:db8:0:1::/64" and k3 != k1
    assert client_ip.client_ip(_req(_vercel("2001:db8::7", vercel_egress="2600:1f18::1")), s) == "2001:db8::7"
    assert client_ip.client_ip(_req({"x-forwarded-for": "[2001:db8::9]:443"}), s) == "2001:db8::9"
    assert client_ip.client_ip(_req({"x-forwarded-for": "203.0.113.5:5555"}), s) == "203.0.113.5"
    assert client_ip.client_ip(_req({"x-forwarded-for": "::ffff:203.0.113.6"}), s) == "203.0.113.6"
    assert client_ip.client_key(_req({"x-forwarded-for": "203.0.113.6"}), s) == "203.0.113.6"


def test_parse_ip_rejects_junk() -> None:
    for junk in ("unknown", "_hidden", "1.2.3", "300.1.1.1", "", "x" * 100, "[::1"):
        assert client_ip.parse_ip(junk) is None, junk


# --- wired into the app's limiters ------------------------------------------------------------------------------------


def test_health_limit_ignores_forged_left_most_entries(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_ip, "_settings", lambda request: _settings())  # strict once the secret is configured
    codes = [client.get("/api/health/public", headers={"x-forwarded-for": f"198.51.100.{i}, 203.0.113.70"}).status_code
             for i in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429  # a fresh forged address each time, still one bucket
    assert client.get("/api/health/public", headers={"x-forwarded-for": "203.0.113.71"}).status_code == 200


def test_health_limit_per_visitor_through_vercel(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_ip, "_settings", lambda request: _settings())
    for _ in range(10):
        assert client.get("/api/health/public", headers=_vercel("192.0.2.80")).status_code == 200
    assert client.get("/api/health/public", headers=_vercel("192.0.2.80")).status_code == 429
    # another visitor behind the same Vercel egress address has a bucket of its own
    assert client.get("/api/health/public", headers=_vercel("192.0.2.81")).status_code == 200
    # a direct caller forging Vercel's headers with a wrong secret lands on its own edge address, not the victim's
    assert client.get("/api/health/public", headers=_vercel("192.0.2.81", edge="nope", vercel_egress="203.0.113.90")).status_code == 200


def test_startup_check_warns_in_production(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    prod = _settings(secret=None).model_copy(update={"environment": "production"})
    with caplog.at_level(logging.WARNING, logger="app.security.client_ip"):
        client_ip.startup_check(prod)
        client_ip.startup_check(prod.model_copy(update={"proxy_shared_secret": SecretStr("short")}))
        client_ip.startup_check(prod.model_copy(update={"proxy_shared_secret": SecretStr(SECRET)}))
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2 and "not set" in msgs[0] and "too short" in msgs[1]


def test_notes_once_whether_website_traffic_carries_the_secret(caplog: pytest.LogCaptureFixture,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_ip, "_noted", set())
    with caplog.at_level(logging.INFO, logger="app.security.client_ip"):
        for _ in range(3):
            client_ip.client_ip(_req(_vercel("192.0.2.1", edge="wrong-wrong-wrong-wrong")), _settings())
            client_ip.client_ip(_req(_vercel("192.0.2.1")), _settings())
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2 and "may differ" in msgs[0] and "carries the proxy secret" in msgs[1]
