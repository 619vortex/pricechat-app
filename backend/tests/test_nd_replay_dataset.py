"""The 7-day replay dataset (tests/fixtures/nd_replay_7d.json) stays loadable and replayable through the current gates
(ops/newsdesk/replay_7d.py). A guard, not a target: no story labelled stay_out may pass every replayed gate."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _replay():
    spec = importlib.util.spec_from_file_location("replay_7d", ROOT / "ops" / "newsdesk" / "replay_7d.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_the_dataset_replays_and_junk_stays_out() -> None:
    ds = json.loads((ROOT / "backend" / "tests" / "fixtures" / "nd_replay_7d.json").read_text(encoding="utf8"))
    assert len(ds["items"]) > 600 and {"publish", "stay_out", "covered", "went_out"} <= {i["label"] for i in ds["items"]}
    r = _replay()
    passed = {i["id"] for i in ds["items"] if all(ok for _, ok, _ in r.stages(i))}
    assert not [i["id"] for i in ds["items"] if i["label"] == "stay_out" and i["id"] in passed]
