"""Admin -> Side Projects (owner, 2026-11-23): GUMMY & BAKED stays a separate project; the admin only links to it, for admins only,
never with a localhost address, and says "Not available yet" until the audit is deployed. No GUMMY data passes through Price Chat."""
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes.side_projects import deployed_url
from app.main import create_app
from app.security import admin_auth
from tests.test_production import DOMAIN, PASSWORD, prod_settings


def client_with(**over: object) -> Iterator[TestClient]:
    admin_auth.reset_for_tests()
    app = create_app(app_settings=prod_settings(**over), background_jobs=False)
    with TestClient(app, base_url=DOMAIN) as c:
        yield c
    admin_auth.reset_for_tests()


@pytest.fixture
def undeployed() -> Iterator[TestClient]:
    yield from client_with()


@pytest.fixture
def deployed() -> Iterator[TestClient]:
    yield from client_with(gummy_audit_url="https://audit.gummybaked.example/", gummy_audit_admin_url="https://mod.gummybaked.example/moderation.html")


def login(c: TestClient) -> None:
    assert c.post("/api/admin/login", json={"username": "owner", "password": PASSWORD}).status_code == 200


def test_only_a_signed_in_admin_learns_where_the_audit_lives(deployed: TestClient) -> None:
    r = deployed.get("/api/admin/side-projects")
    assert r.status_code == 401 and r.json()["code"] == "admin_auth" and "gummybaked" not in r.text
    login(deployed)
    [p] = deployed.get("/api/admin/side-projects").json()["projects"]
    assert p == {"key": "gummy-baked", "label": "GUMMY & BAKED Audit", "about": p["about"], "available": True,
                 "site_url": "https://audit.gummybaked.example/", "admin_url": "https://mod.gummybaked.example/moderation.html"}


def test_not_available_yet_until_it_is_deployed(undeployed: TestClient) -> None:
    login(undeployed)
    [p] = undeployed.get("/api/admin/side-projects").json()["projects"]
    assert p["available"] is False and p["site_url"] is None and p["admin_url"] is None


def test_a_local_address_is_never_sent_even_if_configured() -> None:
    for c in client_with(gummy_audit_url="http://127.0.0.1:8000/", gummy_audit_admin_url="http://127.0.0.1:8001/moderation.html"):
        login(c)
        r = c.get("/api/admin/side-projects")
        [p] = r.json()["projects"]
        assert p["available"] is False and "127.0.0.1" not in r.text and "localhost" not in r.text


@pytest.mark.parametrize("url,ok", [
    ("https://audit.example.org/", True), ("https://audit.example.org/x?y=1", True),
    ("", False), ("http://audit.example.org/", False), ("https://localhost:8000/", False), ("https://app.localhost/", False),
    ("https://127.0.0.1/", False), ("https://10.0.0.5/", False), ("https://192.168.1.20/", False), ("https://[::1]/", False),
    ("https://gummy-pc/", False), ("https://box.local/", False), ("https://user:pw@audit.example.org/", False), ("javascript:alert(1)", False),
    ("https://8.8.8.8/", True),
])
def test_what_counts_as_a_deployed_address(url: str, ok: bool) -> None:
    assert (deployed_url(url) is not None) is ok
