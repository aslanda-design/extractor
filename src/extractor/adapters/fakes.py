"""Do-nothing adapters so the whole pipeline can run before any model exists.

They are honest: they never invent content. The fake parser reports that
nothing was parsed, the fake interpreter abstains, and so on.
"""

from pathlib import Path
from typing import Sequence

from ..contracts.candidate import Candidate, InterpretationProposal
from ..contracts.document import DocumentRepresentation, IssueKind, ParsingIssue
from ..contracts.evidence import EvidencePackage
from ..contracts.manifest import DocumentEntry
from ..contracts.task import TaskSpecification
from ..contracts.validation import ValidationFinding
from .base import (
    EmbedderAdapter,
    ExtractorAdapter,
    InterpreterAdapter,
    ModelIdentity,
    ParserAdapter,
    RerankerAdapter,
    VerifierAdapter,
)

_FAKE = ModelIdentity(name="fake", revision="0")


class FakeParser(ParserAdapter):
    identity = _FAKE

    def parse(self, entry: DocumentEntry, source_path: Path) -> DocumentRepresentation:
        issue = ParsingIssue(
            kind=IssueKind.NOT_PARSED,
            document_id=entry.document_id,
            message="fake parser: no content extracted",
        )
        return DocumentRepresentation(document_id=entry.document_id, issues=[issue])


class FakeEmbedder(EmbedderAdapter):
    identity = _FAKE

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


class FakeReranker(RerankerAdapter):
    identity = _FAKE

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [0.0 for _ in passages]


class FakeExtractor(ExtractorAdapter):
    identity = _FAKE

    def extract(self, task: TaskSpecification, evidence: EvidencePackage) -> list[Candidate]:
        return []


class FakeInterpreter(InterpreterAdapter):
    identity = _FAKE

    def interpret(
        self, task: TaskSpecification, evidence: EvidencePackage
    ) -> InterpretationProposal:
        return InterpretationProposal(label=None, unresolved_issues=["fake interpreter abstains"])


class FakeVerifier(VerifierAdapter):
    identity = _FAKE

    def verify(self, task, proposal, evidence) -> list[ValidationFinding]:
        return []
