"""Adapter interfaces: the only place model/library-specific code may live.

Business logic asks for a *capability* (parse, embed, rerank, extract,
interpret, verify) and never names a model or vendor. Each adapter reports its
identity and limits so the engine can check compatibility before running.
Model-specific templates, tokenization and response envelopes stay inside it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from ..artifacts.store import ArtifactStore
from ..config.runtime import ParsingConfig
from ..contracts.candidate import Candidate
from ..contracts.common import StrictModel
from ..contracts.document import DocumentRepresentation
from ..contracts.evidence import EvidencePackage
from ..contracts.generation import ModelPrompt, ModelReply
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


@dataclass(frozen=True)
class AdapterContext:
    """Shared runtime objects every adapter receives when it is built."""

    parsing: ParsingConfig = field(default_factory=ParsingConfig)
    store: ArtifactStore | None = None


class Adapter(ABC):
    """Common surface of every adapter."""

    capability: Capability

    def __init__(self, context: AdapterContext | None = None):
        self.context = context or AdapterContext()

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
    """Turns text into vectors. Swap the model by changing the runtime config only.

    Implement `embed`. Override the others when the model needs it (query
    prefixes, a real tokenizer, a known dimension).
    """

    capability = Capability.EMBEDDING

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for passages (documents)."""

    def embed_query(self, text: str) -> list[float]:
        """Vector for a search query; models with asymmetric prompts override this."""
        return self.embed([text])[0]

    @property
    def dimension(self) -> int | None:
        """Vector size if known without running the model."""
        return None

    def count_tokens(self, text: str) -> int:
        """Tokens of `text` under this model. Chunk sizes are measured with it.

        Default: a rough 4-characters-per-token estimate.
        """
        return max(1, (len(text) + 3) // 4) if text else 0


class RerankerAdapter(Adapter):
    capability = Capability.RERANKING

    @abstractmethod
    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Relevance of each passage to the query (not answer correctness)."""


class ExtractorAdapter(Adapter):
    """A generative model that answers a prompt with JSON following a schema.

    The engine writes the prompt and decides what to do with the answer
    (extraction/); the adapter only talks to one model or server. Swap the
    model, runtime or provider by changing `models.extraction` in the runtime config.
    """

    capability = Capability.EXTRACTION

    @abstractmethod
    def generate(self, prompt: ModelPrompt) -> ModelReply:
        """Never raises for model/transport problems: they come back as `ModelReply.error`."""

    @property
    def context_tokens(self) -> int:
        """How many tokens (prompt + answer) one call can hold."""
        return self.capabilities.max_context_tokens or 8192

    @property
    def output_tokens(self) -> int:
        return self.capabilities.max_output_tokens or 1024

    def count_tokens(self, text: str) -> int:
        """Default: a rough 4-characters-per-token estimate."""
        return max(1, (len(text) + 3) // 4) if text else 0


class InterpreterAdapter(ExtractorAdapter):
    """The model behind S08 (interpretation). Same contract as the extractor: prompt + schema -> JSON.

    `models.interpretation` can name a different (bigger, reasoning) model than `models.extraction`; when it is left
    as `fake` the engine uses the extraction model for interpretation tasks."""

    capability = Capability.INTERPRETATION


class VerifierAdapter(ExtractorAdapter):
    """The model behind the optional semantic check (S10). Same contract as the extractor: prompt + schema -> JSON.

    `models.verification` can name a different (bigger) model than `models.extraction`; the same chat adapters
    (`ollama_chat`, `openai_compatible_chat`) are registered for both capabilities."""

    capability = Capability.VERIFICATION
