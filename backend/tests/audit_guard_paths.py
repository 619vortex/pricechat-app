"""AUDIT (2026-11 security review): admin_guard must refuse odd spellings of admin paths. Diagnostic script."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.config.settings import Settings
from app.main import create_app
from app.security.admin_auth import hash_password

s = Settings(admin_username="owner", admin_password_hash=SecretStr(hash_password("x" * 16)))
app = create_app(app_settings=s, initialize_database=False, background_jobs=False)
c = TestClient(app, raise_server_exceptions=False)
for m, p in [("GET", "/api/admin/session"), ("GET", "/api/settings"), ("GET", "//api/settings"), ("GET", "/api//settings"),
             ("GET", "/API/settings"), ("GET", "/api/members/../settings"), ("GET", "/api/members/%2e%2e/settings"),
             ("GET", "/api/settings/"), ("HEAD", "/api/settings"), ("GET", "/api/website/home/../../settings"),
             ("GET", "/api/members%2f..%2fsettings"), ("OPTIONS", "/api/settings")]:
    r = c.request(m, p)
    print(m, p, r.status_code, r.text[:80].replace("\n", " "))
