"""Model/library adapters. Import a backend module here to register it."""

from . import fakes
from .base import Capability
from .registry import AdapterSet, build_adapters, register_adapter

register_adapter(Capability.PARSING, "fake", fakes.FakeParser)
register_adapter(Capability.EMBEDDING, "fake", fakes.FakeEmbedder)
register_adapter(Capability.RERANKING, "fake", fakes.FakeReranker)
register_adapter(Capability.EXTRACTION, "fake", fakes.FakeExtractor)
register_adapter(Capability.INTERPRETATION, "fake", fakes.FakeInterpreter)
register_adapter(Capability.VERIFICATION, "fake", fakes.FakeVerifier)

__all__ = ["AdapterSet", "Capability", "build_adapters", "register_adapter"]
