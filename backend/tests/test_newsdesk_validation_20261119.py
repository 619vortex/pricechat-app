"""The validation round kept in production (owner, 2026-11-19: "make the validation persistent in production, independent of this Claude
session ... When 30 candidates are collected, generate the report automatically. Flag obvious false negatives and bad images immediately.
Keep extraordinary news in shadow mode, AI vision OFF, and publishing behavior unchanged")."""
import io
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdValidation
from app.newsdesk import newsworthy, validation


def cand(i: int, title: str, outlets: int = 2, virality: int = 60) -> SimpleNamespace:
    return SimpleNamespace(id=i, kind="news", title=title, headline=title, summary="", evidence="two outlets", virality=float(virality),
                           sources=[{"name": f"Outlet {k}"} for k in range(outlets)], facts={})


def record(db: Session, ev: SimpleNamespace) -> NdValidation:
    validation.record_candidate(db, ev, newsworthy.shadow(ev.title, "", outlets=len(ev.sources), evidence=ev.evidence))
    db.commit()
    return db.scalar(select(NdValidation).where(NdValidation.kind == "candidate", NdValidation.ref == str(ev.id)))


def photo(size: tuple[int, int]) -> bytes:
    import random

    rnd = random.Random(3)
    img = Image.new("RGB", size, (12, 12, 16))
    small = Image.new("RGB", (size[0] // 6, size[1] // 6))
    small.putdata([(rnd.randint(120, 255), rnd.randint(80, 200), rnd.randint(0, 90)) for _ in range(small.width * small.height)])
    img.paste(small.resize((size[0] // 2, size[1] // 2)), (size[0] // 4, size[1] // 4))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=92)
    return b.getvalue()


def meta(library: str, who: str = "", licence: str = "public domain") -> dict[str, Any]:
    return {"kind": "photo", "library": library, "licence": licence, "page": "https://commons.wikimedia.org/wiki/File:X.jpg", "who": who,
            "title": "File:X.jpg", "credit_line": f"{who + ' · ' if who else ''}Photo: The White House / Wikimedia Commons (public domain)",
            "editor": {"ok": True, "role": "subject"}}


# ------------------------------------------------------------------------------------------------------------------ candidates
def test_every_candidate_is_kept_with_its_decision_reason_and_shadow_verdict(db: Session) -> None:
    row = record(db, cand(1, "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south"))
    assert row.data["decision"] == "REJECT" and "routine military update" in row.data["why"] and row.data["evidence"] == "two outlets"
    assert "extraordinary" in row.data and row.flag == ""  # refused, rightly: no flag
    record(db, cand(1, "anything"))  # once per candidate
    assert len(list(db.scalars(select(NdValidation).where(NdValidation.kind == "candidate")))) == 1


@pytest.mark.parametrize("title,outlets,virality,flagged", [
    ("Archaeologists unearth 2,000-year-old Roman gold coin hoard", 2, 50, "extraordinary path"),  # the shadow path would publish it
    ("Senate committee holds hearing on stablecoin rules", 5, 50, "reported by 5 outlets"),
    ("Senate committee holds hearing on stablecoin rules", 2, 80, "scored it 80"),
    ("Senate committee holds hearing on stablecoin rules", 2, 50, ""),
])
def test_obvious_false_negatives_are_flagged(db: Session, title: str, outlets: int, virality: int, flagged: str) -> None:
    row = record(db, cand(7, title, outlets, virality))
    assert (flagged in row.flag) if flagged else row.flag == ""


def test_a_repeat_is_never_flagged() -> None:
    assert validation.false_negative({"decision": "REJECT", "why": "repeat: already posted 3 h ago", "outlets": 9, "virality": 90}) == ""


def test_the_gate_verdict_is_added_when_it_gets_that_far(db: Session) -> None:
    ev = cand(9, "Binance receives Abu Dhabi court order to 'Freeze' assets after $6.3B heist", 3)
    record(db, ev)
    validation.record_gate(db, ev, True, 90, "the $6.3B heist case")
    db.commit()
    row = db.scalar(select(NdValidation).where(NdValidation.ref == "9"))
    assert row.data["gate"]["decision"] == "PUBLISH" and row.data["gate"]["interest"] == 90


def test_extraordinary_stays_in_shadow_and_publishing_is_unchanged() -> None:
    assert newsworthy.EXTRAORDINARY_LIVE is False
    assert not newsworthy.qualify("Archaeologists unearth 2,000-year-old Roman gold coin hoard", "", outlets=3).ok


# ------------------------------------------------------------------------------------------------------------------ images
def test_a_right_picture_passes() -> None:
    assert validation.audit_image("Trump tells Xi the US and China will sign a trade deal", photo((1280, 1024)),
                                  meta("trump_xi", "Xi Jinping and Donald Trump in Beijing, May 2026"), "telegram") == []


@pytest.mark.parametrize("headline,size,m,platform,problem", [
    ("Gold falls 3%", (1280, 1024), meta("microsoft"), "telegram", "off-subject"),
    ("Trump orders strikes on Iranian missile sites", (1600, 900), meta("trump", "Donald Trump at the White House, November 2025"), "x", "violence"),
    ("Putin says Russia is ready for talks", (1600, 900), meta("putin"), "x", "no caption"),
    ("Gold falls 3%", (1600, 900), meta("gold"), "telegram", "expected 1280x1024"),
    ("Gold falls 3%", (1280, 1024), meta("gold", licence="All rights reserved"), "telegram", "licence"),
    ("Gold falls 3%", (1280, 1024), {**meta(""), "library": ""}, "telegram", "no visual verification"),
])
def test_bad_pictures_are_flagged(headline: str, size: tuple[int, int], m: dict[str, Any], platform: str, problem: str) -> None:
    assert any(problem in p for p in validation.audit_image(headline, photo(size), m, platform))


# ------------------------------------------------------------------------------------------------------------------ alerts and report
async def test_flags_reach_the_owner_once_and_the_report_is_made_at_30(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import alerts

    sent: list[tuple[str, str]] = []

    async def notify(db: Any, key: str, text: str, **k: Any) -> bool:
        sent.append((key, text))
        return True

    monkeypatch.setattr(alerts, "notify_owner", notify)
    record(db, cand(100, "Archaeologists unearth 2,000-year-old Roman gold coin hoard"))  # a possible false negative
    validation.record_image(db, "telegram", "301", cand(100, "Gold falls 3%"), photo((1280, 1024)), meta("microsoft"), "https://t.me/x/301")
    db.commit()
    await validation.tick(db)
    assert [k for k, _ in sent] == ["validation:candidate:100", "validation:image:telegram:301"]
    await validation.tick(db)
    assert len(sent) == 2  # each flag once
    for i in range(101, 130):
        record(db, cand(i, f"Senate committee holds hearing number {i}"))
    await validation.tick(db)
    rep = db.scalar(select(NdValidation).where(NdValidation.kind == "report"))
    assert rep is not None and rep.data["candidates"] == 30 and rep.data["refused"] == 30 and len(rep.data["possible_false_negatives"]) == 1
    assert sent[-1][0] == "validation:report:ready" and "30 real candidates" in sent[-1][1]
    for i in range(302, 311):  # the 10th picture refreshes the report once
        validation.record_image(db, "x", str(i), cand(100, "Gold falls 3%"), photo((1600, 900)), meta("gold"))
    db.commit()
    await validation.tick(db)
    assert sent[-1][0] == "validation:report:updated with the first 10 pictures"
    rep = db.scalar(select(NdValidation).where(NdValidation.kind == "report"))
    assert rep.data["images"]["audited"] == 10 and rep.data["images"]["flagged"] == 1


def test_terminal_admin_shows_progress(client: Any, db: Session) -> None:
    record(db, cand(200, "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south"))
    r = client.get("/api/terminal/validation")
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"]["count"] >= 1 and body["candidates"]["target"] == 30 and body["vision"] == "off" and body["extraordinary_live"] is False
    assert any(row["id"] == 200 for row in body["rows"])
