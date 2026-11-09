"""Proposals produced by extraction strategies (stages S07/S08)."""

from enum import StrEnum
from typing import Any

from .common import SourceRef, StrictModel
from .generation import ModelPrompt, ModelReply


class Candidate(StrictModel):
    """A value proposed from evidence. Extraction, normalization, derivation stay distinct."""

    source_value: str | None  # literal text as written in the source
    normalized_value: Any = None  # same meaning, canonical representation
    derived: bool = False  # True only if the task permitted derivation
    evidence: list[SourceRef] = []
    qualifications: list[str] = []  # conditions, entity, date, ambiguity
    candidate_id: str = ""  # "k1", "k2"... within one extraction
    quote: str | None = None  # verbatim source sentence the model pointed at
    passage_id: str | None = None  # the passage that quote was found in
    entity: str | None = None  # whom/what the value is about, as the text says
    date: str | None = None  # the date/period the value applies to, as the text says
    flags: list[str] = []  # engine observations: quote_not_located, not_normalized, ...
    derivation: str | None = None  # "k1 - k2" when the value was computed


class InterpretationProposal(StrictModel):
    label: str | None  # allowed label or generated answer; None = abstain
    justification: str = ""  # auditable explanation, not hidden reasoning
    citations: list[SourceRef] = []
    unresolved_issues: list[str] = []


class ProposalOutcome(StrEnum):
    FOUND = "found"  # one value chosen (or the several values of a `many` task)
    ABSENT = "absent"  # inspected and nothing supports a value (still not accepted: phase 6)
    UNCERTAIN = "uncertain"  # candidates conflict, or absence cannot be trusted
    FAILED = "failed"  # the model call or its output was unusable


class ModelCall(StrictModel):
    """One call to the model, kept whole so a person can read what was asked and answered."""

    purpose: str  # "extract 1/2", "choose among candidates"
    prompt: ModelPrompt
    reply: ModelReply


class ValueProposal(StrictModel):
    """S07's answer for one value task: a proposal, never an accepted result."""

    task_id: str
    outcome: ProposalOutcome
    value: Any = None  # the chosen normalized (or literal) value; a list for cardinality `many`
    chosen: list[Candidate] = []  # the candidate(s) behind `value`
    candidates: list[Candidate] = []  # everything the model reported, after normalization
    reason: str = ""  # why this outcome (one sentence)
    steps: list[str] = []  # reconciliation log, in order
    calls: list[ModelCall] = []
    gaps: list[str] = []  # evidence the model never saw (budget) or that retrieval flagged
