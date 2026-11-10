"""S07 - One model call, retried when it fails, kept whole for inspection."""

from ..adapters.base import ExtractorAdapter
from ..contracts.candidate import ModelCall
from ..contracts.generation import ModelPrompt
from ..events import emit


def call_model(
    extractor: ExtractorAdapter, prompt: ModelPrompt, purpose: str, retries: int, think: bool | None = None, stage: str = "S07",
) -> ModelCall:
    """The last reply after at most `retries` extra attempts (a reply without data counts as failed)."""
    if think is not None:
        prompt = prompt.model_copy(update={"think": think})
    emit(stage, f"asking the model: {purpose}" + (" (thinking)" if think else ""))
    reply = extractor.generate(prompt)
    for attempt in range(1, retries + 1):
        if reply.data is not None:
            break
        emit(stage, f"{purpose}: no usable answer ({reply.error}); retry {attempt}/{retries}", "warn")
        reply = extractor.generate(prompt)
    if reply.data is None:
        emit(stage, f"{purpose}: failed ({reply.error})", "error")
    else:
        emit(stage, f"{purpose}: answered", "ok")
    return ModelCall(purpose=purpose, prompt=prompt, reply=reply)
