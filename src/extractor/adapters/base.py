"""Adapter interfaces: the only place model/library-specific code may live.

Business logic asks for a *capability* (parse, embed, rerank, extract,
interpret, verify) and never names a model or vendor. Each adapter reports its
identity and limits so the engine can check compatibility before running.
Model-specific templates, tokenization and response envelopes stay inside it.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from ..contracts.candidate import Candidate, InterpretationProposal
from ..contracts.common import StrictModel
from ..contracts.document import DocumentRepresentation
from ..contracts.evidence import EvidencePackage
from ..contracts.manifest import DocumentEntry
from ..contracts.task import TaskSpecification
from ..contracts.validation import ValidationFinding


class Capability(StrEnum):
    PARSING = "parsing"
    EMBEDDING = "embedding"
    RERANKING = "reranking"
    EXTRACTION = "extraction"
    INTERPRETATION = "interpretation"
    VERIFICATION = "verification"


class ModelIdentity(StrictModel):
    name: str
    revision: str = "unversioned"
    settings: dict = {}  # quantization, device, ... (part of the runtime fingerprint)

    def label(self) -> str:
        return f"{self.name}@{self.revision}"


class AdapterCapabilities(StrictModel):
    modalities: list[str] = ["text"]  # "text", "image"
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None
    languages: list[str] = []  # empty = unknown
    structured_output: bool = False
    supports_batching: bool = False


class Adapter(ABC):
    """Common surface of every adapter."""

    capability: Capability

    @property
    @abstractmethod
    def identity(self) -> ModelIdentity: ...

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()


class ParserAdapter(Adapter):
    capability = Capability.PARSING

    @abstractmethod
    def parse(self, entry: DocumentEntry, source_path: Path) -> DocumentRepresentation: ...


class EmbedderAdapter(Adapter):
    capability = Capability.EMBEDDING

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class RerankerAdapter(Adapter):
    capability = Capability.RERANKING

    @abstractmethod
    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Relevance of each passage to the query (not answer correctness)."""


class ExtractorAdapter(Adapter):
    capability = Capability.EXTRACTION

    @abstractmethod
    def extract(self, task: TaskSpecification, evidence: EvidencePackage) -> list[Candidate]: ...


class InterpreterAdapter(Adapter):
    capability = Capability.INTERPRETATION

    @abstractmethod
    def interpret(
        self, task: TaskSpecification, evidence: EvidencePackage
    ) -> InterpretationProposal: ...


class VerifierAdapter(Adapter):
    capability = Capability.VERIFICATION

    @abstractmethod
    def verify(
        self,
        task: TaskSpecification,
        proposal: Candidate | InterpretationProposal,
        evidence: EvidencePackage,
    ) -> list[ValidationFinding]: ...
