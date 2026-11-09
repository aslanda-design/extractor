"""Core contracts. Names may change; responsibilities must stay distinct.

One file per contract family:
    common.py         enums + base classes
    manifest.py       DocumentManifest            (S01)
    task.py           TaskSpecification           (S02)
    specification.py  YAML file + compiled output (S02)
    document.py       DocumentRepresentation      (S03/S04)
    evidence.py       EvidencePackage             (S06)
    candidate.py      Candidate / ValueProposal / interpretation  (S07/S08)
    generation.py     ModelPrompt / ModelReply    (S07+)
    passage.py        Passage / IndexedDocument   (S04)
    record.py         Record                      (S09)
    coverage.py       CoverageLedger              (S04+)
    validation.py     ValidationReport            (S10)
    result.py         TaskResult                  (S13)
"""

from .candidate import Candidate, InterpretationProposal, ModelCall, ProposalOutcome, ValueProposal
from .common import (
    Completeness,
    EvidenceRole,
    MutableModel,
    SourceRef,
    StrictModel,
    TaskKind,
    TaskStatus,
)
from .coverage import CoverageEntry, CoverageLedger, CoverageState
from .generation import ModelPrompt, ModelReply
from .document import DocumentRepresentation, ParsingIssue, SourceBlock
from .evidence import EvidenceItem, EvidenceOrigin, EvidencePackage, EvidencePassage, RetrievalTrace
from .manifest import DocumentEntry, DocumentManifest, DocumentRelationship, Scope
from .passage import Continuation, IndexedDocument, Passage, PassageSegment, Reference
from .record import FieldResult, Record
from .result import EvidenceLink, ExecutionMetadata, TaskResult
from .specification import CompiledSpecification, SpecificationFile, SpecIssue
from .task import TaskSpecification
from .validation import AcceptanceDecision, Category, RecoveryHint, ValidationFinding, ValidationReport

__all__ = [
    "Candidate", "InterpretationProposal", "ModelCall", "ModelPrompt", "ModelReply", "ProposalOutcome", "ValueProposal", "Completeness", "EvidenceRole", "MutableModel",
    "SourceRef", "StrictModel", "TaskKind", "TaskStatus", "CoverageEntry", "CoverageLedger",
    "CoverageState", "DocumentRepresentation", "ParsingIssue", "SourceBlock", "EvidenceItem",
    "EvidencePackage", "DocumentEntry", "DocumentManifest", "DocumentRelationship", "Scope",
    "FieldResult", "Record", "EvidenceLink", "ExecutionMetadata", "TaskResult",
    "CompiledSpecification", "SpecificationFile", "SpecIssue", "TaskSpecification",
    "AcceptanceDecision", "Category", "RecoveryHint", "ValidationFinding", "ValidationReport", "Continuation", "IndexedDocument", "Passage",
    "PassageSegment", "Reference", "EvidenceOrigin", "EvidencePassage", "RetrievalTrace",
]
