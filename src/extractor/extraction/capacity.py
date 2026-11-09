"""S05/S07 - How much one model call can hold.

Both the planner (which tasks may share a call) and the extraction step (how the evidence is cut into calls) ask
the same questions, so the arithmetic lives here once: what a passage costs, what the instructions cost, how many
evidence tokens are left, and how many answers fit in the model's output limit.
"""

import json
from typing import Callable

from ..adapters.base import ExtractorAdapter
from ..config.runtime import ExtractionSettings, PlanningSettings
from ..contracts.evidence import EvidencePassage
from ..contracts.task import TaskSpecification
from .prompts import extraction_prompt, group_extraction_prompt, render_passage


def passage_cost(extractor: ExtractorAdapter, show_document: bool) -> Callable[[EvidencePassage], int]:
    """Tokens one passage takes in a prompt (as it is rendered, with its heading line)."""
    return lambda p: extractor.count_tokens(render_passage("P00", p, show_document))


def prompt_overhead(tasks: list[TaskSpecification], settings: ExtractionSettings, extractor: ExtractorAdapter, show_document: bool) -> int:
    """Tokens of everything in a call except the passages: instructions, the questions, the answer schema."""
    prompt = (extraction_prompt(tasks[0], {}, settings.max_candidates, show_document) if len(tasks) == 1
              else group_extraction_prompt(tasks, {}, settings.max_candidates, show_document))
    return extractor.count_tokens(prompt.system + prompt.user + json.dumps(prompt.json_schema))


def evidence_budget(tasks: list[TaskSpecification], settings: ExtractionSettings, extractor: ExtractorAdapter, show_document: bool) -> int:
    """Evidence tokens one call can carry for these tasks (the context, minus the safety margin, the answer and the instructions)."""
    return int(extractor.context_tokens * (1 - settings.prompt_margin)) - extractor.output_tokens - prompt_overhead(
        tasks, settings, extractor, show_document)


def max_fields_per_call(planning: PlanningSettings, extractor: ExtractorAdapter) -> int:
    """Tasks one call may answer: the configured limit, and no more than the model's output limit holds."""
    return min(planning.max_fields_per_call, max(1, extractor.output_tokens // planning.output_tokens_per_field))
