"""S09 step 2b - A second look at what discovery listed: does each entry match the record definition?

The first pass is generous (it lists everything that looks like a record, so none is missed); on a long document a small model also lists
look-alikes: a numbered heading when the records are lettered definitions, a clause from another section. This step shows the model the
SHORT list (key, page, first words) next to the definition and asks only for the entries that do not match. Like every second opinion in
the engine it is cheap, narrow and recorded: a rejected entry leaves the collection AND is reported as a finding, and if the call fails
nothing is rejected.
"""

from dataclasses import dataclass, field

from ..adapters.base import ExtractorAdapter
from ..config.runtime import RecordSettings
from ..contracts.candidate import ModelCall
from ..contracts.generation import ModelPrompt
from ..contracts.task import TaskSpecification
from ..extraction.calling import call_model
from .discovery import Found
from .outline import Tagged
from .prompts import record_definition

SYSTEM = """You check a list of entries that were found in a contract. Each entry was listed as a record of one kind. Say which entries \
are NOT records of that kind: a heading or clause of another section, an item of another list, a page number, text that only mentions a \
record. Judge each entry only from the definition and from what the entry starts with. An entry that fits the definition is NOT listed in \
your answer. When in doubt, do not list it. Give a short reason for each entry you list."""


@dataclass
class Rejection:
    found: Found
    reason: str


@dataclass
class Rejected:
    kept: list[Found]
    dropped: list[Rejection] = field(default_factory=list)
    call: ModelCall | None = None
    error: str = ""


def reject_non_records(
    task: TaskSpecification, found: list[Found], tagged: list[Tagged], model: ExtractorAdapter, settings: RecordSettings,
) -> Rejected:
    if len(found) < 2 or not settings.verify_discovery:
        return Rejected(kept=list(found))
    ids = [f"R{i}" for i in range(1, len(found) + 1)]
    lines = []
    for rid, f in zip(ids, found):
        block = tagged[f.start].block
        lines.append(f"[{rid}] key {f.key!r} (page {block.page}): {' '.join(block.text[f.offset:f.offset + 110].split())}")
    schema = {"type": "object", "properties": {"not_records": {"type": "array", "items": {"type": "string", "enum": ids}}}, "required": ["not_records"]}
    prompt = ModelPrompt(system=SYSTEM, schema_name="records_check", json_schema=schema,
                         user=f"{record_definition(task)}\n\nENTRIES:\n" + "\n".join(lines))
    call = call_model(model, prompt, "check the listed records", settings.retries, settings.think_discovery, stage="S09")
    if call.reply.data is None:
        return Rejected(kept=list(found), call=call, error=str(call.reply.error))
    wrong = {str(x): "not a record of this kind (judged from its first words)" for x in call.reply.data.get("not_records") or []}
    kept = [f for rid, f in zip(ids, found) if rid not in wrong]
    if not kept:  # rejecting everything is not a judgement, it is a broken answer: keep the list
        return Rejected(kept=list(found), call=call, error="the check rejected every entry; ignored")
    return Rejected(kept=kept, dropped=[Rejection(f, wrong[rid]) for rid, f in zip(ids, found) if rid in wrong], call=call)
