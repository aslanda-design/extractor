"""S07 - The JSON schemas the model must answer with (built from the task, never hand-written per model)."""

from ..contracts.task import OutputType, TaskSpecification

DERIVATION_OPERATIONS = ["add", "subtract", "multiply", "divide", "date_difference_days"]


def extraction_schema(task: TaskSpecification, tags: list[str], allow_derivation: bool, max_candidates: int) -> dict:
    value: dict = {"type": "string"}
    if task.output.type == OutputType.LABEL and task.output.labels:
        value = {"type": "string", "enum": [label.name for label in task.output.labels]}
    candidate = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "passage": {"type": "string", "enum": tags},
            "quote": {"type": "string"},
            "value": value,
            "entity": {"type": "string"},
            "date": {"type": "string"},
            "conditions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "passage", "quote", "value", "entity", "date", "conditions"],
    }
    properties: dict = {
        "candidates": {"type": "array", "items": candidate, "maxItems": max_candidates},
        "absent_reason": {"type": "string"},
    }
    if allow_derivation:
        properties["derived"] = {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {"operation": {"type": "string", "enum": DERIVATION_OPERATIONS},
                           "operands": {"type": "array", "items": {"type": "string"}},
                           "explanation": {"type": "string"}},
            "required": ["operation", "operands", "explanation"]}}
    return {"type": "object", "properties": properties, "required": list(properties)}


def verify_schema(ids: list[str]) -> dict:
    verdict = {"type": "object",
               "properties": {"id": {"type": "string", "enum": ids}, "reason": {"type": "string"}, "answers_field": {"type": "boolean"}},
               "required": ["id", "reason", "answers_field"]}
    return {"type": "object", "properties": {"verdicts": {"type": "array", "items": verdict}}, "required": ["verdicts"]}


def choice_schema() -> dict:
    return {"type": "object",
            "properties": {"choice": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["choice", "reason"]}


def group_schema(tasks: list[TaskSpecification], tags: list[str], max_candidates: int) -> dict:
    """One answer for SEVERAL tasks: `fields` has one required entry per task id, each with the single-task answer shape.

    An object with one property per task (not an array) so the answer cannot skip, repeat or reorder a field, and
    every task keeps its own `value` enum when it is a label task. Derivation is never offered in a group."""
    per_task = {task.id: extraction_schema(task, tags, False, max_candidates) for task in tasks}
    return {"type": "object",
            "properties": {"fields": {"type": "object", "properties": per_task, "required": list(per_task)}},
            "required": ["fields"]}
