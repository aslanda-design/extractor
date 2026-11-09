"""Model/library adapters. Import a backend module here to register it."""

from . import fakes
from .base import Capability
from .registry import AdapterSet, build_adapters, register_adapter, registered_adapters
from ..parsing.layered_parser import LayeredParser  # noqa: E402  (after .base to avoid a cycle)
from .chat_ollama import OllamaChat
from .chat_openai import OpenAICompatibleChat
from .embedding_openai import OpenAICompatibleEmbedder
from .embedding_sentence_transformers import SentenceTransformersEmbedder
from .rerank_cross_encoder import CrossEncoderReranker
from .rerank_http import HttpReranker

register_adapter(Capability.PARSING, "fake", fakes.FakeParser)
register_adapter(Capability.PARSING, "layered", LayeredParser)
register_adapter(Capability.EMBEDDING, "fake", fakes.FakeEmbedder)
register_adapter(Capability.EMBEDDING, "sentence_transformers", SentenceTransformersEmbedder)
register_adapter(Capability.EMBEDDING, "openai_compatible", OpenAICompatibleEmbedder)
register_adapter(Capability.RERANKING, "fake", fakes.FakeReranker)
register_adapter(Capability.RERANKING, "cross_encoder", CrossEncoderReranker)
register_adapter(Capability.RERANKING, "http_rerank", HttpReranker)
register_adapter(Capability.EXTRACTION, "fake", fakes.FakeExtractor)
register_adapter(Capability.EXTRACTION, "ollama_chat", OllamaChat)
register_adapter(Capability.EXTRACTION, "openai_compatible_chat", OpenAICompatibleChat)
register_adapter(Capability.INTERPRETATION, "fake", fakes.FakeInterpreter)
register_adapter(Capability.VERIFICATION, "fake", fakes.FakeVerifier)

__all__ = ["AdapterSet", "Capability", "build_adapters", "register_adapter", "registered_adapters"]
