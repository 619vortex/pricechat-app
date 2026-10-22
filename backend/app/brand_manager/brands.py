"""Brands and the plain-language event log."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import defaults
from app.db.base import utcnow
from app.models.brand_manager import BmAutomation, BmBrand, BmEvent

TEXT_FIELDS = ("name", "website", "description", "audience", "industry", "language", "personality", "logo_url", "cta")
LIST_FIELDS = ("topics", "avoid", "colors", "competitors")


def ensure_default(db: Session) -> BmBrand:
    """The first brand is Price Chat, seeded from the AI Brain's brand profile so the owner starts with their own voice."""
    b = db.scalar(select(BmBrand).where(BmBrand.is_default.is_(True)).limit(1)) or db.scalar(select(BmBrand).order_by(BmBrand.id).limit(1))
    if b is not None:
        return b
    from app.models.brand import BrandProfile

    src = db.scalar(select(BrandProfile).limit(1))
    b = BmBrand(
        name=(src.brand_name if src else "") or "Price Chat", website="https://pricechat.live",
        description=(src.description if src else "") or "Price Chat is a crypto and collectibles community: market news, prices and the Pokémon card market.",
        audience=(src.audience if src else "") or "Crypto traders and Pokémon card collectors, 18-40, who want fast, accurate market news.",
        industry="Crypto & collectibles media", language="English",
        personality=(src.personality if src else "") or "Fast, clear, confident and honest. Plain English, no hype, no financial advice.",
        topics=list(src.topics_of_interest) if src and src.topics_of_interest else ["Bitcoin", "altcoins", "crypto news", "Pokémon card market"],
        avoid=list(src.topics_to_avoid) if src and src.topics_to_avoid else ["price predictions", "financial advice", "politics"],
        colors=["#00e58f", "#050706", "#e6c46e"], logo_url="", examples=[], cta="Follow Price Chat for daily market news.",
        competitors=[], strategy={}, is_default=True,
    )
    db.add(b)
    db.flush()
    db.add(BmAutomation(brand_id=b.id, running=False, status="stopped", reason=""))
    db.commit()
    return b


def automation(db: Session, brand: BmBrand) -> BmAutomation:
    a = db.scalar(select(BmAutomation).where(BmAutomation.brand_id == brand.id))
    if a is None:
        a = BmAutomation(brand_id=brand.id, running=False, status="stopped", reason="")
        db.add(a)
        db.commit()
    return a


def strategy(brand: BmBrand) -> dict[str, Any]:
    return defaults.merged(brand.strategy)


def brand_out(b: BmBrand) -> dict[str, Any]:
    return {"id": b.id, **{f: getattr(b, f) for f in TEXT_FIELDS + LIST_FIELDS}, "examples": b.examples, "is_default": b.is_default,
            "strategy": strategy(b)}


def update(db: Session, b: BmBrand, data: dict[str, Any]) -> BmBrand:
    for f in TEXT_FIELDS:
        if f in data and data[f] is not None:
            setattr(b, f, str(data[f]).strip())
    for f in LIST_FIELDS:
        if f in data and data[f] is not None:
            setattr(b, f, [str(x).strip() for x in data[f] if str(x).strip()][:50])
    if "examples" in data and data["examples"] is not None:
        b.examples = [{"text": str(e.get("text", "")).strip()[:2000], "approved": bool(e.get("approved", True))}
                      for e in data["examples"] if str(e.get("text", "")).strip()][:30]
    if "strategy" in data and data["strategy"] is not None:
        keep = {k: v for k, v in data["strategy"].items() if k in defaults.STRATEGY}
        b.strategy = {**(b.strategy or {}), **keep}
    db.commit()
    return b


def log(db: Session, brand_id: int, message: str, *, level: str = "info", stage: str | None = None, video_id: int | None = None,
        commit: bool = False) -> None:
    db.add(BmEvent(brand_id=brand_id, video_id=video_id, at=utcnow(), level=level, stage=stage, message=message[:2000]))
    if commit:
        db.commit()


def events(db: Session, brand_id: int, video_id: int | None = None, limit: int = 60) -> list[dict[str, Any]]:
    q = select(BmEvent).where(BmEvent.brand_id == brand_id)
    if video_id is not None:
        q = q.where(BmEvent.video_id == video_id)
    return [{"at": e.at.isoformat(), "level": e.level, "stage": e.stage, "message": e.message, "video_id": e.video_id}
            for e in db.scalars(q.order_by(BmEvent.id.desc()).limit(limit))]
