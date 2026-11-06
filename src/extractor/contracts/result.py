"""TaskResult: what the engine returns for each task (stage S13, semantics in section 7)."""

from typing import Any

from pydantic import model_validator

from .common import Completeness, EvidenceRole, SourceRef, StrictModel, TaskKind, TaskStatus
from .record import Record
from .validation import ValidationReport


class EvidenceLink(StrictModel):
    ref: SourceRef
    role: EvidenceRole
    quote: str | None = None


class ExecutionMetadata(StrictModel):
    """Enough to reproduce the run: inputs, schema, parser and model versions."""

    document_hashes: dict[str, str] = {}  # document_id -> sha256
    schema_hash: str = ""
    runtime_fingerprint: str = ""
    models: dict[str, str] = {}  # capability -> "name@revision"
    stage_versions: dict[str, str] = {}


class TaskResult(StrictModel):
    task_id: str
    kind: TaskKind
    status: TaskStatus
    value: Any = None  # scalar tasks only
    records: list[Record] = []  # records tasks only
    completeness: Completeness | None = None  # records tasks only
    evidence: list[EvidenceLink] = []
    validation: ValidationReport = ValidationReport()
    alternatives: list[Any] = []  # tentative values live here, not in `value`
    diagnostics: list[str] = []  # technical errors, kept apart from uncertainty
    execution: ExecutionMetadata = ExecutionMetadata()

    @model_validator(mode="after")
    def _check_semantics(self):
        if self.kind == TaskKind.RECORDS:
            if self.value is not None:
                raise ValueError("records tasks return `records`, not `value`")
            if self.completeness is None:
                raise ValueError("records tasks must declare `completeness`")
        else:
            if self.records or self.completeness is not None:
                raise ValueError("only records tasks have `records` / `completeness`")
            if self.status != TaskStatus.SUPPORTED and self.value is not None:
                raise ValueError("a scalar value is only returned with status 'supported'")
        return self
