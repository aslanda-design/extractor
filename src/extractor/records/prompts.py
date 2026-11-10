"""S09 - What the model is told when it must FIND records (scope, then boundaries), in one readable place."""

from ..contracts.generation import ModelPrompt
from ..contracts.task import TaskSpecification
from .outline import Tagged
from .schema import discover_schema, scope_schema

SCOPE_SYSTEM = """You help find where a contract lists a collection of records. You get the OUTLINE of the document (every block, \
shortened, with its tag, page and kind) and a description of the records. Say the first and the last block of the region that \
contains ALL the records described (headings and the text under them included), and nothing from other sections. Use the region the \
description names ("clauses 12.1 and 12.2", "the preamble", "Article V"); when the description does not name one, take the smallest \
region that contains every record. Give a one-sentence reason."""

DISCOVER_SYSTEM = """You find EVERY record of one kind inside a region of a contract. You get the blocks of the region (each with its \
tag) and the definition of the record.

Rules:
- List every record in document order. Leave none out and invent none. A record is exactly what the definition says; do not list \
headings, page numbers, running footers, signature blocks or text that merely mentions a record.
- A record can also be a short item INSIDE a longer sentence or list (a country in "the Territory means A, B and C", a party, a name): then every item is its own entry, "starts_with" is the item itself and "end_block" is the same block. Never list the whole sentence as one record.
- "key": the identity of the record as the document prints it (for example "(c)", "4.1(iii)", "Stage 4", "Part II", a country name).
- "start_block": the tag of the block where the record starts. "starts_with": its first 3 to 8 words, copied exactly as printed, \
including the marker ("(c)", "5.", "Stage 4"). Several records can sit in ONE block (a list written as one paragraph, the rows of a \
table): give each its own entry with the same start_block and its own starts_with.
- "end_block": the tag of the last block that still belongs to the record (it continues over a page break: include the next page's \
blocks, skip printed page numbers). A nested numbered sub-item belongs to its record unless the definition says it is a record.
- The first blocks of the region may repeat the end of the previous part: list a record that STARTS there only if it starts there.
- The definition is strict. Headings, clauses and numbered items that are not records of THIS kind are not listed, even if they look similar and \
even if they come right after the last record. After the last record that matches, list nothing more.
- If the region contains no record, return an empty list."""


def scope_prompt(task: TaskSpecification, outline: list[str]) -> ModelPrompt:
    return ModelPrompt(
        system=SCOPE_SYSTEM, schema_name="records_scope", json_schema=scope_schema(len(outline)),
        user="OUTLINE:\n" + "\n".join(outline) + "\n\n" + record_definition(task))


def discover_prompt(task: TaskSpecification, window: list[Tagged], max_records: int, after: str | None = None) -> ModelPrompt:
    blocks = "\n\n".join(t.render() for t in window)
    return ModelPrompt(
        system=DISCOVER_SYSTEM, schema_name="records_discover", json_schema=discover_schema([t.tag for t in window], max_records),
        user=f"BLOCKS:\n\n{blocks}\n\n{record_definition(task)}" + (
            f"\n\nALREADY LISTED: every record up to and including '{after}' (it starts in the first block shown or earlier). "
            "List only the records that come AFTER it, in order; if there are none, return an empty list." if after else ""))


def record_definition(task: TaskSpecification) -> str:
    r = task.record
    lines = [f"RECORDS: {task.description}"]
    if r.identity:
        lines.append(f"Identity: {r.identity}")
    if r.boundaries:
        lines.append(f"Boundaries: {r.boundaries}")
    return "\n".join(lines)
