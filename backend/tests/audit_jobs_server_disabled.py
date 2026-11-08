"""AUDIT (2026-11-08, AR-5): a job switched off on this server by SCHEDULER_ONLY_JOBS is still listed by GET /api/jobs as
enabled (no field says "off on this server"), and PATCH enabled=true reports success although nothing will run.
Asserts CURRENT behaviour; flip when the API exposes the server-level state."""

from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app


def test_server_disabled_job_is_reported_enabled(fake_provider, fake_embedder) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings().model_copy(update={"scheduler_only_jobs": "telegram.ingest"})
    app = create_app(app_settings=settings, provider=fake_provider, embedder=fake_embedder, background_jobs=False)
    with TestClient(app) as c:
        jobs = {j["name"]: j for j in c.get("/api/jobs").json()}
        assert "x.ingest" in app.state.runtime.scheduler.disabled           # really off on this server
        assert jobs["x.ingest"]["enabled"] is True                          # ...but the dashboard is told "enabled"
        assert not any(k in jobs["x.ingest"] for k in ("server_disabled", "disabled_on_server", "off_here"))
        r = c.patch("/api/jobs/x.ingest", json={"enabled": True})
        assert r.status_code == 200 and r.json()["status"] == "enabled"    # "enabled" although it can never run here
        run = c.post("/api/jobs/x.ingest/run").json()
        assert run["status"] == "disabled"                                   # only "Run now" reveals it
