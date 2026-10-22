"""Links between AI-derived records and the Telegram messages that support them."""

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.community import ProvenanceLink
from app.models.enums import DerivedKind
from app.models.telegram import TelegramMessage


def link(db: Session, kind: DerivedKind, derived_id: int, message_ids: Iterable[int]) -> int:
    existing = set(
        db.scalars(
            select(ProvenanceLink.message_id).where(
                ProvenanceLink.derived_kind == kind, ProvenanceLink.derived_id == derived_id
            )
        )
    )
    new = [m for m in dict.fromkeys(message_ids) if m not in existing]
    db.add_all(ProvenanceLink(derived_kind=kind, derived_id=derived_id, message_id=m) for m in new)
    return len(new)


def message_ids_for(db: Session, kind: DerivedKind, derived_id: int) -> list[int]:
    return list(
        db.scalars(
            select(ProvenanceLink.message_id)
            .where(ProvenanceLink.derived_kind == kind, ProvenanceLink.derived_id == derived_id)
            .order_by(ProvenanceLink.message_id)
        )
    )


def messages_for(db: Session, kind: DerivedKind, derived_id: int, limit: int = 50) -> list[TelegramMessage]:
    return list(
        db.scalars(
            select(TelegramMessage)
            .join(ProvenanceLink, ProvenanceLink.message_id == TelegramMessage.id)
            .where(ProvenanceLink.derived_kind == kind, ProvenanceLink.derived_id == derived_id)
            .order_by(TelegramMessage.sent_at)
            .limit(limit)
        )
    )


def unlink_all(db: Session, kind: DerivedKind, derived_id: int) -> None:
    db.execute(delete(ProvenanceLink).where(ProvenanceLink.derived_kind == kind, ProvenanceLink.derived_id == derived_id))


def derived_ids_without_support(db: Session, kind: DerivedKind, candidate_ids: Iterable[int]) -> list[int]:
    """Of the given derived records, those with no remaining supporting messages."""
    ids = list(candidate_ids)
    if not ids:
        return []
    supported = set(
        db.scalars(
            select(ProvenanceLink.derived_id).where(ProvenanceLink.derived_kind == kind, ProvenanceLink.derived_id.in_(ids))
        )
    )
    return [i for i in ids if i not in supported]
