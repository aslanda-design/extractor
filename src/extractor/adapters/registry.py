"""Adapter registry: maps (capability, name) from the runtime config to an implementation.

To add a backend: write the adapter class, then call `register_adapter(...)`
from the module that defines it, and import that module in `adapters/__init__.py`.
"""

from dataclasses import dataclass
from typing import Callable

from ..artifacts.store import ArtifactStore
from ..config.runtime import RuntimeConfig
from .fakes import FakeInterpreter, FakeVerifier
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


def registered_adapters(capability: Capability) -> list[str]:
    """Names usable in `models.<capability>.name` of the runtime config."""
    return sorted(name for cap, name in _REGISTRY if cap == capability)


@dataclass(frozen=True)
class AdapterSet:
    """The concrete adapters chosen for one run."""

    parser: ParserAdapter
    embedder: EmbedderAdapter
    reranker: RerankerAdapter
    extractor: ExtractorAdapter
    interpreter: InterpreterAdapter
    verifier: VerifierAdapter
    escalator: ExtractorAdapter | None = None  # `models.escalation`; None or a fake = recovery cannot escalate

    @property
    def interpretation_model(self) -> ExtractorAdapter:
        """The model S08 talks to: `models.interpretation`, or the extraction model while that one is left as `fake`."""
        return self.extractor if isinstance(self.interpreter, FakeInterpreter) else self.interpreter

    @property
    def escalation_model(self) -> ExtractorAdapter | None:
        """The stronger model recovery may ask, or None when `models.escalation` is left as `fake`."""
        return None if self.escalator is None or self.escalator.identity.name == "fake" else self.escalator

    def chat_models(self) -> list[ExtractorAdapter]:
        """Every generative model a run may call (the scheduler takes the smallest `max_parallel` of them)."""
        return [self.extractor, self.interpretation_model, *([] if isinstance(self.verifier, FakeVerifier) else [self.verifier])]

    def all(self) -> dict[Capability, Adapter]:
        if self.escalator is not None:
            return {**self._core(), Capability.ESCALATION: self.escalator}
        return self._core()

    def _core(self) -> dict[Capability, Adapter]:
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
        escalator=_build(Capability.ESCALATION, config, context),
    )
