"""Evidence layer of the Kiwi AI Investment Committee: build_evidence() assembles the pack the analysts reason from (see
pack.py for the rules), provider_status() reports each source's health for /committee status."""

from app.committee.evidence.common import EvidenceError
from app.committee.evidence.pack import build_evidence, provider_status

__all__ = ["EvidenceError", "build_evidence", "provider_status"]
