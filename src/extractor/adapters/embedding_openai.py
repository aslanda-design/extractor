"""Embedding adapter for any OpenAI-compatible `/embeddings` endpoint (API models, vLLM, TEI, Ollama...).

    models:
      embedding:
        name: openai_compatible
        params:
          base_url: https://api.openai.com/v1      # or http://localhost:8000/v1
          model: text-embedding-3-large
          api_key_env: OPENAI_API_KEY              # NAME of the environment variable, never the key
          # dimensions: 1024   batch_size: 64   query_prefix: ""   document_prefix: ""   timeout: 60

Uses only the standard library. Token counting is a 4-characters-per-token
estimate unless `tiktoken` is installed (then it uses the `encoding` param).
"""

import json
import os
import urllib.request
from typing import Sequence

from .base import AdapterCapabilities, AdapterContext, EmbedderAdapter, ModelIdentity


class OpenAICompatibleEmbedder(EmbedderAdapter):
    def __init__(
        self,
        context: AdapterContext | None = None,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "text-embedding-3-small",
        api_key_env: str | None = "OPENAI_API_KEY",
        dimensions: int | None = None,
        batch_size: int = 64,
        query_prefix: str = "",
        document_prefix: str = "",
        timeout: float = 60.0,
        encoding: str = "cl100k_base",
    ):
        super().__init__(context)
        self.base_url, self.model_name, self.api_key_env = base_url.rstrip("/"), model, api_key_env
        self.dimensions, self.batch_size, self.timeout = dimensions, batch_size, timeout
        self.query_prefix, self.document_prefix, self.encoding = query_prefix, document_prefix, encoding
        self._tokenizer = None

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(
            name=f"openai_compatible:{self.model_name}", revision="api",
            settings={"base_url": self.base_url, "dimensions": self.dimensions,
                      "query_prefix": self.query_prefix, "document_prefix": self.document_prefix})

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(supports_batching=True)

    def _post(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self.model_name, "input": texts}
        if self.dimensions:
            body["dimensions"] = self.dimensions
        headers = {"Content-Type": "application/json"}
        if self.api_key_env and os.environ.get(self.api_key_env):
            headers["Authorization"] = f"Bearer {os.environ[self.api_key_env]}"
        request = urllib.request.Request(f"{self.base_url}/embeddings", json.dumps(body).encode(), headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read())["data"]
        return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]

    def _embed_all(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out += self._post(list(texts[i:i + self.batch_size]))
        return out

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed_all([self.document_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed_all([self.query_prefix + text])[0]

    @property
    def dimension(self) -> int | None:
        return self.dimensions

    def count_tokens(self, text: str) -> int:
        if self._tokenizer is None:
            try:
                import tiktoken

                self._tokenizer = tiktoken.get_encoding(self.encoding)
            except Exception:  # not installed / unknown encoding: fall back to the estimate
                self._tokenizer = False
        if self._tokenizer:
            return len(self._tokenizer.encode(text))
        return super().count_tokens(text)
