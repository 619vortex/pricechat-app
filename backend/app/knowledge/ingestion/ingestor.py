from datetime import datetime

from app.knowledge.ingestion.base import IngestionReport, KnowledgeSource
from app.knowledge.service import KnowledgeService


class Ingestor:
    """Pulls items from a KnowledgeSource and stores them via KnowledgeService."""

    def __init__(self, knowledge: KnowledgeService) -> None:
        self.knowledge = knowledge

    async def run(self, source: KnowledgeSource, since: datetime | None = None) -> IngestionReport:
        report = IngestionReport(source=source.key)
        async for item in source.fetch(since):
            if not item.content.strip():
                report.skipped += 1
                continue
            try:
                _, created, _ = await self.knowledge.add(source.to_memory(item), actor=f"ingest:{source.key}", log=False)
            except ValueError as exc:
                report.errors.append(f"{item.external_id}: {exc}")
                continue
            if created:
                report.created += 1
            else:
                report.updated += 1
        return report
