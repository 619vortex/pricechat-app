

def test_scheduler_only_jobs_allowlist() -> None:
    from app.config.settings import Settings

    names = ["pokemon.catalog", "pokemon.top100", "telegram.ingest", "x.ingest"]
    s = Settings(_env_file=None, scheduler_only_jobs="pokemon.", scheduler_disabled_jobs="pokemon.top100")  # type: ignore[call-arg]
    assert s.jobs_off(names) == {"pokemon.top100", "telegram.ingest", "x.ingest"}
    assert Settings(_env_file=None, scheduler_disabled_jobs="").jobs_off(names) == set()  # type: ignore[call-arg]  # nothing set: everything runs
