"""EvidencePackage: selected source content for a task (stage S06)."""

from .common import EvidenceRole, SourceRef, StrictModel


class EvidenceItem(StrictModel):
    ref: SourceRef
    role: EvidenceRole = EvidenceRole.SUPPORTS
    retrieval_provenance: list[str] = []  # e.g. ["lexical:3", "semantic:1", "rerank:2"]


class EvidencePackage(StrictModel):
    task_id: str
    items: list[EvidenceItem] = []
    gaps: list[str] = []  # content omitted (e.g. context limit) and still unprocessed
