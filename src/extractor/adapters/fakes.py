"""Do-nothing adapters so the whole pipeline can run before any model exists.

They are honest: they never invent content. The fake parser reports that
nothing was parsed, the fake interpreter abstains, and so on.
"""

from pathlib import Path
from typing import Sequence

from ..contracts.generation import ModelPrompt, ModelReply
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
    """No model: every call reports that, so a run with it ends `failed`, never `not_found`."""

    identity = _FAKE

    def generate(self, prompt: ModelPrompt) -> ModelReply:
        return ModelReply(error="fake extractor: no model configured (set models.extraction in the runtime config)")


class FakeInterpreter(InterpreterAdapter):
    """No model: every call reports that (the engine then falls back to the extraction model)."""

    identity = _FAKE

    def generate(self, prompt: ModelPrompt) -> ModelReply:
        return ModelReply(error="fake interpreter: no model configured (set models.interpretation in the runtime config)")


class FakeVerifier(VerifierAdapter):
    """No model: the semantic check reports that it could not run."""

    identity = _FAKE

    def generate(self, prompt: ModelPrompt) -> ModelReply:
        return ModelReply(error="fake verifier: no model configured (set models.verification in the runtime config)")
