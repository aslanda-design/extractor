"""Reranker adapter for any sentence-transformers CrossEncoder, run locally.

Switch the model in the runtime config; no code changes:

    models:
      reranking:
        name: cross_encoder
        params:
          model: tomaarsen/Qwen3-Reranker-0.6B-seq-cls   # or BAAI/bge-reranker-v2-m3, a local folder, ...
          prompt_format: qwen3        # qwen3 = Qwen3-Reranker's chat prompt | plain = (query, passage) as is
          # instruction: "..."        # qwen3 only: what "relevant" means for this job
          # device: auto   dtype: float16   max_length: 2048   batch_size: 16

Scores are relevance judgements in [0, 1] (the model's yes-probability), NOT
the probability that an answer is correct.
"""

from typing import Sequence

from .base import AdapterCapabilities, AdapterContext, ModelIdentity, RerankerAdapter

DEFAULT_MODEL = "tomaarsen/Qwen3-Reranker-0.6B-seq-cls"
DEFAULT_INSTRUCTION = (
    "Given a description of information to find in a document, judge whether the passage "
    "contains information needed to answer it"
)
_QWEN3_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
    'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
_QWEN3_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


class CrossEncoderReranker(RerankerAdapter):
    def __init__(
        self,
        context: AdapterContext | None = None,
        *,
        model: str = DEFAULT_MODEL,
        prompt_format: str = "qwen3",
        instruction: str = DEFAULT_INSTRUCTION,
        device: str = "auto",
        dtype: str | None = None,
        max_length: int | None = 2048,
        batch_size: int = 16,
        trust_remote_code: bool = False,
    ):
        super().__init__(context)
        if prompt_format not in ("qwen3", "plain"):
            raise ValueError("prompt_format must be 'qwen3' or 'plain'")
        self.model_name, self.prompt_format, self.instruction = model, prompt_format, instruction
        self.device, self.dtype, self.max_length = device, dtype, max_length
        self.batch_size, self.trust_remote_code = batch_size, trust_remote_code
        self._model = None

    @property
    def identity(self) -> ModelIdentity:
        from importlib.metadata import PackageNotFoundError, version

        try:
            revision = f"st-{version('sentence-transformers')}"
        except PackageNotFoundError:
            revision = "st-not-installed"
        return ModelIdentity(
            name=f"cross_encoder:{self.model_name}", revision=revision,
            settings={"prompt_format": self.prompt_format, "instruction": self.instruction,
                      "dtype": self.dtype, "max_length": self.max_length})

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(max_context_tokens=self.max_length, supports_batching=True)

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            kwargs = {"trust_remote_code": self.trust_remote_code}
            if self.device != "auto":
                kwargs["device"] = self.device
            if self.max_length:
                kwargs["max_length"] = self.max_length
            if self.dtype:
                kwargs["model_kwargs"] = {"torch_dtype": self.dtype}
            self._model = CrossEncoder(self.model_name, **kwargs)
        return self._model

    def _pair(self, query: str, passage: str) -> list[str]:
        if self.prompt_format == "qwen3":
            return [f"{_QWEN3_PREFIX}<Instruct>: {self.instruction}\n<Query>: {query}\n",
                    f"<Document>: {passage}{_QWEN3_SUFFIX}"]
        return [query, passage]

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        pairs = [self._pair(query, p) for p in passages]
        scores = self.model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        return [float(s) for s in scores]
