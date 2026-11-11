"""Chat-model adapter for any OpenAI-compatible `/chat/completions` endpoint.

Works with hosted APIs, vLLM, LM Studio, llama.cpp server... Moving from a local
model to a bigger or hosted one only changes these params:

    models:
      extraction:
        name: openai_compatible_chat
        params:
          base_url: https://api.openai.com/v1      # or http://localhost:8000/v1
          model: gpt-4.1
          api_key_env: OPENAI_API_KEY              # NAME of the environment variable, never the key
          context_tokens: 128000                   # what the model can read + write in one call
          # max_output_tokens: 1024   temperature: 0   timeout: 300   seed: 0
          # extra_body: {chat_template_kwargs: {enable_thinking: false}}   # server-specific options

The reply is forced into the engine's JSON schema (`response_format: json_schema`).
If the server rejects that, the schema is put into the prompt instead and the
answer is parsed (still validated by the engine). For Ollama prefer `ollama_chat`:
Ollama's OpenAI endpoint ignores the context size, so long evidence would be cut silently.
"""

import os
import time

from ..contracts.generation import ModelPrompt, ModelReply
from .base import AdapterCapabilities, AdapterContext, ExtractorAdapter, ModelIdentity
from .chat_common import ChatError, post_json, read_json_object, schema_instruction


class OpenAICompatibleChat(ExtractorAdapter):
    def __init__(
        self,
        context: AdapterContext | None = None,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4.1-mini",
        api_key_env: str | None = "OPENAI_API_KEY",
        context_tokens: int = 32768,
        max_output_tokens: int = 1024,
        temperature: float = 0.0,
        seed: int | None = 0,
        timeout: float = 300.0,
        max_parallel: int = 4,
        extra_body: dict | None = None,
        encoding: str = "cl100k_base",
    ):
        super().__init__(context)
        self._max_parallel = max(1, max_parallel)
        self.base_url, self.model_name, self.api_key_env = base_url.rstrip("/"), model, api_key_env
        self._context_tokens, self._max_output = context_tokens, max_output_tokens
        self.temperature, self.seed, self.timeout = temperature, seed, timeout
        self.extra_body, self.encoding = extra_body or {}, encoding
        self._tokenizer = None
        self._schema_supported = True  # flips to False if the server rejects json_schema

    @property
    def max_parallel(self) -> int:
        return self._max_parallel

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(name=f"openai_compatible_chat:{self.model_name}", revision="api",
                             settings={"base_url": self.base_url, "temperature": self.temperature,
                                       "seed": self.seed, "extra_body": self.extra_body})

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(max_context_tokens=self._context_tokens, max_output_tokens=self._max_output,
                                   structured_output=True)

    def count_tokens(self, text: str) -> int:
        if self._tokenizer is None:
            try:
                import tiktoken

                self._tokenizer = tiktoken.get_encoding(self.encoding)
            except Exception:
                self._tokenizer = False
        return len(self._tokenizer.encode(text)) if self._tokenizer else super().count_tokens(text)

    def _body(self, prompt: ModelPrompt, enforce_schema: bool) -> dict:
        system = prompt.system
        if prompt.json_schema and not enforce_schema:
            system += "\n\n" + schema_instruction(prompt.json_schema)
        body = {"model": self.model_name, "temperature": self.temperature, "max_tokens": self._max_output,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt.user}],
                **self.extra_body}
        if self.seed is not None:
            body["seed"] = self.seed
        if prompt.json_schema:
            body["response_format"] = (
                {"type": "json_schema", "json_schema": {"name": prompt.schema_name, "strict": True,
                                                        "schema": prompt.json_schema}}
                if enforce_schema else {"type": "json_object"})
        return body

    def _call(self, prompt: ModelPrompt, enforce_schema: bool) -> dict:
        headers = {}
        if self.api_key_env and os.environ.get(self.api_key_env):
            headers["Authorization"] = f"Bearer {os.environ[self.api_key_env]}"
        return post_json(f"{self.base_url}/chat/completions", self._body(prompt, enforce_schema), headers, self.timeout)

    def generate(self, prompt: ModelPrompt) -> ModelReply:
        started = time.time()
        try:
            try:
                answer = self._call(prompt, self._schema_supported and prompt.json_schema is not None)
            except ChatError as e:
                if e.status not in (400, 422) or not prompt.json_schema or not self._schema_supported:
                    raise
                self._schema_supported = False  # the server does not know json_schema: ask in words
                answer = self._call(prompt, False)
        except ChatError as e:
            return ModelReply(error=str(e), failure="transport", seconds=time.time() - started)
        choice = answer["choices"][0]
        raw = choice["message"].get("content") or ""
        usage = answer.get("usage") or {}
        reply = ModelReply(raw=raw, seconds=round(time.time() - started, 2),
                           prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
        if choice.get("finish_reason") == "length":
            return reply.model_copy(update={"error": "the answer was cut off at max_output_tokens "
                                                      f"({self._max_output}); raise models.extraction.params.max_output_tokens", "failure": "truncated"})
        try:
            return reply.model_copy(update={"data": read_json_object(raw)})
        except ValueError as e:
            return reply.model_copy(update={"error": f"invalid JSON from the model: {e}", "failure": "invalid_json"})
