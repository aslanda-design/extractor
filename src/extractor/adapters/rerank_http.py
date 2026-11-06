"""Reranker adapter for Cohere / Jina / vLLM-style `/rerank` endpoints (API models in production).

    models:
      reranking:
        name: http_rerank
        params:
          base_url: https://api.cohere.com/v2        # or http://my-vllm-host:8000/v1
          model: rerank-v3.5
          api_key_env: COHERE_API_KEY                # NAME of the environment variable, never the key
          # endpoint: /rerank   timeout: 60

Request: {"model", "query", "documents": [...]}. Response: {"results": [{"index", "relevance_score"}]}.
Standard library only.
"""

import json
import os
import urllib.request
from typing import Sequence

from .base import AdapterCapabilities, AdapterContext, ModelIdentity, RerankerAdapter


class HttpReranker(RerankerAdapter):
    def __init__(
        self,
        context: AdapterContext | None = None,
        *,
        base_url: str,
        model: str,
        api_key_env: str | None = None,
        endpoint: str = "/rerank",
        timeout: float = 60.0,
    ):
        super().__init__(context)
        self.base_url, self.model_name, self.api_key_env = base_url.rstrip("/"), model, api_key_env
        self.endpoint, self.timeout = endpoint, timeout

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(name=f"http_rerank:{self.model_name}", revision="api",
                             settings={"base_url": self.base_url, "endpoint": self.endpoint})

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(supports_batching=True)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key_env and os.environ.get(self.api_key_env):
            headers["Authorization"] = f"Bearer {os.environ[self.api_key_env]}"
        body = json.dumps({"model": self.model_name, "query": query, "documents": list(passages)}).encode()
        request = urllib.request.Request(f"{self.base_url}{self.endpoint}", body, headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            results = json.loads(response.read())["results"]
        scores = [0.0] * len(passages)
        for item in results:
            scores[item["index"]] = float(item["relevance_score"])
        return scores
