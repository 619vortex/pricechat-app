"""The extraordinary-news path and the production measurement (owner, 2026-11-19, validation round: "Add a carefully verified
extraordinary-news category for discoveries, bizarre events, major revelations and unexpected developments. Don't let ordinary speculation
or conspiracy claims pass as facts ... validate against NEW real production events").

It is judged and logged for every candidate but publishes nothing until EXTRAORDINARY_LIVE (measured first on the next real candidates)."""
import json
import logging

import pytest

from app.newsdesk import newsworthy as nw


@pytest.mark.parametrize("headline,kind", [
    ("Astronomers discover Earth-sized planet in habitable zone of nearby star", "a discovery"),
    ("Archaeologists unearth 2,000-year-old Roman gold coin hoard", "a discovery"),
    ("Leaked documents show Tether held reserves at sanctioned bank", "a revelation"),
    ("Pentagon releases declassified UFO footage", "a revelation"),
    ("Exchange mistakenly sends $40 million in bitcoin to users", "unexpected"),
])
def test_verified_extraordinary_stories(headline: str, kind: str) -> None:
    x = nw.extraordinary(headline, outlets=2)
    assert x.ok and x.why.startswith(f"extraordinary: {kind}")


@pytest.mark.parametrize("headline", [
    "Scientists may have found signs of life on K2-18b",  # a maybe
    "UFO sighting over New Jersey stuns residents",  # a sighting is not a finding
    "Conspiracy theorists claim Bitcoin creator found",
    "Satoshi Nakamoto's identity revealed, documentary claims",  # a claim
    "Study suggests coffee could extend lifespan",
    "Experts react to bizarre Fed decision",  # a reaction
])
def test_speculation_claims_and_theories_never_pass_as_facts(headline: str) -> None:
    assert not nw.extraordinary(headline, outlets=3).ok


def test_one_outlet_is_never_enough() -> None:
    assert not nw.extraordinary("Archaeologists unearth 2,000-year-old Roman gold coin hoard", outlets=1).ok


def test_not_live_it_publishes_nothing_but_says_what_it_would() -> None:
    assert nw.EXTRAORDINARY_LIVE is False
    head = "Archaeologists unearth 2,000-year-old Roman gold coin hoard"
    assert not nw.qualify(head, "", outlets=3).ok  # the live decision is unchanged
    s = nw.shadow(head, "", outlets=3, evidence="two outlets")
    assert s["decision"] == "REJECT" and s["extraordinary_would_publish"] and s["extraordinary_interest"] >= nw.INTEREST_BAR


def test_live_it_goes_through_the_audience_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nw, "EXTRAORDINARY_LIVE", True)
    a = nw.qualify("Bank accidentally credits customer with $81 trillion", "", outlets=3)
    assert a.ok and a.strength == "extraordinary" and "accidentally" in a.pitch
    assert not nw.qualify("Trump tells Xi the US and China will end the war, in a surprise call", "", outlets=3).ok  # diplomacy talk stays refused


def test_every_new_candidate_is_measured_in_the_production_log(caplog: pytest.LogCaptureFixture) -> None:
    from types import SimpleNamespace

    from app.newsdesk import events

    ev = SimpleNamespace(id=812, kind="news", title="Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south", headline="", summary="",
                         evidence="two outlets", virality=61.0, sources=[{"name": "Al Jazeera"}, {"name": "The National"}], facts={"outlets": ["Al Jazeera", "The National"]})
    with caplog.at_level(logging.INFO, logger="app.newsdesk.events"):
        events._measure(ev, "candidate")  # type: ignore[arg-type]
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("editorial candidate: "))
    got = json.loads(line.removeprefix("editorial candidate: "))
    assert got["id"] == 812 and got["decision"] == "REJECT" and "routine military update" in got["why"] and got["evidence"] == "two outlets"
