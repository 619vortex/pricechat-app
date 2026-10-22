"""The shared cache builds a value once even when many requests ask for it at the same moment."""

import threading
import time

from app.pokemon import history


def test_concurrent_callers_share_one_build() -> None:
    history.invalidate()
    builds = []

    def build() -> int:
        builds.append(1)
        time.sleep(0.2)
        return 42

    out: list[int] = []
    threads = [threading.Thread(target=lambda: out.append(history.cached(("base", "TEST", 7), build, ttl=60))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out == [42] * 8 and len(builds) == 1
    history.invalidate()
