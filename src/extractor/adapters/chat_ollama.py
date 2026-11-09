"""Chat-model adapter for a local Ollama server (native `/api/chat`).

    models:
      extraction:
        name: ollama_chat
        params:
          model: qwen3:8b
          base_url: http://localhost:11434
          context_tokens: 16384        # sent as num_ctx; Ollama's default (4096) would cut evidence off
          # max_output_tokens: 1024   temperature: 0   think: false   timeout: 600   keep_alive: 10m

Uses Ollama's native schema-constrained output (`format: <json schema>`), so the
answer always parses. `think: false` stops Qwen3 from spending tokens on a
reasoning preamble (the engine needs an auditable answer, not hidden reasoning).
If the prompt is longer than `context_tokens` Ollama truncates it silently; this
adapter detects that from the token counts and reports an error instead.
"""

import time

from ..contracts.generation import ModelPrompt, ModelReply
from .base import AdapterCapabilities, AdapterContext, ExtractorAdapter, ModelIdentity
from .chat_common import ChatError, post_json, read_json_object


class OllamaChat(ExtractorAdapter):
    def __init__(
        self,
        context: AdapterContext | None = None,
        *,
        model: str = "qwen3:8b",
        base_url: str = "http://localhost:11434",
        context_tokens: int = 16384,
        max_output_tokens: int = 1024,
        temperature: float = 0.0,
        seed: int | None = 0,
        think: bool = False,
        thinking_tokens: int = 4096,
        timeout: float = 600.0,
        keep_alive: str = "10m",
    ):
        super().__init__(context)
        self.model_name, self.base_url = model, base_url.rstrip("/")
        self._context_tokens, self._max_output = context_tokens, max_output_tokens
        self.temperature, self.seed, self.think = temperature, seed, think
        self.timeout, self.keep_alive, self.thinking_tokens = timeout, keep_alive, thinking_tokens

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(name=f"ollama_chat:{self.model_name}", revision="local",
                             settings={"base_url": self.base_url, "num_ctx": self._context_tokens,
                                       "temperature": self.temperature, "seed": self.seed, "think": self.think})

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(max_context_tokens=self._context_tokens, max_output_tokens=self._max_output,
                                   structured_output=True)

    def generate(self, prompt: ModelPrompt) -> ModelReply:
        think = self.think if prompt.think is None else prompt.think
        budget = self._max_output + (self.thinking_tokens if think else 0)  # reasoning tokens count against the answer limit
        options = {"num_ctx": self._context_tokens, "num_predict": budget, "temperature": self.temperature}
        if self.seed is not None:
            options["seed"] = self.seed
        body = {"model": self.model_name, "stream": False, "think": think, "keep_alive": self.keep_alive,
                "options": options,
                "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}]}
        if prompt.json_schema:
            body["format"] = prompt.json_schema
        started = time.time()
        try:
            answer = post_json(f"{self.base_url}/api/chat", body, {}, self.timeout)
        except ChatError as e:
            return ModelReply(error=str(e), seconds=round(time.time() - started, 2))
        raw = (answer.get("message") or {}).get("content") or ""
        reply = ModelReply(raw=raw, seconds=round(time.time() - started, 2),
                           prompt_tokens=answer.get("prompt_eval_count"), completion_tokens=answer.get("eval_count"))
        used = (reply.prompt_tokens or 0) + (reply.completion_tokens or 0)
        if reply.prompt_tokens and reply.prompt_tokens >= self._context_tokens - 8:
            return reply.model_copy(update={"error": f"the prompt filled the whole context window ({self._context_tokens} "
                                                      "tokens): evidence was probably cut off; raise context_tokens"})
        if answer.get("done_reason") == "length":
            return reply.model_copy(update={"error": f"the answer was cut off at {budget} tokens "
                                                      f"after {used} tokens; raise models.extraction.params.max_output_tokens"})
        try:
            return reply.model_copy(update={"data": read_json_object(raw)})
        except ValueError as e:
            return reply.model_copy(update={"error": f"invalid JSON from the model: {e}"})
