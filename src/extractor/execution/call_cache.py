"""The model-call cache: a call that already produced a usable answer is never made again.

Why it exists: a corpus run is hundreds of calls and tens of minutes. Without it, a crash at call 190 of 200 repeats the 189
finished ones, and re-measuring after a change to ONE prompt repeats every other prompt too. With it, a re-run repeats only
the calls whose prompt, model or generation settings changed.

The key is a fingerprint of the model identity (name, revision, temperature, seed...), its limits (context and output size)
and the whole prompt (system, user, schema, thinking). Only usable answers are stored: a failed call is always tried again.

Honest limit: the cache assumes a call is repeatable (temperature 0, fixed seed). To measure run-to-run variation, turn it
off (`execution.cache_model_calls: false`, or `eval --no-cache`).
"""

from ..adapters.base import ExtractorAdapter
from ..artifacts.fingerprint import fingerprint
from ..artifacts.store import ArtifactStore
from ..contracts.generation import ModelPrompt, ModelReply

KIND = "model_calls"


class CallCache:
    def __init__(self, store: ArtifactStore):
        self.store = store

    def key(self, model: ExtractorAdapter, prompt: ModelPrompt) -> str:
        return fingerprint(model.identity, model.capabilities, prompt)

    def get(self, key: str) -> ModelReply | None:
        try:
            reply = self.store.get_model(KIND, key, ModelReply)
        except (ValueError, OSError):  # an unreadable entry is a miss, never an error
            return None
        return reply.model_copy(update={"cached": True}) if reply is not None and reply.data is not None else None

    def put(self, key: str, reply: ModelReply) -> None:
        if reply.data is not None:
            self.store.put(KIND, key, reply)
