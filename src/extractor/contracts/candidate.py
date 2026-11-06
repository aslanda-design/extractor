"""Proposals produced by extraction strategies (stages S07/S08)."""

from typing import Any

from .common import SourceRef, StrictModel


class Candidate(StrictModel):
    """A value proposed from evidence. Extraction, normalization, derivation stay distinct."""

    source_value: str | None  # literal text as written in the source
    normalized_value: Any = None  # same meaning, canonical representation
    derived: bool = False  # True only if the task permitted derivation
    evidence: list[SourceRef] = []
    qualifications: list[str] = []  # conditions, entity, date, ambiguity


class InterpretationProposal(StrictModel):
    label: str | None  # allowed label or generated answer; None = abstain
    justification: str = ""  # auditable explanation, not hidden reasoning
    citations: list[SourceRef] = []
    unresolved_issues: list[str] = []
