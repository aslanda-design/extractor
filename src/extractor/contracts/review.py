"""Review: what a person decided about a prediction (stage S13). Kept apart from the prediction, always.

A review never changes a `TaskResult`. It points at one thing the engine said (a task, a record, or one field of a record),
carries a SNAPSHOT of what the engine said at that moment, and says what the person decided:

  accept   the engine's answer is right
  correct  the engine's answer is wrong or incomplete; `value` is the right one
  reject   there is no right answer in the document for this (the engine should have said `not_found`)

Reviews are append-only: a later review of the same target supersedes an earlier one without deleting it.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from .common import StrictModel, TaskStatus


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    CORRECT = "correct"
    REJECT = "reject"


class ReviewTarget(StrictModel):
    task_id: str
    record_key: str | None = None  # a records task: which record (its key as the document prints it)
    field_id: str | None = None  # ...and which field of it; None = the whole record (or the whole task)

    def label(self) -> str:
        return " / ".join(x for x in (self.task_id, self.record_key, self.field_id) if x)

    @model_validator(mode="after")
    def _field_needs_record(self):
        if self.field_id and not self.record_key:
            raise ValueError("a field of a record is named by the record's key and the field id")
        return self


class Prediction(StrictModel):
    """What the engine said about the target when the review was made."""

    status: TaskStatus
    value: Any = None
    digest: str  # fingerprint of the status and the value: a later change of the prediction is detected by it


class Review(StrictModel):
    review_id: str
    run_id: str
    target: ReviewTarget
    decision: ReviewDecision
    value: Any = None  # the right value (decision = correct)
    note: str = ""
    reviewer: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    prediction: Prediction

    @model_validator(mode="after")
    def _correct_has_a_value(self):
        if self.decision == ReviewDecision.CORRECT and self.value is None:
            raise ValueError("a correction must say the right value")
        if self.decision != ReviewDecision.CORRECT and self.value is not None:
            raise ValueError("only a correction carries a value")
        return self


class ReviewedItem(StrictModel):
    """The answer a person ends up with for one target: the engine's, or the reviewer's."""

    target: ReviewTarget
    source: str  # engine | human
    status: TaskStatus
    value: Any = None
    decision: ReviewDecision | None = None
    review_id: str | None = None
    stale: bool = False  # the engine's prediction is no longer the one that was reviewed
    note: str = ""
    engine_status: TaskStatus
    engine_value: Any = None  # always kept: what the engine said
