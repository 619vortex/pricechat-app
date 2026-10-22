from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.memory.records import MemoryRecord
from app.memory.store import MemoryFilters

Strategy = Literal["hybrid", "lexical", "semantic", "none"]


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    text: str
    limit: int = 6
    filters: MemoryFilters | None = None


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    record: MemoryRecord
    score: float  # 0..~1.1, comparable within one result set
    lexical: float = 0.0
    semantic: float | None = None  # raw cosine similarity when semantic search ran
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    memories: list[RetrievedMemory]
    strategy: Strategy
    duration_ms: float
    notes: list[str] = field(default_factory=list)  # e.g. why semantic search was skipped


class MemoryRetriever(Protocol):
    """Finds the memories most relevant to a request. Must not raise for provider outages."""

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...
