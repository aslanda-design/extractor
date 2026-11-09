"""S07 step 8 - Verification: does each candidate really answer THE FIELD, or just something nearby?

Extraction asks the model to be generous ("list every value the passages state"). That finds
the right value, but also other dates, other parties and other periods. This step asks a
separate, much easier question per candidate: "does this quote state the value this field
asks for?". It is one extra call, with the field definition and the candidates side by side,
and it only ever REMOVES candidates; it never adds or changes a value.
If the call fails, nothing is removed (the failure is logged, never silently trusted).
"""

from dataclasses import dataclass

from ..adapters.base import ExtractorAdapter
from ..contracts.candidate import Candidate, ModelCall
from ..contracts.task import TaskSpecification
from .calling import call_model
from .prompts import verify_prompt


@dataclass
class Verdicts:
    rejected: dict[str, str]  # candidate id -> why the model says it is not the asked value
    call: ModelCall | None


def verify_candidates(
    task: TaskSpecification, candidates: list[Candidate], headings: dict[str, list[str]], extractor: ExtractorAdapter,
    retries: int, think: bool | None = None,
) -> Verdicts:
    """`headings`: passage id -> section path, so the judge sees where each quote sits."""
    if not candidates:
        return Verdicts({}, None)
    call = call_model(extractor, verify_prompt(task, candidates, headings), "verify candidates", retries, think)
    if call.reply.data is None:
        return Verdicts({}, call)
    rejected: dict[str, str] = {}
    known = {c.candidate_id for c in candidates}
    for item in call.reply.data.get("verdicts") or []:
        if isinstance(item, dict) and str(item.get("id")) in known and item.get("answers_field") is False:
            rejected[str(item["id"])] = str(item.get("reason") or "judged not to answer the field").strip()
    return Verdicts(rejected, call)
