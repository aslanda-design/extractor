"""S08 - The JSON schemas the model must answer with (built from the task, never hand-written per model)."""

from ..contracts.task import TaskSpecification
from .tools import Tool

NO_DECISION = "none"  # the label the model gives when no passage states a decision
ROLES = ["supports", "supersedes"]


def interpretation_schema(task: TaskSpecification, tags: list[str], max_evidence: int, tools: list[Tool]) -> dict:
    labels = [label.name for label in task.output.labels]
    stance = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "passage": {"type": "string", "enum": tags},
            "quote": {"type": "string"},
            "label": {"type": "string", "enum": labels},
            "role": {"type": "string", "enum": ROLES},
        },
        "required": ["id", "passage", "quote", "label", "role"],
    }
    properties: dict = {
        "evidence": {"type": "array", "items": stance, "maxItems": max_evidence},
        "label": {"type": "string", "enum": [*labels, NO_DECISION]},
        "justification": {"type": "string"},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "unresolved": {"type": "array", "items": {"type": "string"}},
    }
    if tools:
        properties["tool_calls"] = {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {"tool": {"type": "string", "enum": [t.name for t in tools]}, "arguments": {"type": "string"}},
            "required": ["tool", "arguments"]}}
    return {"type": "object", "properties": properties, "required": list(properties)}


def judge_schema(labels: list[str]) -> dict:
    return {"type": "object",
            "properties": {"label_correct": {"type": "boolean"}, "better_label": {"type": "string", "enum": [*labels, NO_DECISION]},
                           "reason": {"type": "string"}},
            "required": ["label_correct", "better_label", "reason"]}
