"""Adapter registry: maps (capability, name) from the runtime config to an implementation.

To add a backend: write the adapter class, then call `register_adapter(...)`
from the module that defines it, and import that module in `adapters/__init__.py`.
"""

from dataclasses import dataclass
from typing import Callable

from ..artifacts.store import ArtifactStore
from ..config.runtime import RuntimeConfig
from .base import (
    Adapter,
    AdapterContext,
    Capability,
    EmbedderAdapter,
    ExtractorAdapter,
    InterpreterAdapter,
    ParserAdapter,
    RerankerAdapter,
    VerifierAdapter,
)

AdapterFactory = Callable[..., Adapter]  # called as factory(context, **params)
_REGISTRY: dict[tuple[Capability, str], AdapterFactory] = {}


def register_adapter(capability: Capability, name: str, factory: AdapterFactory) -> None:
    _REGISTRY[(capability, name)] = factory


@dataclass(frozen=True)
class AdapterSet:
    """The concrete adapters chosen for one run."""

    parser: ParserAdapter
    embedder: EmbedderAdapter
    reranker: RerankerAdapter
    extractor: ExtractorAdapter
    interpreter: InterpreterAdapter
    verifier: VerifierAdapter

    def all(self) -> dict[Capability, Adapter]:
        return {
            Capability.PARSING: self.parser,
            Capability.EMBEDDING: self.embedder,
            Capability.RERANKING: self.reranker,
            Capability.EXTRACTION: self.extractor,
            Capability.INTERPRETATION: self.interpreter,
            Capability.VERIFICATION: self.verifier,
        }

    def model_labels(self) -> dict[str, str]:
        return {cap.value: a.identity.label() for cap, a in self.all().items()}


def _build(capability: Capability, config: RuntimeConfig, context: AdapterContext) -> Adapter:
    selection = getattr(config.models, capability.value)
    try:
        factory = _REGISTRY[(capability, selection.name)]
    except KeyError:
        known = sorted(n for c, n in _REGISTRY if c == capability)
        raise ValueError(
            f"no {capability.value} adapter named '{selection.name}' (registered: {known})"
        ) from None
    return factory(context, **selection.params)


def build_adapters(config: RuntimeConfig, store: ArtifactStore | None = None) -> AdapterSet:
    context = AdapterContext(parsing=config.parsing, store=store)
    return AdapterSet(
        parser=_build(Capability.PARSING, config, context),
        embedder=_build(Capability.EMBEDDING, config, context),
        reranker=_build(Capability.RERANKING, config, context),
        extractor=_build(Capability.EXTRACTION, config, context),
        interpreter=_build(Capability.INTERPRETATION, config, context),
        verifier=_build(Capability.VERIFICATION, config, context),
    )
