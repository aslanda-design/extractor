"""S09 step 5 - Extract the fields of ONE record, from that record's own text only.

Each field is an ordinary value task (S07) or interpretation task (S08) whose evidence package is the record's span and nothing else, so
a value from another party, row or clause can never be assigned to this record. The value fields of one record read exactly the same
text, so they share ONE model call (the same grouped call S05 uses); label (interpretation) fields are decided one by one. Every field then
goes through S10/S11 like any other answer: the record keeps one result per field, each with its own status.
"""

from dataclasses import dataclass, field

from ..adapters.registry import AdapterSet
from ..config.runtime import RuntimeConfig
from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.common import SourceRef, TaskKind, TaskStatus
from ..contracts.coverage import CoverageLedger
from ..contracts.evidence import EvidencePackage, EvidencePassage
from ..contracts.passage import IndexedDocument, Passage
from ..contracts.record import FieldResult
from ..contracts.task import FieldSpec, TaskSpecification
from ..extraction.capacity import max_fields_per_call
from ..extraction.value_pipeline import ask_batch, conclude_task
from ..interpretation.interpretation_pipeline import interpret_task
from ..validation import Validated, validate_and_accept
from ..validation.coverage_check import Scorer


@dataclass
class FieldOutcome:
    result: FieldResult
    proposal: ValueProposal
    validated: Validated
    required: bool = True
    calls: list = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        ok = (TaskStatus.SUPPORTED, TaskStatus.NOT_FOUND) if not self.required else (TaskStatus.SUPPORTED,)
        return self.result.status in ok


RECORD_HINT = (" (The text you are given is ONE record: one item or one row of a list. Its fields are parts of THIS text: a marker such as (b) or 4.1, "
               "the first cell of a table row or the opening words can themselves be the value. The first line, [record key: ...], is the identity of the "
               "record as found in the document: use it for a field that asks for the record's identifier, number or letter, in the form the field "
               "describes. Never take any other value from outside the record.)")


def field_task(record_task: TaskSpecification, spec: FieldSpec) -> TaskSpecification:
    return TaskSpecification(id=spec.id, kind=spec.kind, description=spec.description + RECORD_HINT, output=spec.output, rules=spec.rules)


def extract_fields(
    record_task: TaskSpecification, record_id: str, passage: Passage, evidence: EvidencePassage, document: IndexedDocument,
    adapters: AdapterSet, config: RuntimeConfig, profile: str | None, scorer: Scorer | None, ledger: CoverageLedger | None = None,
) -> list[FieldOutcome]:
    extraction = config.extraction_for(profile).model_copy(update={"verify_candidates": config.records.verify_fields})
    interpretation = config.interpretation_for(profile)
    tasks = {spec.id: field_task(record_task, spec) for spec in record_task.record.fields}
    packages = {t.id: EvidencePackage(task_id=t.id, passages=[evidence]) for t in tasks.values()}
    only_this_record = document.model_copy(update={"passages": [passage]})  # quotes are located in the record's own text

    proposals: dict[str, ValueProposal] = {}
    values = [t for t in tasks.values() if t.kind == TaskKind.VALUE]
    per_call = max(1, max_fields_per_call(config.planning, adapters.extractor))
    for i in range(0, len(values), per_call):
        asked = ask_batch(values[i:i + per_call], packages, [only_this_record], adapters.extractor, extraction, config.planning, ledger=ledger)
        for task in values[i:i + per_call]:
            proposals[task.id] = conclude_task(asked[task.id], adapters.extractor, extraction)
    for task in tasks.values():
        if task.kind == TaskKind.INTERPRETATION:
            proposals[task.id] = interpret_task(task, packages[task.id], [only_this_record], adapters.interpretation_model, interpretation, ledger=ledger)

    outcomes = []
    for spec in record_task.record.fields:
        task = tasks[spec.id]
        proposal = proposals[task.id]
        validated = validate_and_accept(
            task, proposal, packages[task.id], [document], config.acceptance, scorer=scorer, query=spec.description,
            verifier=adapters.verifier, retries=extraction.retries)
        status = validated.decision.status
        refs: list[SourceRef] = [ref for c in proposal.chosen for ref in c.evidence] if proposal.outcome == ProposalOutcome.FOUND else []
        value = proposal.value if status == TaskStatus.SUPPORTED else None
        outcomes.append(FieldOutcome(
            result=FieldResult(field_id=spec.id, status=status, value=value, evidence=list(dict.fromkeys(refs))),
            proposal=proposal, validated=validated, required=spec.output.required, calls=[*proposal.calls, *validated.calls]))
    return outcomes
