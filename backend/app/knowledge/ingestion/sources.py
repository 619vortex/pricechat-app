"""Placeholder knowledge sources. None of these connect to anything in Phase 2."""

from collections.abc import AsyncIterator
from datetime import datetime

from app.knowledge.ingestion.base import IngestionItem, KnowledgeSource, SourceNotAvailableError
from app.models.enums import MemoryKind, MemoryLayer, Platform, SourceType


class _Placeholder(KnowledgeSource):
    async def fetch(self, since: datetime | None = None) -> AsyncIterator[IngestionItem]:
        raise SourceNotAvailableError(f"{self.display_name} ingestion is not implemented yet")
        yield  # pragma: no cover  (makes this an async generator)


class TelegramCommunitySource(_Placeholder):
    key = "telegram"
    display_name = "Telegram community"
    source_type = SourceType.TELEGRAM
    layer = MemoryLayer.COMMUNITY
    kind = MemoryKind.MESSAGE
    platform = Platform.TELEGRAM
    description = "Reviewed insights learned from authorised Telegram chats (raw messages stay separate)."


class XPostsSource(_Placeholder):
    key = "x"
    display_name = "X / Twitter posts"
    source_type = SourceType.X
    layer = MemoryLayer.SOCIAL
    kind = MemoryKind.POST
    platform = Platform.X
    description = "Relevant posts and conversations from X, as social knowledge."


class DocumentSource(_Placeholder):
    key = "documents"
    display_name = "Documents"
    source_type = SourceType.DOCUMENT
    layer = MemoryLayer.BRAND
    kind = MemoryKind.DOCUMENT
    description = "Uploaded documents (PDF, DOCX, Markdown), split into searchable chunks."


class UrlSource(_Placeholder):
    key = "urls"
    display_name = "Web pages"
    source_type = SourceType.URL
    layer = MemoryLayer.BRAND
    kind = MemoryKind.DOCUMENT
    description = "Content fetched from URLs you provide."


SOURCES: tuple[KnowledgeSource, ...] = (
    TelegramCommunitySource(),
    XPostsSource(),
    DocumentSource(),
    UrlSource(),
)
