"""TaskResult: what the engine returns for each task (stage S13, semantics in section 7)."""

from typing import Any

from pydantic import model_validator

from .candidate import Interpretation
from .common import Completeness, EvidenceRole, SourceRef, StrictModel, TaskKind, TaskStatus
from .record import Record
from .validation import AcceptanceDecision, ValidationReport


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


class RecoveryAttempt(StrictModel):
    """One recovery try for a task (S12): what was done differently, what it cost and whether its result was kept."""

    action: str  # retry_call | widen_evidence | deepen | escalate_model | rediscover | refield | none
    reason: str  # the finding or outcome that triggered it
    change: str = ""  # what this attempt did differently, in plain language
    status_before: TaskStatus
    status_after: TaskStatus
    value_before: Any = None  # the accepted value before (None unless it was `supported`)
    value_after: Any = None  # the value this attempt proposed (accepted or not)
    kept: bool = False  # True = this attempt's result replaced the earlier one
    why: str = ""  # why it was kept or not ("accepted", "no improvement", "budget exhausted", "two attempts disagree")
    calls: int = 0  # model calls this attempt made (cached replies not counted)
    seconds: float = 0.0


class StageCost(StrictModel):
    """Model usage of one stage (or one task, or the whole run)."""

    calls: int = 0  # calls that reached a model
    cached: int = 0  # calls answered from the model-call cache
    failed: int = 0  # calls that returned no usable answer
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0  # time spent waiting for the model (cached calls cost none)


class RunSummary(StrictModel):
    """What a run cost and what it produced: the numbers a product owner asks first."""

    seconds: float = 0.0  # wall-clock time of the run
    phase_seconds: dict[str, float] = {}  # index | retrieve | extract | tasks
    statuses: dict[str, int] = {}  # supported / not_found / uncertain / failed -> number of tasks
    total: StageCost = StageCost()
    stages: dict[str, StageCost] = {}  # "S07", "S08", "S09", "S10"...
    tasks: dict[str, StageCost] = {}  # task id -> model usage
    recovery_attempts: int = 0
    recovered: int = 0  # tasks whose result a recovery attempt improved
    concurrency: int = 1


class TaskResult(StrictModel):
    task_id: str
    kind: TaskKind
    status: TaskStatus
    value: Any = None  # scalar tasks only
    records: list[Record] = []  # records tasks only
    completeness: Completeness | None = None  # records tasks only
    evidence: list[EvidenceLink] = []
    validation: ValidationReport = ValidationReport()
    acceptance: AcceptanceDecision | None = None  # why the engine gave this status (S11)
    alternatives: list[Any] = []  # tentative values live here, not in `value`
    scope_note: str = ""  # records tasks: where the records were searched ("pages 2-3, blocks B14-B40, located by the model")
    interpretation: Interpretation | None = None  # interpretation tasks: every quote by label, the justification, tool results (S08)
    recovery: list[RecoveryAttempt] = []  # S12: every recovery attempt, kept or not
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


RESULT_FORMAT = "extractor.run/1"


class RunReport(StrictModel):
    """The stable output of a run (`run --out result.json`): every task result plus what the run cost.

    `python -m extractor schema` prints the JSON Schema of this model."""

    format: str = RESULT_FORMAT
    specification: str | None = None  # the name in the YAML
    results: list[TaskResult] = []
    summary: RunSummary = RunSummary()
    execution: ExecutionMetadata = ExecutionMetadata()
