"""Shared vocabulary used by every contract (ARCHITECTURE.md sections 5 and 7).

The enums here are the single source of truth for task kinds, statuses and
evidence roles. Business answers (e.g. "rejected") are *values*, never statuses.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class TaskKind(StrEnum):
    VALUE = "value"
    INTERPRETATION = "interpretation"
    RECORDS = "records"


class TaskStatus(StrEnum):
    SUPPORTED = "supported"  # passed the acceptance policy
    NOT_FOUND = "not_found"  # required inspection completed, nothing found
    UNCERTAIN = "uncertain"  # ambiguity / conflict / inadequate coverage
    FAILED = "failed"  # technical failure; never converted to not_found


class Completeness(StrEnum):
    CHECKED = "checked"  # discovery procedure completed, no known gaps
    PARTIAL = "partial"  # some processed, known gaps remain
    UNKNOWN = "unknown"  # cannot be assessed yet


class EvidenceRole(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    CONTEXTUALIZES = "contextualizes"


class StrictModel(BaseModel):
    """Immutable, typo-intolerant base for all contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MutableModel(BaseModel):
    """For the few contracts that are built up over time (e.g. the ledger)."""

    model_config = ConfigDict(extra="forbid")


class SourceRef(StrictModel):
    """Points at a source block. Evidence always uses these, never model-invented pages."""

    document_id: str
    block_id: str
