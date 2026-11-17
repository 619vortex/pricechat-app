"""Browser security headers on backend responses (app/security/headers.py): api.pricechat.live is called directly by the Kiwi VPN
apps and the extension, so Vercel's header rules never apply there."""

from fastapi.testclient import TestClient

from app.main import create_app
from app.security import admin_auth
from tests.test_production import DOMAIN, prod_settings

SAME_AS_VERCEL = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def _has_base(r) -> None:  # type: ignore[no-untyped-def]
    for name, value in SAME_AS_VERCEL.items():
        assert r.headers.get_list(name) == [value], name


def test_production_responses_carry_the_headers_and_hsts_even_when_refused() -> None:
    admin_auth.reset_for_tests()
    try:
        with TestClient(create_app(app_settings=prod_settings(), background_jobs=False), base_url=DOMAIN) as c:
            ok = c.get("/api/health/live")
            refused = c.get("/api/system/alerts")  # admin_guard's 401
            cross_site = c.post("/api/members/vpn/trial", headers={"Origin": "https://evil.example"})  # its 403
            missing = c.get("/api/members/no-such-route")  # a public prefix, so routing answers (elsewhere admin_guard's 401 comes first)
            for r in (ok, refused, cross_site, missing):
                _has_base(r)
                assert r.headers.get_list("strict-transport-security") == ["max-age=31536000; includeSubDomains"]
            assert (ok.status_code, refused.status_code, cross_site.status_code, missing.status_code) == (200, 401, 403, 404)
            preflight = c.options("/api/members/vpn/trial", headers={"Origin": DOMAIN, "Access-Control-Request-Method": "POST"})
            assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == DOMAIN  # CORS unchanged
            _has_base(preflight)
    finally:
        admin_auth.reset_for_tests()


def test_no_hsts_outside_production(client: TestClient) -> None:
    r = client.get("/api/health/live")
    assert r.status_code == 200
    _has_base(r)
    assert "strict-transport-security" not in r.headers


def test_a_header_a_route_sets_itself_is_kept() -> None:
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.security.headers import SecurityHeaders

    inner = Starlette(routes=[Route("/", lambda request: PlainTextResponse("x", headers={"X-Frame-Options": "DENY"}))])
    with TestClient(SecurityHeaders(inner, hsts=False)) as c:
        r = c.get("/")
    assert r.headers.get_list("x-frame-options") == ["DENY"]
    assert r.headers["x-content-type-options"] == "nosniff"
