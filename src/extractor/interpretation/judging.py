"""S08 step 6 - The judge: is the chosen label really what the quotes and the criteria give?

One extra call with the question, the criteria, every located quote (with the label it supports) and the chosen
label. It can only DOWNGRADE: a "no" turns the answer into `uncertain` (and names the label it prefers); it can
never add a label or a quote of its own. If the call fails, nothing is downgraded and the failure is logged.
"""

from dataclasses import dataclass

from ..adapters.base import ExtractorAdapter
from ..contracts.candidate import ModelCall, Stance
from ..contracts.task import TaskSpecification
from ..extraction.calling import call_model
from .prompts import judge_prompt
from .schema import NO_DECISION


@dataclass
class Judgement:
    approved: bool  # False only when the judge ran and disagreed
    better_label: str | None
    reason: str
    call: ModelCall | None
    ran: bool = True


def judge_label(
    task: TaskSpecification, label: str, stances: list[Stance], headings: dict[str, list[str]], model: ExtractorAdapter,
    retries: int, think: bool | None, tool_results: list[dict] | None = None,
) -> Judgement:
    call = call_model(model, judge_prompt(task, label, stances, headings, tool_results or []), "judge the label", retries, think, stage="S08")
    if call.reply.data is None:
        return Judgement(True, None, f"the judge could not run: {call.reply.error}", call, ran=False)
    data = call.reply.data
    if data.get("label_correct") is False:
        better = str(data.get("better_label") or "")
        allowed = {x.name for x in task.output.labels}
        return Judgement(False, better if better in allowed and better != label else None,
                         str(data.get("reason") or "no reason given").strip(), call)
    return Judgement(True, None, str(data.get("reason") or "").strip(), call)
