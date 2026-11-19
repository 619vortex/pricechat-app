"""The validation round, kept in production (owner, 2026-11-19): "make the validation persistent in production, independent of this Claude
session. Store candidate decisions, rejection reasons, extraordinary-news shadow verdicts and image audit results in the existing database.
Show progress in Terminal Admin. When 30 candidates are collected, generate the report automatically. Flag obvious false negatives and bad
images immediately. Keep extraordinary news in shadow mode, AI vision OFF, and publishing behavior unchanged."

Everything here OBSERVES: no decision, picture or post is changed by it. Each function swallows its own errors (a validation bug must never
stop a post). The image audit is rule-based (vision stays off): it re-checks the published picture's record and pixels, so it can say a
photo is unlicensed, off-subject by the editor's own subject rules, a portrait on a violent story, mis-framed, soft or uncaptioned; it can't
see what a photo shows beyond that. The false-negative flag is a "look at this": a rejected candidate that was widely reported, scored high,
or that the extraordinary path (shadow) would have published."""
from __future__ import annotations

import io
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdValidation

logger = logging.getLogger(__name__)

TARGET_CANDIDATES = 30
TARGET_IMAGES = 10
FN_OUTLETS = 4  # reported by this many independent outlets and still refused: look again
FN_VIRALITY = 75  # the desk's own interest score this high and still refused: look again
FRAMES = {"telegram": (1280, 1024), "x": (1600, 900)}
CREDIT_MAX = 130  # characters drawn along the bottom: longer is unreadable on a phone


def _row(db: Session, kind: str, ref: str) -> NdValidation | None:
    return db.scalar(select(NdValidation).where(NdValidation.kind == kind, NdValidation.ref == ref))


def _count(db: Session, kind: str) -> int:
    return int(db.scalar(select(func.count()).select_from(NdValidation).where(NdValidation.kind == kind)) or 0)


# ------------------------------------------------------------------------------------------------------------------ candidates
def false_negative(c: dict[str, Any]) -> str:
    """Why a REFUSED candidate deserves a second look, or "". Repeats are never flagged (that refusal is about the channel, not the story)."""
    if c.get("decision") != "REJECT":
        return ""
    why = str(c.get("why") or "").lower()
    if "repeat" in why or "already" in why:
        return ""
    reasons = []
    if c.get("extraordinary_would_publish"):
        reasons.append(f"the extraordinary path (shadow) would publish it: {c.get('extraordinary')}")
    if int(c.get("outlets") or 0) >= FN_OUTLETS:
        reasons.append(f"reported by {c.get('outlets')} outlets")
    if int(c.get("virality") or 0) >= FN_VIRALITY:
        reasons.append(f"the desk scored it {c.get('virality')}")
    return "possible false negative: " + "; ".join(reasons) if reasons else ""


def record_candidate(db: Session, ev: Any, shadow: dict[str, Any]) -> None:
    """A new News Desk candidate, as the gate judged it the moment it was seen."""
    try:
        ref = str(ev.id)
        if ev.id is None or _row(db, "candidate", ref) is not None:
            return
        data = {**shadow, "id": ev.id, "kind": getattr(ev, "kind", ""), "virality": int(getattr(ev, "virality", 0) or 0),
                "sources": [str(x.get("name") or "") for x in (getattr(ev, "sources", None) or []) if isinstance(x, dict)][:5],
                "seen_at": utcnow().isoformat()}
        with db.begin_nested():
            db.add(NdValidation(kind="candidate", ref=ref, flag=false_negative(data), data=data))
    except Exception as e:  # noqa: BLE001
        logger.info("validation: candidate #%s not recorded (%s)", getattr(ev, "id", "?"), type(e).__name__)


def record_gate(db: Session, ev: Any, ok: bool, interest: int, why: str) -> None:
    """The release gate's verdict on a candidate that got that far (the outlets may have grown since it was first seen)."""
    try:
        row = _row(db, "candidate", str(ev.id))
        if row is None:
            return
        data = dict(row.data or {})
        data["gate"] = {"decision": "PUBLISH" if ok else "REJECT", "interest": interest, "why": why[:400], "at": utcnow().isoformat()}
        row.data = data
    except Exception as e:  # noqa: BLE001
        logger.info("validation: gate for #%s not recorded (%s)", getattr(ev, "id", "?"), type(e).__name__)


# ------------------------------------------------------------------------------------------------------------------ images
def audit_image(headline: str, image: bytes | None, meta: dict[str, Any], platform: str) -> list[str]:
    """Rule-based problems with a PUBLISHED picture (vision is off): [] when none were found."""
    from app.newsdesk import clean, editor, photography, photos

    problems: list[str] = []
    meta = meta or {}
    kind = str(meta.get("kind") or "")
    if image:
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(image))
            want = FRAMES["x" if platform == "x" else "telegram"]
            if img.size != want:
                problems.append(f"frame {img.size[0]}x{img.size[1]}, expected {want[0]}x{want[1]} (stretched or mis-cropped)")
            if kind == "photo":
                bad = editor.pixel_problem(img.convert("RGB"), ui=False, sharp_min=photography.LIBRARY_SHARP_MIN)
                if bad:
                    problems.append(f"picture quality: {bad}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"the published picture could not be read ({type(e).__name__})")
    if kind != "photo":
        return problems
    lic = photos.canonical_licence(str(meta.get("licence") or ""), str((meta.get("photo") or {}).get("licence_url") or ""))
    if lic is None:
        problems.append(f"licence not on the allow-list: {meta.get('licence') or 'none recorded'}")
    if not str(meta.get("page") or "").startswith("https://"):
        problems.append("no source page recorded for the licence")
    lib = str(meta.get("library") or "")
    if not lib and not (meta.get("editor") or {}).get("vision"):
        problems.append("a searched photo with no visual verification (only library photos may publish while vision is off)")
    credit = str(meta.get("credit_line") or "")
    if not credit:
        problems.append("no credit drawn on the picture")
    elif len(credit) > CREDIT_MAX or not clean.drawable(credit):
        problems.append(f"credit too long or undrawable for a phone ({len(credit)} characters)")
    if lib:
        people = {s.key for s in photography.PEOPLE} | {p[0].key for p in photography.PAIRS}
        named = photography.people_of(headline)
        subject = photography.subject_of(headline)
        expected = {k for k in ((named[0].key if named else ""), (subject.key if subject else "")) if k}
        if expected and lib not in expected and not meta.get("symbol_subject"):
            problems.append(f"off-subject: the {lib} photo for a story about {', '.join(sorted(expected))}")
        if lib in people or lib == "binance":
            if clean.ACT_OF_VIOLENCE.search(headline or ""):
                problems.append(f"a portrait ({lib}) on a story of violence")
            if not str(meta.get("who") or ""):
                problems.append("a photo of people with no caption saying who, where and when")
    return problems


def record_image(db: Session, platform: str, ref: str, ev: Any, image: bytes | None, meta: dict[str, Any], link: str = "") -> None:
    """A picture that was actually published, audited at once."""
    try:
        if not image or _row(db, "image", f"{platform}:{ref}") is not None:
            return
        headline = str(getattr(ev, "headline", "") or getattr(ev, "title", "") or "")
        problems = audit_image(headline, image, meta, platform)
        data = {"platform": platform, "ref": ref, "event": getattr(ev, "id", None), "headline": headline[:300], "link": link,
                "kind": meta.get("kind"), "library": meta.get("library"), "title": meta.get("title"), "credit": meta.get("credit_line"),
                "licence": meta.get("licence"), "page": meta.get("page"), "who": meta.get("who", ""), "problems": problems,
                "verdict": "FLAG" if problems else "PASS", "at": utcnow().isoformat(), "checks": "rule-based (vision off)"}
        flag = "bad image: " + "; ".join(problems) if problems else ""
        with db.begin_nested():
            db.add(NdValidation(kind="image", ref=f"{platform}:{ref}", flag=flag, data=data))
    except Exception as e:  # noqa: BLE001
        logger.info("validation: %s image %s not recorded (%s)", platform, ref, type(e).__name__)


# ------------------------------------------------------------------------------------------------------------------ the report
def _first(db: Session, kind: str, n: int) -> list[NdValidation]:
    return list(db.scalars(select(NdValidation).where(NdValidation.kind == kind).order_by(NdValidation.id).limit(n)))


def _reason_group(why: str) -> str:
    w = re.sub(r"^why (?:should anyone|would our audience) care\? ", "", why or "")
    return re.split(r"[:(,]", w, maxsplit=1)[0].strip()[:80] or "other"


def build_report(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """The 30-candidate report (and the first 10 pictures, as far as they have come): actual production results only."""
    now = now or utcnow()
    cands = [r.data | {"flag": r.flag} for r in _first(db, "candidate", TARGET_CANDIDATES)]
    imgs = [r.data | {"flag": r.flag} for r in _first(db, "image", TARGET_IMAGES)]
    published = [c for c in cands if c.get("decision") == "PUBLISH"]
    refused = [c for c in cands if c.get("decision") == "REJECT"]
    fns = [c for c in cands if c.get("flag")]
    would = [c for c in refused if c.get("extraordinary_would_publish")]
    return {
        "generated_at": now.isoformat(), "candidates": len(cands), "published": len(published), "refused": len(refused),
        "mean_interest_published": round(sum(int(c.get("interest") or 0) for c in published) / len(published), 1) if published else None,
        "refusal_reasons": Counter(_reason_group(str(c.get("why") or "")) for c in refused).most_common(),
        "possible_false_negatives": [{"id": c.get("id"), "title": c.get("title"), "why_flagged": c.get("flag"), "gate_reason": c.get("why")} for c in fns],
        "extraordinary_shadow": {"would_add": len(would), "titles": [c.get("title") for c in would], "live": False},
        "images": {"audited": len(imgs), "flagged": sum(1 for i in imgs if i.get("problems")), "rows": imgs},
        "rows": cands,
        "note": "Actual production decisions as the gate made them. Image checks are rule-based while AI vision is off; flags are for review.",
    }


async def tick(db: Session, now: datetime | None = None, *, settings: Any = None, transport: Any = None) -> dict[str, Any]:
    """After each desk run: tell the owner at once about a flagged candidate or picture, and make the report at 30 candidates (refreshed
    once more when the 10th picture is audited). Never raises."""
    from app.alerts import notify_owner

    now = now or utcnow()
    out = {"alerts": 0, "report": ""}
    try:
        for r in db.scalars(select(NdValidation).where(NdValidation.flag != "", NdValidation.alerted.is_(False)).order_by(NdValidation.id).limit(5)):
            d = r.data or {}
            if r.kind == "candidate":
                text = (f"Price Chat validation: {r.flag}\n#{d.get('id')} {str(d.get('title') or '')[:160]}\nRefused: {str(d.get('why') or '')[:200]}\n"
                        f"Terminal › Settings › Validation")
            else:
                text = f"Price Chat validation: {d.get('platform')} picture flagged\n{str(d.get('headline') or '')[:160]}\n{r.flag[:300]}\n{d.get('link') or ''}"
            await notify_owner(db, f"validation:{r.kind}:{r.ref}", text, settings=settings, transport=transport, now=now)
            r.alerted = True  # once: the flag stays on the record in Admin either way
            out["alerts"] += 1
        n_c, n_i = _count(db, "candidate"), _count(db, "image")
        report = _row(db, "report", str(TARGET_CANDIDATES))
        due = n_c >= TARGET_CANDIDATES and (report is None or ((report.data or {}).get("images", {}).get("audited", 0) < TARGET_IMAGES <= n_i))
        if due:
            data = build_report(db, now)
            if report is None:
                db.add(NdValidation(kind="report", ref=str(TARGET_CANDIDATES), data=data))
                what = "ready"
            else:
                report.data = data
                what = "updated with the first 10 pictures"
            text = (f"Price Chat validation report {what}: {data['candidates']} real candidates, {data['published']} published, {data['refused']} refused, "
                    f"{len(data['possible_false_negatives'])} possible false negatives; extraordinary (shadow) would add {data['extraordinary_shadow']['would_add']}; "
                    f"pictures audited {data['images']['audited']}, flagged {data['images']['flagged']}. Terminal › Settings › Validation")
            await notify_owner(db, f"validation:report:{what}", text, settings=settings, transport=transport, now=now, cooldown=False)
            out["report"] = what
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("validation: tick failed (%s: %s)", type(e).__name__, str(e)[:200])
    return out


def progress(db: Session, limit: int = 60) -> dict[str, Any]:
    """What Terminal Admin shows."""
    from app.newsdesk import newsworthy

    cands = list(db.scalars(select(NdValidation).where(NdValidation.kind == "candidate").order_by(NdValidation.id.desc()).limit(limit)))
    imgs = list(db.scalars(select(NdValidation).where(NdValidation.kind == "image").order_by(NdValidation.id.desc()).limit(40)))
    report = _row(db, "report", str(TARGET_CANDIDATES))
    first = _first(db, "candidate", TARGET_CANDIDATES)
    return {
        "candidates": {"count": _count(db, "candidate"), "target": TARGET_CANDIDATES,
                       "published": sum(1 for r in first if (r.data or {}).get("decision") == "PUBLISH"),
                       "flagged": sum(1 for r in first if r.flag)},
        "images": {"count": _count(db, "image"), "target": TARGET_IMAGES, "flagged": sum(1 for r in imgs if r.flag)},
        "extraordinary_live": newsworthy.EXTRAORDINARY_LIVE, "vision": "off",
        "rows": [{**(r.data or {}), "flag": r.flag, "alerted": r.alerted} for r in cands],
        "image_rows": [{**(r.data or {}), "flag": r.flag, "alerted": r.alerted} for r in imgs],
        "report": report.data if report is not None else None,
    }
