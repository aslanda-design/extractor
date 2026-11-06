"""TaskSpecification: one normalized extraction task (stage S02).

These models are also the YAML schema: unknown keys are rejected, so a typo or
a runtime setting (model name, retry budget...) placed in a task is an error.
Runtime settings live in `config/runtime.py`, never here.
"""

from enum import StrEnum

from pydantic import model_validator

from .common import StrictModel, TaskKind


class OutputType(StrEnum):
    STRING = "string"  # text copied from the document
    TEXT = "text"  # generated text grounded in evidence (summaries)
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    AMOUNT = "amount"  # number plus currency/unit
    LABEL = "label"  # one of `labels`


class Cardinality(StrEnum):
    ONE = "one"
    MANY = "many"


class OnAbsent(StrEnum):
    NOT_FOUND = "not_found"
    UNCERTAIN = "uncertain"


class LabelSpec(StrictModel):
    name: str
    meaning: str


class OutputSpec(StrictModel):
    type: OutputType = OutputType.STRING
    cardinality: Cardinality = Cardinality.ONE
    labels: list[LabelSpec] = []
    required: bool = True  # required tasks always get a result entry
    format: str | None = None  # e.g. "ISO-8601" for dates

    @model_validator(mode="before")
    @classmethod
    def _infer_label_type(cls, data):
        # `labels:` without `type:` means a label output.
        if isinstance(data, dict) and "type" not in data and data.get("labels"):
            return {**data, "type": OutputType.LABEL}
        return data


class TaskScope(StrictModel):
    documents: list[str] = []  # filenames or document ids; empty = whole request scope
    sections: list[str] = []
    entity: str | None = None
    time: str | None = None


class Rules(StrictModel):
    normalization: str | None = None  # representation change only
    derivation: str | None = None  # None = derivation NOT permitted
    evidence_requirements: list[str] = []
    decision_criteria: list[str] = []  # interpretation tasks
    precedence: list[str] = []
    ambiguity: str | None = None  # what to do when evidence is ambiguous
    on_absent: OnAbsent = OnAbsent.NOT_FOUND


class FieldSpec(StrictModel):
    """A value/interpretation task evaluated once per record."""

    id: str
    kind: TaskKind = TaskKind.VALUE
    description: str
    output: OutputSpec = OutputSpec()
    rules: Rules = Rules()
    depends_on: list[str] = []  # sibling field ids only


class RecordSpec(StrictModel):
    """How repeated records are identified and what each record contains."""

    identity: str | None = None  # what makes one record distinct
    boundaries: str | None = None  # where a record starts and ends
    parent: str | None = None  # id of another `records` task (nesting)
    fields: list[FieldSpec] = []


class TaskSpecification(StrictModel):
    id: str
    kind: TaskKind
    description: str
    output: OutputSpec = OutputSpec()
    scope: TaskScope = TaskScope()
    rules: Rules = Rules()
    depends_on: list[str] = []
    record: RecordSpec | None = None  # required for kind=records, forbidden otherwise
