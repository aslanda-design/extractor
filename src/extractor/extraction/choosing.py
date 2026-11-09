"""S07 step 7 (part) - Settle a conflict between different values using ONLY the task's precedence rules.

The model never decides on its own authority: it applies the rules the YAML
gave, to the values already extracted. Its answer is a number that must be one
of the listed values; anything else (or 0 = "the rules do not decide") leaves the
conflict unresolved, and the task becomes `uncertain`. No rule is ever invented.
"""

from dataclasses import dataclass

from ..adapters.base import ExtractorAdapter
from ..contracts.candidate import ModelCall
from ..contracts.task import TaskSpecification
from .calling import call_model
from .prompts import choose_prompt
from .reconcile import Group, describe


@dataclass
class Choice:
    group: Group | None  # the group the rules selected; None = unresolved
    reason: str
    call: ModelCall


def choose_between(task: TaskSpecification, groups: list[Group], extractor: ExtractorAdapter, retries: int, think: bool | None = None) -> Choice:
    call = call_model(extractor, choose_prompt(task, [describe(g.merged()) for g in groups]),
                      "choose between conflicting values", retries, think)
    reply = call.reply
    if reply.data is None:
        return Choice(None, f"the model could not apply the precedence rules ({reply.error})", call)
    reason = str(reply.data.get("reason") or "").strip()
    try:
        number = int(reply.data.get("choice", 0))
    except (TypeError, ValueError):
        number = 0
    if not 1 <= number <= len(groups):
        return Choice(None, "the precedence rules do not decide between the values" + (f": {reason}" if reason else ""), call)
    return Choice(groups[number - 1], reason, call)
