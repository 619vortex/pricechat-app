"""Embedding vectors for Telegram messages (used for topics, questions and search)."""

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.telegram import TelegramMessage, TelegramMessageVector
from app.providers.base import EmbeddingProvider

BATCH = 32


def normalise(vector: list[float] | np.ndarray) -> np.ndarray:
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm > 0 else arr


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


async def embed_messages(db: Session, embedder: EmbeddingProvider, messages: list[TelegramMessage]) -> dict[int, np.ndarray]:
    """Return {message.id: unit vector}, embedding (and storing) any that are missing or stale."""
    ids = [m.id for m in messages]
    existing = {
        v.message_id: v
        for v in db.scalars(select(TelegramMessageVector).where(TelegramMessageVector.message_id.in_(ids)))
    }
    out: dict[int, np.ndarray] = {}
    todo = []
    for m in messages:
        row = existing.get(m.id)
        if row is not None and row.model == embedder.model and m.edited_at is None:
            out[m.id] = from_blob(row.vector)
        else:
            todo.append(m)

    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        vectors = await embedder.embed([m.text[:2000] or f"[{m.media_type}]" for m in batch], purpose="document")
        for m, raw in zip(batch, vectors, strict=True):
            vec = normalise(raw)
            row = existing.get(m.id) or TelegramMessageVector(message_id=m.id)
            row.model = embedder.model
            row.dimensions = int(vec.shape[0])
            row.vector = to_blob(vec)
            db.add(row)
            out[m.id] = vec
    db.flush()
    return out


def load_vectors(db: Session, message_ids: list[int], model: str) -> dict[int, np.ndarray]:
    if not message_ids:
        return {}
    rows = db.execute(
        select(TelegramMessageVector.message_id, TelegramMessageVector.vector).where(
            TelegramMessageVector.message_id.in_(message_ids), TelegramMessageVector.model == model
        )
    ).all()
    return {mid: from_blob(blob) for mid, blob in rows}
