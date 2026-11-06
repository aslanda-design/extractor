"""Embedding adapter for any sentence-transformers / Hugging Face model, run locally.

Switch the model in the runtime config; no code changes:

    models:
      embedding:
        name: sentence_transformers
        params:
          model: Qwen/Qwen3-Embedding-0.6B      # or BAAI/bge-large-en-v1.5, a local folder, ...
          device: auto                          # auto | cuda | cpu
          dtype: float16                        # optional: float16 | bfloat16 | float32
          query_prompt_name: query              # model-defined prompt for queries (Qwen3, e5, bge...)
          # query_prefix: "Represent this sentence for searching relevant passages: "
          # document_prefix: ""
          # max_seq_length: 512   truncate_dim: 256   batch_size: 32   trust_remote_code: false

The index cache key includes this adapter's identity, so changing the model
(or any setting listed in `identity.settings`) rebuilds the vectors on the next run.
"""

from typing import Sequence

from ..contracts.common import StrictModel
from .base import AdapterCapabilities, AdapterContext, EmbedderAdapter, ModelIdentity

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"


class SentenceTransformersEmbedder(EmbedderAdapter):
    def __init__(
        self,
        context: AdapterContext | None = None,
        *,
        model: str = DEFAULT_MODEL,
        device: str = "auto",
        dtype: str | None = None,
        query_prompt_name: str | None = "query",
        query_prefix: str = "",
        document_prefix: str = "",
        max_seq_length: int | None = None,
        truncate_dim: int | None = None,
        batch_size: int = 32,
        normalize: bool = True,
        trust_remote_code: bool = False,
    ):
        super().__init__(context)
        self.model_name, self.device, self.dtype = model, device, dtype
        self.query_prompt_name, self.query_prefix, self.document_prefix = query_prompt_name, query_prefix, document_prefix
        self.max_seq_length, self.truncate_dim, self.batch_size = max_seq_length, truncate_dim, batch_size
        self.normalize, self.trust_remote_code = normalize, trust_remote_code
        self._model = None

    # --- identity (computable without loading the model) ---------------------------

    @property
    def identity(self) -> ModelIdentity:
        from importlib.metadata import PackageNotFoundError, version

        try:
            revision = f"st-{version('sentence-transformers')}"
        except PackageNotFoundError:
            revision = "st-not-installed"
        return ModelIdentity(
            name=f"sentence_transformers:{self.model_name}", revision=revision,
            settings={"dtype": self.dtype, "truncate_dim": self.truncate_dim, "normalize": self.normalize,
                      "query_prompt_name": self.query_prompt_name, "query_prefix": self.query_prefix,
                      "document_prefix": self.document_prefix, "max_seq_length": self.max_seq_length})

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(max_context_tokens=self.max_seq_length, supports_batching=True)

    # --- lazy model ------------------------------------------------------------------

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            kwargs = {"trust_remote_code": self.trust_remote_code}
            if self.device != "auto":
                kwargs["device"] = self.device
            if self.dtype:
                kwargs["model_kwargs"] = {"torch_dtype": self.dtype}
            if self.truncate_dim:
                kwargs["truncate_dim"] = self.truncate_dim
            self._model = SentenceTransformer(self.model_name, **kwargs)
            if self.max_seq_length:
                self._model.max_seq_length = self.max_seq_length
        return self._model

    # --- EmbedderAdapter ---------------------------------------------------------------

    def _encode(self, texts: Sequence[str], **kwargs) -> list[list[float]]:
        vectors = self.model.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=self.normalize,
                                    convert_to_numpy=True, show_progress_bar=False, **kwargs)
        return vectors.tolist()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([self.document_prefix + t for t in texts]) if texts else []

    def embed_query(self, text: str) -> list[float]:
        prompts = getattr(self.model, "prompts", {}) or {}
        if self.query_prompt_name and self.query_prompt_name in prompts:
            return self._encode([text], prompt_name=self.query_prompt_name)[0]
        return self._encode([self.query_prefix + text])[0]

    @property
    def dimension(self) -> int | None:
        return self.truncate_dim or self.model.get_sentence_embedding_dimension()

    def count_tokens(self, text: str) -> int:
        tokenizer = self.model.tokenizer
        return len(tokenizer(text, add_special_tokens=False)["input_ids"]) if text else 0
