"""S10 layer 1 - Structure: is the proposed value the kind of thing the task asked for?

Checks the declared output type, the allowed labels and the cardinality. Pure code.
"""

import re

from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.task import Cardinality, OutputType, TaskSpecification
from ..contracts.validation import Category, ValidationFinding, ValidationLayer
from .findings import finding

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_structure(task: TaskSpecification, proposal: ValueProposal) -> list[ValidationFinding]:
    if proposal.outcome != ProposalOutcome.FOUND:
        return []
    out = []

    def bad(message: str, category: Category = Category.BAD_TYPE):
        out.append(finding(ValidationLayer.STRUCTURE, category, message, task.id))

    if proposal.task_id != task.id:
        bad(f"the proposal belongs to task '{proposal.task_id}', not '{task.id}'")
    if not proposal.chosen:
        bad("a value was proposed but no candidate stands behind it", Category.NO_VALUE)
        return out
    values = proposal.value if isinstance(proposal.value, list) else [proposal.value]
    if task.output.cardinality == Cardinality.ONE and isinstance(proposal.value, list):
        bad("the task expects one value but several were proposed")
    if task.output.cardinality == Cardinality.MANY and not isinstance(proposal.value, list):
        bad("the task expects a list of values")
    if not values or any(v is None for v in values):
        bad("the proposed value is empty", Category.NO_VALUE)
    for value in values:
        problem = _type_problem(task, value)
        if problem:
            bad(problem)
    return out


def _type_problem(task: TaskSpecification, value) -> str | None:
    kind = task.output.type
    if value is None:
        return None
    if kind == OutputType.INTEGER and (isinstance(value, bool) or not isinstance(value, int)):
        return f"{value!r} is not a whole number"
    if kind == OutputType.NUMBER and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return f"{value!r} is not a number"
    if kind == OutputType.BOOLEAN and not isinstance(value, bool):
        return f"{value!r} is not a yes/no"
    if kind == OutputType.DATE and not (isinstance(value, str) and _ISO_DATE.match(value)):
        return f"{value!r} is not an ISO date"
    if kind == OutputType.AMOUNT and not (isinstance(value, dict) and isinstance(value.get("amount"), (int, float))):
        return f"{value!r} is not an amount"
    if kind == OutputType.LABEL and value not in {label.name for label in task.output.labels}:
        return f"{value!r} is not one of the allowed labels"
    if kind in (OutputType.STRING, OutputType.TEXT) and not (isinstance(value, str) and value.strip()):
        return f"{value!r} is not text"
    return None
