"""S07 - The prompts: what the model is told, in one readable place.

To change how the model is asked, edit the text here. Nothing in this file
depends on which model answers.
"""

from ..contracts.evidence import EvidenceOrigin, EvidencePassage
from ..contracts.generation import ModelPrompt
from ..contracts.task import OutputType, TaskSpecification
from .schema import choice_schema, extraction_schema, verify_schema

EXTRACTION_SYSTEM = """You extract values from source passages for a document-analysis engine.

Rules:
- Report every value the passages STATE that answers the question. If several passages state different values \
(other amounts, other dates, an older and a newer version), report each one separately. Do not choose between them.
- "value" is the answer to the question and nothing more: only the name, number, date or short phrase the question \
asks for, never a whole sentence. It is copied word for word from the quote (you may leave surrounding words out, but never \
rewrite, convert or reformat it). If the question says what form the answer takes ("state name only", "number of days"), \
keep only the words that give that form.
- "quote" is copied exactly from the passage: the shortest stretch of text (usually one sentence) that contains the value.
- A field that is only printed as a label or left blank (for example "Contract No.:" with nothing after it, an underscore \
line, "[ ]") has NO value: do not report it.
- Check WHICH thing the question is about before you report a value: the initial term is not the renewal term, the name in \
the preamble is not the name in the signature block, the date of signing is not the effective date, a default rate is not \
the ordinary rate. If the passage is about a different thing, it is not a candidate.
- "passage" is the tag of the passage the quote comes from, e.g. P2.
- Report a value only if it answers the question AS ASKED. A value of a different kind (for example a penalty or default rate when the question asks for the ordinary rate) is NOT a candidate; neither is a passage that only mentions the topic without stating the value.
- Never guess and never calculate, unless the task explicitly allows derivation.
- "entity": who or what the value is about, as the text says. "date": the date or period it applies to, as the text \
says. "conditions": any condition, exception or qualification attached to the value. Use "" / [] when the text says nothing.
- A passage marked "may qualify" can change a value stated elsewhere (exception, amendment, condition): report both \
values; in "conditions" write what the passage actually says it changes or requires (never the marker itself).
- If no passage states the value, return an empty "candidates" list and explain in "absent_reason".
- Give each candidate a short unique "id" (k1, k2, ...).

Examples (invented text, to show the form of the answer):
  Question "State whose law governs the agreement; return state name only." Passage "This Agreement is governed by the laws \
of the State of Ohio." -> value "Ohio", quote "governed by the laws of the State of Ohio".
  Question "Notice period in days." Passage "...upon ninety (90) days' prior written notice..." -> value "ninety (90)", \
quote "upon ninety (90) days' prior written notice"."""

DERIVATION_RULES = """

Derivation is allowed for this task: {rule}
Only when the answer cannot be read directly but follows from values you reported as candidates, add an entry to \
"derived" naming the operation (add, subtract, multiply, divide, date_difference_days) and the ids of the candidates \
it uses, in order (subtract and divide: first minus / divided by second). The engine does the arithmetic."""

OUTPUT_HINTS = {
    OutputType.STRING: "text copied from the document",
    OutputType.TEXT: "a short text grounded in the passages",
    OutputType.NUMBER: "a number",
    OutputType.INTEGER: "a whole number",
    OutputType.BOOLEAN: "yes or no (as the passage states it)",
    OutputType.DATE: "a date",
    OutputType.AMOUNT: "an amount of money or a quantity with its currency or unit",
    OutputType.LABEL: "one of the allowed labels",
}


def passage_tags(passages: list[EvidencePassage]) -> dict[str, EvidencePassage]:
    """P1, P2... in source order: short names the model copies reliably (the engine maps them back)."""
    return {f"P{i}": p for i, p in enumerate(passages, start=1)}


def render_passage(tag: str, passage: EvidencePassage, show_document: bool) -> str:
    where = [passage.document_id.rsplit(":", 1)[-1]] if show_document else []
    if passage.pages:
        where.append("p." + (str(passage.pages[0]) if len(passage.pages) == 1 else f"{passage.pages[0]}-{passage.pages[-1]}"))
    if passage.heading_path:
        where.append("section: " + " > ".join(passage.heading_path))
    if passage.origin == EvidenceOrigin.QUALIFIER:
        where.append("may qualify other passages")
    return f"[{tag}] ({' · '.join(where)})\n{passage.text}"


def task_section(task: TaskSpecification) -> str:
    lines = [f"QUESTION: {task.description}"]
    if task.scope.entity:
        lines.append(f"About: {task.scope.entity}")
    if task.scope.time:
        lines.append(f"Time / period: {task.scope.time}")
    expected = OUTPUT_HINTS[task.output.type]
    if task.output.labels:
        expected += ": " + "; ".join(f"{label.name} = {label.meaning}" for label in task.output.labels)
    if task.output.cardinality.value == "many":
        expected += " (the question may have several answers: report each one)"
    lines.append(f"Expected value: {expected}")
    for hint in (task.rules.normalization, task.rules.ambiguity):
        if hint:
            lines.append(f"Note: {hint}")
    lines += [f"Evidence must: {r}" for r in task.rules.evidence_requirements]
    return "\n".join(lines)


def extraction_prompt(
    task: TaskSpecification, tagged: dict[str, EvidencePassage], max_candidates: int, show_document: bool
) -> ModelPrompt:
    system = EXTRACTION_SYSTEM
    if task.rules.derivation:
        system += DERIVATION_RULES.format(rule=task.rules.derivation)
    evidence = "\n\n".join(render_passage(tag, p, show_document) for tag, p in tagged.items())
    return ModelPrompt(
        system=system, user=f"{task_section(task)}\n\nPASSAGES:\n\n{evidence}", schema_name="extraction",
        json_schema=extraction_schema(task, list(tagged), bool(task.rules.derivation), max_candidates))


VERIFY_SYSTEM = """You check values that were extracted from a contract. For each candidate decide whether its quote really \
states THE value the field asks for.

Answer false when any of these is true:
- the quote is about a different thing than the field (a different date, party, amount, period, section or document);
- the value is only a label, a placeholder or a blank (for example "Contract No:");
- the value is a rule or a formula, not the value itself (for example "the day after the last signature");
- the quote does not actually contain the value;
- the value is in another form than the field asks for in a way that changes its meaning.
Answer true when the quote states the value the field asks for, even if more than one candidate does.
Judge only from the quote and the field definition. Give a short reason."""


CHOOSE_SYSTEM = """You settle a conflict between values extracted from the same documents.
Use ONLY the precedence rules you are given. Do not invent rules and do not use outside knowledge.
Answer with the number of the value the rules select, or 0 if the rules do not decide between them \
(for example when the information needed to apply a rule is missing)."""


def verify_prompt(task: TaskSpecification, candidates, headings: dict[str, list[str]]) -> ModelPrompt:
    listing = []
    for c in candidates:
        where = " > ".join(headings.get(c.passage_id or "", [])) or "no section"
        listing.append(f"CANDIDATE {c.candidate_id}\n  value: {c.source_value}\n  quote: {c.quote}\n  section: {where}"
                       + (f"\n  about: {c.entity}" if c.entity else "") + (f"\n  date: {c.date}" if c.date else ""))
    return ModelPrompt(
        system=VERIFY_SYSTEM, schema_name="verdicts", json_schema=verify_schema([c.candidate_id for c in candidates]),
        user=f"FIELD\n{task_section(task)}\n\n" + "\n\n".join(listing))


def choose_prompt(task: TaskSpecification, options: list[str]) -> ModelPrompt:
    rules = "\n".join(f"- {r}" for r in task.rules.precedence)
    ambiguity = f"\nIf the evidence is ambiguous: {task.rules.ambiguity}" if task.rules.ambiguity else ""
    listing = "\n\n".join(f"VALUE {i}:\n{text}" for i, text in enumerate(options, start=1))
    return ModelPrompt(
        system=CHOOSE_SYSTEM, schema_name="choice", json_schema=choice_schema(),
        user=f"QUESTION: {task.description}\n\nPRECEDENCE RULES:\n{rules}{ambiguity}\n\n{listing}")
