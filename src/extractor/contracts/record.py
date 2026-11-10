"""Record: one discovered occurrence for a `records` task (stage S09)."""

from typing import Any

from .common import SourceRef, StrictModel, TaskStatus


class FieldResult(StrictModel):
    field_id: str
    status: TaskStatus
    value: Any = None
    evidence: list[SourceRef] = []


class Record(StrictModel):
    record_id: str  # stable, derived from source location
    key: str = ""  # the identity as the document prints it ("(c)", "Stage 4", "4.1(iii)")
    text: str = ""  # the source span the record was read from (what its fields were extracted from)
    block_ids: list[str] = []  # boundaries in the source view
    parent_id: str | None = None
    fields: list[FieldResult] = []
