"""S10 layer 6 - Semantic support (optional): does the evidence support THIS value for THIS field?

One call to `models.verification` (it may be a stronger model than the extractor) on the single
answer the engine is about to accept, with the full field definition and the passage it was read
from. The answer is a finding, never proof: "agreement from a second model call is supporting
information". A failed call is reported (info) and never silently counts as a pass.
"""

from ..adapters.base import VerifierAdapter
from ..contracts.candidate import ModelCall, ProposalOutcome, ValueProposal
from ..contracts.evidence import EvidencePackage
from ..contracts.generation import ModelPrompt
from ..contracts.task import TaskSpecification
from ..contracts.validation import Category, ValidationFinding, ValidationLayer
from ..extraction.calling import call_model
from ..extraction.prompts import task_section
from .findings import finding

SYSTEM = """You audit one extracted value from a contract. Decide whether the evidence supports it for the field described.

Answer supported=false when any of these is true:
- the evidence is about a different party, date, period, amount or document than the field asks for;
- the evidence does not actually state the value (it only mentions the topic, or states a rule or a formula);
- the evidence contains a condition, exception or amendment that changes the value;
- the value is not what the field definition asks for.
Answer supported=true only when the passage states the value for the right thing. Give one short reason."""

SCHEMA = {"type": "object", "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["supported", "reason"]}


def check_semantic(
    task: TaskSpecification, proposal: ValueProposal, package: EvidencePackage, verifier: VerifierAdapter,
    retries: int, think: bool | None,
) -> tuple[list[ValidationFinding], list[ModelCall]]:
    if proposal.outcome != ProposalOutcome.FOUND or not proposal.chosen:
        return [], []
    shown = {c.passage_id for c in proposal.chosen if c.passage_id}
    passages = [p for p in package.passages if p.passage_id in shown] or package.passages[:1]
    value = proposal.value
    evidence = "\n\n".join(f"[{p.passage_id.rsplit(':', 1)[-1]}] ({' > '.join(p.heading_path) or 'no section'})\n{p.text}" for p in passages)
    quotes = "\n".join(f"- {c.quote}" for c in proposal.chosen if c.quote)
    prompt = ModelPrompt(system=SYSTEM, schema_name="audit", json_schema=SCHEMA,
                         user=f"FIELD\n{task_section(task)}\n\nEXTRACTED VALUE: {value!r}\nQUOTE(S):\n{quotes}\n\nEVIDENCE\n{evidence}")
    call = call_model(verifier, prompt, "semantic support check", retries, think)
    if call.reply.data is None:
        return [finding(ValidationLayer.SEMANTIC_SUPPORT, Category.SEMANTIC_CHECK_FAILED, f"the semantic check could not run: {call.reply.error}", task.id)], [call]
    if call.reply.data.get("supported") is False:
        why = str(call.reply.data.get("reason") or "no reason given").strip()
        return [finding(ValidationLayer.SEMANTIC_SUPPORT, Category.SEMANTIC_UNSUPPORTED, f"the checking model says the evidence does not support the value: {why}", task.id)], [call]
    return [], [call]
