"""S09 step 1 - Scope: which blocks of the document may hold the records?

A model reads the document OUTLINE (every block shortened) and names the first and last block; the engine checks that both exist and
are in order, adds a small margin, and falls back to the WHOLE document (and says so) when the answer is unusable or the outline is too
big for one call. A wrong region is the worst error for a collection (records outside it are never seen), so the fallback is the whole
document, never a guess.
"""

from dataclasses import dataclass, field

from ..adapters.base import ExtractorAdapter
from ..config.runtime import RecordSettings
from ..contracts.candidate import ModelCall
from ..contracts.task import TaskSpecification
from ..extraction.calling import call_model
from .outline import Tagged, tag_index
from .prompts import scope_prompt


@dataclass
class Scope:
    first: int  # block indexes, inclusive
    last: int
    how: str  # "model: ..." | "whole document (why)"
    calls: list[ModelCall] = field(default_factory=list)
    fell_back: bool = False


def find_scope(task: TaskSpecification, tagged: list[Tagged], model: ExtractorAdapter, settings: RecordSettings) -> Scope:
    first, last = 0, len(tagged) - 1
    if not settings.scope_with_model:
        return Scope(first, last, "whole document (scope_with_model is off)")
    whole_tokens = sum(model.count_tokens(t.render()) + 8 for t in tagged)
    window = int(model.context_tokens * (1 - settings.prompt_margin)) - model.output_tokens
    if whole_tokens <= window * (settings.scope_min_windows - 1):
        return Scope(first, last, f"whole document (~{whole_tokens} tokens: read entirely, no region to guess)")
    outline = [t.render(settings.outline_chars_per_block) for t in tagged]
    prompt = scope_prompt(task, outline)
    budget = int(model.context_tokens * (1 - settings.prompt_margin)) - model.output_tokens
    if model.count_tokens(prompt.system + prompt.user) > budget:
        return Scope(first, last, "whole document (the outline does not fit one call)", fell_back=True)
    call = call_model(model, prompt, "locate the region", settings.retries, settings.think_discovery, stage="S09")
    if call.reply.data is None:
        return Scope(first, last, f"whole document (the scope call failed: {call.reply.error})", [call], True)
    a, b = tag_index(call.reply.data.get("first_block"), len(tagged)), tag_index(call.reply.data.get("last_block"), len(tagged))
    if a is None or b is None or a > b:
        return Scope(first, last, "whole document (the model named an invalid region)", [call], True)
    reason = str(call.reply.data.get("reason") or "").strip()
    return Scope(max(0, a - settings.scope_margin_blocks), min(len(tagged) - 1, b + settings.scope_margin_blocks),
                 f"model: B{a}-B{b} (+{settings.scope_margin_blocks} margin): {reason}", [call])
