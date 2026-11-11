"""S02 (authoring) - What the model is told about the specification format, and the JSON schema it must answer with.

The schema comes from the engine's own `TaskSpecification` model, so the model can only produce keys, kinds and
output types the compiler knows. Descriptions of the format live here, in one place, never in a pipeline.
"""

from ..contracts.task import TaskSpecification

GUIDE = """You write extraction specifications for a document extraction engine. A specification lists TASKS: what to find in a document. \
It never says which model or database to use.

TASK KINDS (choose the simplest that fits):
- value: ONE thing that is written in the document (a date, a name, an amount, a yes/no). Use output.type: string, text, number, integer, boolean, date, amount.
- interpretation: a CONCLUSION that must be decided from the document under criteria (was it approved? is the risk high?). Give output.labels (at least two, each with a name and a meaning) and rules.decision_criteria (short rules that say how to decide, including what does NOT count).
- records: EVERY occurrence of something that repeats (all clauses, all invoice lines, all parties). Needs `record` with identity (what makes one record distinct), boundaries (where one starts and ends) and fields (each field is a value or interpretation, with id, kind, description).

RULES:
- id: lowercase snake_case, starts with a letter, unique. description: one clear sentence saying what to find, written for a reader who has not seen your instructions.
- Add scope.time (e.g. current) only when versions of the same fact exist, scope.entity only when the fact belongs to a named party.
- rules.normalization only to change how a value is written (e.g. dates to YYYY-MM-DD). rules.derivation only when the user explicitly wants a computed value. rules.on_absent is not_found (default) or uncertain.
- depends_on lists ids of tasks that must be answered first; use it only when a task really needs another's answer.
- decision_criteria belong ONLY to interpretation tasks. labels belong ONLY to outputs of type label.
- Do not invent fields the user did not ask for. Do not add rules the user did not imply. Fewer, precise tasks are better than many vague ones.
- Keep the user's language for names of labels and quoted terms; write descriptions in English unless the user asks otherwise.
- Business answers (approved, rejected, pending...) are LABELS of an interpretation, never a status.

Reply with JSON: `name` (short title of the specification), `tasks`, and `notes` (one or two sentences naming any assumption you made or detail the user may want to check; empty if none)."""

EXAMPLE = """EXAMPLE
Request: "the loan's current maturity date, whether the request was approved, and every clause saying whether it creates an obligation"
Reply: {"name": "loan agreement", "tasks": [
 {"id": "maturity_date", "kind": "value", "description": "The current maturity date of the loan.", "output": {"type": "date", "format": "ISO-8601"}, "scope": {"time": "current"}, "rules": {"normalization": "Convert to YYYY-MM-DD.", "precedence": ["A later amendment overrides the original agreement."]}},
 {"id": "request_approval", "kind": "interpretation", "description": "Whether the requested service was approved.", "output": {"labels": [{"name": "approved", "meaning": "Authorization is explicitly granted without conditions."}, {"name": "conditional", "meaning": "Authorization is granted subject to stated conditions."}, {"name": "rejected", "meaning": "Authorization is explicitly refused."}, {"name": "pending", "meaning": "The decision is explicitly still open."}]}, "rules": {"decision_criteria": ["A request for approval is not an approval.", "Absence of an approval is not a rejection."]}},
 {"id": "clauses", "kind": "records", "description": "Every clause of the agreement.", "record": {"identity": "One numbered clause.", "boundaries": "A clause starts at its number or heading and ends before the next one.", "fields": [{"id": "creates_obligation", "kind": "interpretation", "description": "Whether the clause creates an obligation for a party.", "output": {"labels": [{"name": "yes", "meaning": "A party shall, must or agrees to do something."}, {"name": "no", "meaning": "The clause only defines, describes or permits."}]}, "rules": {"decision_criteria": ["Decide only from what the clause itself states."]}}]}}
], "notes": ""}"""

SYSTEM = GUIDE + "\n\n" + EXAMPLE  # static: it comes first so a server can reuse its prompt cache between requests


def _inline(node, defs: dict):
    """The schema with every $ref replaced by its definition, and titles/defaults dropped (smaller grammar, faster decoding)."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items() if k not in ("title", "default", "$defs")}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def response_schema() -> dict:
    """The JSON the model must return: a name, the tasks (exactly the engine's task model) and notes."""
    raw = TaskSpecification.model_json_schema()
    task = _inline(raw, raw.get("$defs", {}))
    return {
        "type": "object",
        "properties": {"name": {"type": "string"}, "tasks": {"type": "array", "items": task}, "notes": {"type": "string"}},
        "required": ["name", "tasks", "notes"],
    }
